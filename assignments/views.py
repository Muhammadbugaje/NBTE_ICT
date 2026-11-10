"""Phase P pages (spec G1.g): My work, grants log, "who can see X and why".

    /qa/my-work/                              every open assignment of mine (P.6)
    /qa/settings/access/grants/               grants log, filter + revoke (P.5)
    /qa/settings/access/who/                  pick an institution (P.5)
    /qa/settings/access/who/<code>/           who can see it and why (P.5)
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from qa_core.ui import paginate, render_qa, toast

from . import access, grants, work
from .models import AccessGrant, WorkAssignment


def _staff(request):
    if getattr(request.user, "user_type", "staff") != "staff":
        raise Http404


def can_view_access_pages(user):
    """Super admin, ES, Director of ICT (full viewers) or an explicit permission."""
    return bool(access.full_viewer_everywhere(user) or user.has_perm("assignments.view_access_report"))


def can_view_grants_log(user):
    return bool(access.full_viewer_everywhere(user) or user.has_perm("assignments.view_grants_log"))


# ---------------------------------------------------------------------------
# My work
# ---------------------------------------------------------------------------
@login_required
def my_work(request):
    _staff(request)
    show = request.GET.get("show", "open")
    qs = WorkAssignment.objects.filter(assignee=request.user).select_related("institution", "department",
                                                                               "subject_ct", "assigned_by")
    qs = qs.filter(status__in=WorkAssignment.OPEN) if show != "closed" else qs.exclude(
        status__in=WorkAssignment.OPEN)
    rows = list(qs.order_by("due_at", "-assigned_at")[:300])
    from . import inbox

    groups = {}
    for wa in rows:
        try:   # v2.3: the button opens the task itself, not the institution
            wa.task_link, wa.task_verb = inbox.task_url(wa)
        except Exception:
            wa.task_link, wa.task_verb = "", "Open"
        groups.setdefault(wa.get_kind_display(), []).append(wa)
    now = timezone.now()
    live = list(AccessGrant.objects.live(request.user).select_related("institution", "object_ct")
                .order_by("expires_at")[:50])
    ctx = {
        "groups": sorted(groups.items()), "show": show, "now": now, "grants": live,
        "counts": {"open": WorkAssignment.objects.filter(assignee=request.user, status__in=WorkAssignment.OPEN).count(),
                   "due": sum(1 for wa in rows if wa.due_at and wa.due_at < now and wa.is_open)},
        "mode": access.privacy_mode(),
    }
    if show != "closed":
        ctx.update(inbox.context(request.user))
        ctx["next_up"] = ctx["work_rows"][0] if ctx["work_rows"] else None
        ctx["offered_count"] = sum(1 for wa in rows if wa.status in ("offered", "suggested"))
    return render_qa(request, "assignments/my_work.html", ctx, {"qa-panel": "assignments/_my_work_panel.html"})


@login_required
@require_POST
def work_action(request, pk, action):
    _staff(request)
    wa = get_object_or_404(WorkAssignment, pk=pk, assignee=request.user)
    try:
        if action == "accept":
            work.accept(wa, request.user)
            msg = "Accepted. You can now open the work."
        elif action == "decline":
            work.decline(wa, request.user, request.POST.get("reason", ""))
            msg = "Declined. Your director has been told."
        elif action == "start":
            work.start(wa, request.user)
            msg = "Started."
        elif action == "return":  # v2.1 Part 2.2: an SSQ reviewer hands the work back, with a reason
            from accreditation import ssq_assign
            reason = request.headers.get("HX-Prompt") or request.POST.get("reason", "")
            ssq_assign.return_work(wa, request.user, reason)
            msg = "Returned. Your director has been told."
        else:
            raise Http404
    except (ValidationError, PermissionDenied) as exc:
        response = my_work(request)
        toast(response, "error", "Not done", "; ".join(getattr(exc, "messages", [str(exc)])))
        return response
    response = my_work(request)
    toast(response, "success", "Saved", msg)
    return response


# ---------------------------------------------------------------------------
# Grants log
# ---------------------------------------------------------------------------
@login_required
def grants_log(request):
    _staff(request)
    if not can_view_grants_log(request.user):
        raise PermissionDenied
    f = request.GET
    qs = AccessGrant.objects.select_related("grantee", "institution", "granted_by", "object_ct", "department")
    state = f.get("state", "live")
    now = timezone.now()
    if state == "live":
        qs = qs.live(now=now)
    elif state == "ended":
        qs = qs.filter(Q(revoked_at__isnull=False) | Q(expires_at__lte=now))
    if f.get("scope"):
        qs = qs.filter(scope=f["scope"])
    q = (f.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(grantee__email__icontains=q) | Q(grantee__first_name__icontains=q)
                       | Q(grantee__last_name__icontains=q) | Q(institution__name__icontains=q)
                       | Q(institution__code__iexact=q) | Q(reason__icontains=q))
    ctx = {"page": paginate(request, qs.order_by("-granted_at")), "state": state, "scope": f.get("scope", ""),
           "q": q, "scopes": AccessGrant.SCOPE_CHOICES,
           "can_revoke": request.user.is_superuser or request.user.has_perm("assignments.revoke_access_grant"),
           "counts": {"live": AccessGrant.objects.live(now=now).count(),
                      "long": AccessGrant.objects.live(now=now).filter(
                          granted_at__lte=now - timezone.timedelta(days=grants.long_grant_days())).count()}}
    return render_qa(request, "assignments/grants.html", ctx, {"qa-panel": "assignments/_grants_panel.html",
                                                               "qa-rows": "assignments/_grant_rows.html"})


@login_required
@require_POST
def grant_revoke(request, pk):
    _staff(request)
    if not (request.user.is_superuser or request.user.has_perm("assignments.revoke_access_grant")):
        raise PermissionDenied
    g = get_object_or_404(AccessGrant, pk=pk)
    reason = (request.POST.get("reason") or "").strip()
    if len(reason) < 3:
        response = grants_log(request)
        toast(response, "error", "Give a reason", "Say why the access is revoked.")
        return response
    grants.revoke(g, by=request.user, reason=AccessGrant.R_MANUAL)
    from core import audit
    audit.log("grant_revoked", g, actor=request.user, note=reason[:255])
    response = grants_log(request)
    toast(response, "success", "Revoked", "The access ended now.")
    return response


# ---------------------------------------------------------------------------
# Who can see institution X and why
# ---------------------------------------------------------------------------
@login_required
def who_index(request):
    _staff(request)
    if not can_view_access_pages(request.user):
        raise PermissionDenied
    from institutions.models import Institution
    q = (request.GET.get("q") or "").strip()
    if q.startswith("code:"):
        return redirect("assignments:who", code=q[5:].strip().upper())
    qs = Institution.objects.for_user(request.user).order_by("name")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__iexact=q))
    ctx = {"page": paginate(request, qs), "q": q, "mode": access.privacy_mode()}
    return render_qa(request, "assignments/who_index.html", ctx, {"qa-panel": "assignments/_who_index_panel.html"})


@login_required
def who(request, code):
    _staff(request)
    if not can_view_access_pages(request.user):
        raise PermissionDenied
    from institutions.models import Institution
    inst = get_object_or_404(Institution.objects.for_user(request.user), code=code)
    mode = request.GET.get("mode") or access.privacy_mode()
    if mode not in (access.MODE_ASSIGNMENT, access.MODE_DEPARTMENT):
        mode = access.privacy_mode()
    with access.mode_override(mode):
        rows = access.who_can_see(inst, mode=mode)
    order = ["full", "department", "own", "summary", "institution"]
    rows.sort(key=lambda r: (order.index(r["level"]), (r["user"].get_full_name() or r["user"].email).lower()))
    counts = {lvl: sum(1 for r in rows if r["level"] == lvl) for lvl in order}
    access.record_event(request.user, inst, "view_full_journey", request=request, institution=inst)
    ctx = {"inst": inst, "rows": rows, "counts": counts, "mode": mode, "current_mode": access.privacy_mode(),
           "assignments": list(WorkAssignment.objects.filter(institution=inst).select_related(
               "assignee", "department").order_by("-assigned_at")[:50])}
    return render_qa(request, "assignments/who.html", ctx, {"qa-panel": "assignments/_who_panel.html"})
