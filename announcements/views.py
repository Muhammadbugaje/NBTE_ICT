"""Announcements views (Phase 9). Thin: rules live in services/permissions."""
import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .forms import AnnouncementForm
from .models import Announcement, AnnouncementAck, AnnouncementPoll, PollOption, PollVote
from .permissions import can_edit, can_post, can_view_report

PAGE_SIZE = 15
FILTERS = ("all", "unread", "ack", "sent", "drafts")


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _staff_only(user):
    if getattr(user, "user_type", None) != "staff":
        raise PermissionDenied("Announcements are for NBTE staff.")


def _with_related(qs):
    return qs.select_related("created_by", "department", "poll").prefetch_related(
        "roles",
        "attachments",
        Prefetch("poll__options", queryset=PollOption.objects.annotate(n=Count("votes")).order_by("position", "id")),
    )


def _decorate(items, user):
    """Attach the viewer's poll state and results to each announcement."""
    items = list(items)
    polls = [a.poll for a in items if _poll(a)]
    mine, voters = {}, {}
    if polls:
        for poll_id, option_id in PollVote.objects.filter(
            user=user, poll__in=polls,
        ).values_list("poll_id", "option_id"):
            mine.setdefault(poll_id, set()).add(option_id)
        voters = dict(
            PollVote.objects.filter(poll__in=polls).values("poll_id")
            .annotate(n=Count("user_id", distinct=True)).values_list("poll_id", "n")
        )
    for a in items:
        a.can_edit = can_edit(user, a)
        a.can_report = can_view_report(user, a)
        poll = _poll(a)
        if poll:
            _attach_poll_state(poll, mine.get(poll.pk, set()), voters.get(poll.pk, 0))
            # Posters / report viewers outside the audience see results straight away.
            if not getattr(a, "in_audience", True):
                poll.show_results = True
    return items


def _poll(a):
    try:
        return a.poll
    except AnnouncementPoll.DoesNotExist:
        return None


def _attach_poll_state(poll, my_ids, voters):
    poll.my_ids = set(my_ids)
    poll.has_voted = bool(poll.my_ids)
    poll.voters = voters
    poll.show_results = poll.has_voted or not poll.is_open
    rows = []
    for opt in poll.options.all():
        n = getattr(opt, "n", None)
        if n is None:
            n = opt.votes.count()
        rows.append({
            "option": opt, "votes": n, "chosen": opt.pk in poll.my_ids,
            "percent": round(100 * n / voters) if voters else 0,
        })
    poll.rows = rows


def _get_visible(request, pk, *, include_expired=True):
    """The announcement if the viewer may read it (audience) or manage it."""
    _staff_only(request.user)
    ann = get_object_or_404(_with_related(Announcement.objects.all()), pk=pk)
    may_open, visible = services.access_for(request.user, ann, include_expired=include_expired)
    if not may_open:
        raise Http404
    ann.in_audience = visible
    return ann


def _annotated(request, ann):
    """Re-read one announcement with the viewer's read/ack flags."""
    obj = _with_related(Announcement.objects.with_user_state(request.user)).get(pk=ann.pk)
    obj.in_audience = getattr(ann, "in_audience", True)
    obj.my_ack_at = None
    if obj.is_acked:
        obj.my_ack_at = (AnnouncementAck.objects.filter(announcement=obj, user=request.user)
                         .values_list("acknowledged_at", flat=True).first())
    return _decorate([obj], request.user)[0]


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@login_required
def announcement_list(request):
    user = request.user
    _staff_only(user)
    poster = can_post(user)
    current = request.GET.get("filter", "all")
    if current not in FILTERS or (current in ("drafts", "sent") and not poster):
        current = "all"

    if current == "drafts":
        qs = Announcement.objects.drafts()
        if not user.has_perm("announcements.change_announcement"):
            qs = qs.filter(created_by=user)
        qs = qs.with_user_state(user).order_by("-updated_at")
    elif current == "sent":
        qs = (Announcement.objects.published().filter(created_by=user)
              .with_user_state(user).order_by("-published_at", "-pk"))
    else:
        qs = Announcement.objects.visible_to(user).with_user_state(user)
        if current == "unread":
            qs = qs.filter(is_read=False)
        elif current == "ack":
            qs = qs.filter(requires_ack=True, is_acked=False)
        qs = qs.order_by("-pinned", "-published_at", "-pk")

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q))

    page = Paginator(_with_related(qs), PAGE_SIZE).get_page(request.GET.get("page"))
    items = list(page.object_list)
    in_audience = set(Announcement.objects.visible_to(user).filter(
        pk__in=[a.pk for a in items]).values_list("pk", flat=True))
    for a in items:
        a.in_audience = a.pk in in_audience
    items = _decorate(items, user)

    visible = Announcement.objects.visible_to(user)
    ctx = {
        "page": page,
        "items": items,
        "filter": current,
        "q": q,
        "can_post": poster,
        "counts": {
            "unread": Announcement.objects.unread_for(user).count(),
            "ack": Announcement.objects.needing_ack_from(user).count(),
            "all": visible.count(),
            "drafts": (Announcement.objects.drafts().filter(created_by=user).count() if poster else 0),
            "sent": (Announcement.objects.published().filter(created_by=user).count() if poster else 0),
        },
    }
    return render(request, "announcements/list.html", ctx)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------
@login_required
def announcement_detail(request, pk):
    ann = _get_visible(request, pk)
    if ann.in_audience:
        services.mark_read(request.user, ann)
    ann = _annotated(request, ann)
    return render(request, "announcements/detail.html", {"a": ann})


# ---------------------------------------------------------------------------
# Acknowledge / vote (HTMX, with plain-POST fallback)
# ---------------------------------------------------------------------------
@login_required
@require_POST
def acknowledge(request, pk):
    ann = _get_visible(request, pk, include_expired=False)
    try:
        services.acknowledge(request.user, ann)
    except ValidationError as exc:
        return HttpResponse(" ".join(exc.messages), status=400)
    if _is_htmx(request):
        tpl = "announcements/_ack_block.html" if request.POST.get("view") == "detail" else "announcements/_card.html"
        return render(request, tpl, {"a": _annotated(request, ann)})
    messages.success(request, "Thank you — your acknowledgement has been recorded.")
    return redirect(request.POST.get("next") or ann.get_absolute_url())


@login_required
@require_POST
def vote(request, pk):
    ann = _get_visible(request, pk, include_expired=False)
    poll = _poll(ann)
    if poll is None:
        raise Http404
    try:
        services.vote(request.user, poll, request.POST.getlist("option"))
        error = ""
    except ValidationError as exc:
        error = " ".join(exc.messages)
    if _is_htmx(request):
        a = _annotated(request, ann)
        return render(request, "announcements/_poll.html", {"a": a, "poll": a.poll, "poll_error": error})
    if error:
        messages.error(request, error)
    return redirect(request.POST.get("next") or ann.get_absolute_url())


# ---------------------------------------------------------------------------
# Create / edit
# ---------------------------------------------------------------------------
def _save_from_form(request, form, ann, *, creating):
    publish_now = request.POST.get("action") == "publish"
    ann, sent = services.save_from_form(
        form, request.user, creating=creating, publish_now=publish_now,
        remove_attachment_ids=request.POST.getlist("remove_attachments"),
    )
    if sent is not None:
        messages.success(request, f"Published. {sent} staff member{'s' if sent != 1 else ''} notified.")
    elif ann.is_draft:
        messages.success(request, "Draft saved. Only you can see it until you publish.")
    else:
        messages.success(request, "Changes saved.")
    return ann


@login_required
def announcement_create(request):
    if not can_post(request.user):
        raise PermissionDenied("You don't have permission to post announcements.")
    form = AnnouncementForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        ann = _save_from_form(request, form, form.instance, creating=True)
        return redirect(ann.get_absolute_url())
    return render(request, "announcements/form.html", {"form": form, "mode": "create"})


@login_required
def announcement_edit(request, pk):
    ann = get_object_or_404(Announcement, pk=pk)
    if not can_edit(request.user, ann):
        raise PermissionDenied("You cannot edit this announcement.")
    poll = ann.poll_or_none()
    locked = bool(poll and poll.votes.exists())
    form = AnnouncementForm(request.POST or None, request.FILES or None, instance=ann, poll_locked=locked)
    if request.method == "POST" and form.is_valid():
        ann = _save_from_form(request, form, ann, creating=False)
        return redirect(ann.get_absolute_url())
    return render(request, "announcements/form.html", {
        "form": form, "mode": "edit", "a": ann, "poll_locked": locked,
    })


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def _report_target(request, pk):
    _staff_only(request.user)
    ann = get_object_or_404(_with_related(Announcement.objects.all()), pk=pk)
    if not can_view_report(request.user, ann):
        raise PermissionDenied("You cannot see this report.")
    return ann


@login_required
def announcement_report(request, pk):
    ann = _report_target(request, pk)
    data = services.report(ann)
    if request.GET.get("format") == "csv":
        return _report_csv(ann, data)
    return render(request, "announcements/report.html", {
        "a": ann, "r": data, "can_edit": can_edit(request.user, ann),
    })


def _report_csv(ann, data):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    resp["Content-Disposition"] = f'attachment; filename="announcement-{ann.pk}-report-{stamp}.csv"'
    resp.write("﻿")  # Excel opens UTF-8 correctly with a BOM
    w = csv.writer(resp)
    w.writerow(["Name", "Email", "Department", "Role", "Read", "Acknowledged"])
    for u in data["audience"]:
        w.writerow([
            _safe(u.get_full_name() or ""), _safe(u.email),
            _safe(u.department.name if u.department_id else ""),
            _safe(u.role.name if u.role_id else ""),
            "yes" if u.has_read else "no", "yes" if u.has_acked else "no",
        ])
    return resp


def _safe(value):
    """Stop spreadsheet formula injection in CSV cells."""
    value = str(value or "")
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


@login_required
@require_POST
def remind(request, pk):
    ann = _report_target(request, pk)
    try:
        sent = services.remind_pending(ann, actor=request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("announcements:report", pk=ann.pk)
    if sent:
        messages.success(request, f"Reminder sent to {sent} staff member{'s' if sent != 1 else ''}.")
    else:
        messages.info(request, "Everyone has already responded — no reminders needed.")
    return redirect("announcements:report", pk=ann.pk)
