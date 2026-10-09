"""Ticket views — list, detail, create, actions.

All actions delegate to `tickets.services`. Views handle HTTP shaping
(HTMX detection, JSON responses, redirects, flash messages) and never
mutate ticket state directly.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core.sorting import apply_sort

import json
import time
from django.http import StreamingHttpResponse

from . import services
from .forms import (
    AssignTicketForm,
    AttachmentForm,
    AwaitingForm,
    CancelForm,
    CloseForm,
    CommentForm,
    DirectorSignoffForm,
    InternalNoteForm,
    ReopenForm,
    ResolveForm,
    StartWorkForm,
    TicketCreateForm,
)
from .models import (
    Ticket,
    TicketActivity,
    TicketAttachment,
    TicketCategory,
    TicketComment,
)

PER_PAGE = 25

SORTABLE_TICKETS = {
    "created":     "created_at",
    "updated":     "updated_at",
    "priority":    "priority",
    "state":       "state",
    "reference":   "reference",
    "subject":     "subject",
    "due":         "resolution_due_at",
}


# ---------------------------------------------------------------------------
# Visibility helpers
# ---------------------------------------------------------------------------
def _can_see_all(user):
    return user.is_superuser or user.has_perm("accounts.access_all_departments")


def _visible_tickets_qs(user):
    """Return the queryset of tickets this user is permitted to see.

    Scope:
      - superuser / access_all_departments → everything
      - any user → tickets they raised
      - any user → tickets assigned to them
      - department staff (director, deputy, office heads, admin-staff) →
        tickets handled by their department
      - otherwise → empty
    """
    base = Ticket.objects.select_related(
        "category", "raised_by", "assigned_to", "handling_department", "asset",
    )
    if _can_see_all(user):
        return base

    q = Q(raised_by=user) | Q(assigned_to=user)
    if user.department_id and user.role_id:
        if user.role.code in (
            "director", "deputy-director",
            "office-head-admin", "office-head-ts", "office-head-dev",
            "admin-staff",
        ):
            q |= Q(handling_department_id=user.department_id)
    return base.filter(q).distinct()


def _can_edit_ticket(user, ticket):
    return services._is_staff_on_ticket(ticket, user)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@login_required
def ticket_list(request):
    qs = _visible_tickets_qs(request.user)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(reference__icontains=q)
            | Q(subject__icontains=q)
            | Q(body__icontains=q)
            | Q(current_location__icontains=q)
        )

    state = request.GET.get("state")
    if state in dict(Ticket.STATE_CHOICES):
        qs = qs.filter(state=state)
    elif state == "open":
        qs = qs.filter(state__in=Ticket.OPEN_STATES)
    elif state == "closed":
        qs = qs.filter(state__in=Ticket.CLOSED_STATES)

    cat = request.GET.get("category")
    if cat:
        qs = qs.filter(category__code=cat)

    prio = request.GET.get("priority")
    if prio in dict(Ticket.PRIORITY_CHOICES):
        qs = qs.filter(priority=prio)

    scope = request.GET.get("scope")
    if scope == "mine":
        qs = qs.filter(raised_by=request.user)
    elif scope == "assigned":
        qs = qs.filter(assigned_to=request.user)

    qs, sort_key, sort_dir = apply_sort(
        qs, request.GET.get("sort"), request.GET.get("direction"),
        SORTABLE_TICKETS, default="created",
    )

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "tickets/list.html", {
        "page": page,
        "q": q,
        "state_filter": state or "open",
        "cat_filter": cat or "",
        "prio_filter": prio or "",
        "scope_filter": scope or "",
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "categories": TicketCategory.objects.filter(is_active=True).order_by("sort_order", "name"),
        "state_choices": Ticket.STATE_CHOICES,
        "priority_choices": Ticket.PRIORITY_CHOICES,
        "total": qs.count(),
    })


@login_required
def my_tickets(request):
    qs = (
        Ticket.objects
        .filter(raised_by=request.user)
        .select_related("category", "assigned_to", "handling_department")
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(subject__icontains=q))

    state = request.GET.get("state")
    if state in dict(Ticket.STATE_CHOICES):
        qs = qs.filter(state=state)
    elif state == "open":
        qs = qs.filter(state__in=Ticket.OPEN_STATES)
    elif state == "closed":
        qs = qs.filter(state__in=Ticket.CLOSED_STATES)

    qs, sort_key, sort_dir = apply_sort(
        qs, request.GET.get("sort"), request.GET.get("direction"),
        SORTABLE_TICKETS, default="created",
    )

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "tickets/my_tickets.html", {
        "page": page,
        "q": q,
        "state_filter": state or "all",
        "state_choices": Ticket.STATE_CHOICES,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "total": qs.count(),
    })


@login_required
def assigned_to_me(request):
    qs = (
        Ticket.objects
        .filter(assigned_to=request.user)
        .exclude(state=Ticket.CANCELLED)
        .select_related("category", "raised_by", "handling_department")
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(subject__icontains=q))

    state = request.GET.get("state")
    if state in dict(Ticket.STATE_CHOICES):
        qs = qs.filter(state=state)
    elif state == "open":
        qs = qs.filter(state__in=Ticket.OPEN_STATES)

    qs, sort_key, sort_dir = apply_sort(
        qs, request.GET.get("sort"), request.GET.get("direction"),
        SORTABLE_TICKETS, default="created",
    )

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "tickets/assigned_to_me.html", {
        "page": page,
        "q": q,
        "state_filter": state or "open",
        "state_choices": Ticket.STATE_CHOICES,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "total": qs.count(),
    })


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@login_required
def ticket_create(request, category_code=None):
    """Two modes:
       - no category_code → grid picker of active categories
       - with category_code → create form, scoped to that category

    Query-string pre-fills:
       ?asset_tag=XXX   → auto-fills the asset tag field
       ?subject=YYY     → pre-fills the subject
    """
    # Pre-fill values from query string (used by the asset → ticket flow).
    initial_asset_tag = (request.GET.get("asset_tag") or "").strip()
    initial_subject = (request.GET.get("subject") or "").strip()

    if not category_code:
        categories = (
            TicketCategory.objects
            .filter(is_active=True)
            .select_related("handling_department")
            .order_by("sort_order", "name")
        )
        return render(request, "tickets/create_pick.html", {
            "categories": categories,
            "initial_asset_tag": initial_asset_tag,
            "initial_subject": initial_subject,
        })

    category = get_object_or_404(
        TicketCategory.objects.select_related("handling_department"),
        code=category_code, is_active=True,
    )

    if request.method == "POST":
        form = TicketCreateForm(
            request.POST, request.FILES,
            category=category, user=request.user,
        )
        if form.is_valid():
            asset = None
            tag = (form.cleaned_data.get("asset_tag") or "").strip()
            if tag:
                from assets.models import Asset
                asset = Asset.objects.filter(asset_tag__iexact=tag).first()

            try:
                ticket = services.create_ticket(
                    raised_by=request.user,
                    category=category,
                    subject=form.cleaned_data["subject"],
                    body=form.cleaned_data["body"],
                    priority=form.cleaned_data["priority"],
                    current_location=form.cleaned_data.get("current_location") or "",
                    asset=asset,
                    attachment=form.cleaned_data.get("attachment"),
                )
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(request, f"Ticket {ticket.reference} raised.")
                return redirect("tickets:detail", pk=ticket.pk)
    else:
        form = TicketCreateForm(
            category=category,
            user=request.user,
            initial_asset_tag=initial_asset_tag,
            initial_subject=initial_subject,
        )

    return render(request, "tickets/create.html", {
        "form": form,
        "category": category,
    })

# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------
@login_required
def ticket_detail(request, pk):
    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)

    # Prefetch relations for the page
    activities = ticket.activities.select_related("actor").order_by("created_at")
    comments = ticket.comments.select_related("author").order_by("created_at")
    attachments = ticket.attachments.select_related("uploaded_by").order_by("-created_at")

    # What can this user do?
    user = request.user
    is_raiser = ticket.raised_by_id == user.pk
    is_assignee = ticket.assigned_to_id == user.pk

    can_assign = (
        ticket.is_open
        and services._can_assign(ticket, user)
    )
    can_start = (
        is_assignee
        and ticket.state in (Ticket.ASSIGNED, Ticket.REOPENED, Ticket.AWAITING_RESPONSE)
    )
    can_await = (
        is_assignee
        and ticket.state == Ticket.IN_PROGRESS
    )
    can_resolve = (
        ticket.state in (Ticket.IN_PROGRESS, Ticket.AWAITING_RESPONSE,
                         Ticket.ASSIGNED, Ticket.REOPENED)
        and services._can_resolve(ticket, user)
    )
    can_close = (
        is_raiser
        and ticket.state in (Ticket.RESOLVED, Ticket.IN_PROGRESS)
    )
    can_reopen = (
        is_raiser
        and ticket.state in (Ticket.RESOLVED, Ticket.CLOSED)
    )
    can_cancel = (
        is_raiser
        and ticket.state in (Ticket.NEW, Ticket.ASSIGNED, Ticket.AWAITING_RESPONSE)
    )
    can_comment = not ticket.is_closed or is_raiser or user.is_superuser
    can_internal = (
        not is_raiser and services._is_staff_on_ticket(ticket, user)
    ) or user.is_superuser
    can_attach = not ticket.is_closed or is_raiser or user.is_superuser

    return render(request, "tickets/detail.html", {
        "ticket": ticket,
        "activities": activities,
        "comments": comments,
        "attachments": attachments,
        "is_raiser": is_raiser,
        "is_assignee": is_assignee,
        "can_assign": can_assign,
        "can_start": can_start,
        "can_await": can_await,
        "can_resolve": can_resolve,
        "can_close": can_close,
        "can_reopen": can_reopen,
        "can_cancel": can_cancel,
        "can_comment": can_comment,
        "can_internal": can_internal,
        "can_attach": can_attach,
        # forms
        "assign_form": AssignTicketForm(department=ticket.handling_department),
        "start_form": StartWorkForm(),
        "await_form": AwaitingForm(),
        "resolve_form": ResolveForm(),
        "close_form": CloseForm(),
        "reopen_form": ReopenForm(),
        "cancel_form": CancelForm(),
        "comment_form": CommentForm(),
        "internal_form": InternalNoteForm(),
        "attach_form": AttachmentForm(),
        "timeline_last_pk": (
            TicketActivity.objects
            .filter(ticket=ticket)
            .order_by("-pk")
            .values_list("pk", flat=True)
            .first() or 0
        ),
        "director_form": DirectorSignoffForm(),
        "can_signoff": (
            ticket.state == Ticket.AWAITING_DIRECTOR
            and services._is_department_head(request.user, ticket.handling_department_id)
        ),
        "can_reassign": (
            ticket.is_open and services._can_assign(ticket, request.user)
        ),
        "allow_raiser_reassign": getattr(ticket.category, "allow_raiser_reassign", True),
        "requires_director_signoff": getattr(ticket.category, "requires_director_signoff", False),
    })


# ---------------------------------------------------------------------------
# Action helper — shared by every POST endpoint below
# ---------------------------------------------------------------------------
def _action(request, pk, form_class, service_fn, success, **form_kwargs):
    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)
    form = form_class(request.POST, request.FILES, **form_kwargs)

    if not form.is_valid():
        errs = []
        for field, errs_list in form.errors.items():
            for e in errs_list:
                errs.append(f"{field}: {e}")
        messages.error(request, "; ".join(errs) or "Please check the form.")
        return redirect("tickets:detail", pk=ticket.pk)

    try:
        service_fn(ticket, request.user, **form.cleaned_data)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, success)
    return redirect("tickets:detail", pk=ticket.pk)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
@login_required
@require_POST
def ticket_assign(request, pk):
    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)
    form = AssignTicketForm(
        request.POST, department=ticket.handling_department,
    )
    if not form.is_valid():
        messages.error(request, "Please pick an assignee.")
        return redirect("tickets:detail", pk=ticket.pk)
    try:
        services.assign_ticket(
            ticket, request.user,
            assignee=form.cleaned_data["assignee"],
            reason=form.cleaned_data.get("reason") or "",
        )
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Assigned.")
    return redirect("tickets:detail", pk=ticket.pk)


@login_required
@require_POST
def ticket_start(request, pk):
    return _action(
        request, pk, StartWorkForm,
        lambda t, u, note="": services.start_work(t, u, note=note),
        "Work started.",
    )


@login_required
@require_POST
def ticket_awaiting(request, pk):
    return _action(
        request, pk, AwaitingForm,
        lambda t, u, reason="": services.mark_awaiting_response(t, u, reason=reason),
        "Marked as awaiting response.",
    )


@login_required
@require_POST
def ticket_resolve(request, pk):
    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)
    form = ResolveForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(
            f"{k}: {' '.join(v)}" for k, v in form.errors.items()
        ) or "Please check the form.")
        return redirect("tickets:detail", pk=ticket.pk)

    try:
        services.resolve_ticket(
            ticket, request.user,
            resolution_notes=form.cleaned_data["resolution_notes"],
            collection_location=form.cleaned_data.get("collection_location") or "",
            collection_timeframe=form.cleaned_data.get("collection_timeframe") or "",
        )
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(
            request,
            "Resolution submitted for director sign-off."
            if ticket.state == Ticket.AWAITING_DIRECTOR
            else "Ticket resolved. The raiser has been notified.",
        )
    return redirect("tickets:detail", pk=ticket.pk)

@login_required
@require_POST
def ticket_director_signoff(request, pk):
    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)
    form = DirectorSignoffForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please check the form.")
        return redirect("tickets:detail", pk=ticket.pk)

    try:
        services.director_sign_off(
            ticket, request.user,
            notes=form.cleaned_data.get("notes") or "",
        )
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Sign-off recorded. Raiser notified.")
    return redirect("tickets:detail", pk=ticket.pk)


@login_required
@require_POST
def ticket_close(request, pk):
    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)
    form = CloseForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please check the form.")
        return redirect("tickets:detail", pk=ticket.pk)
    try:
        services.close_ticket(
            ticket, request.user,
            rating=form.cleaned_data.get("rating") or None,
            comment=form.cleaned_data.get("comment") or "",
        )
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Ticket closed.")
    return redirect("tickets:detail", pk=ticket.pk)


@login_required
@require_POST
def ticket_reopen(request, pk):
    return _action(
        request, pk, ReopenForm,
        lambda t, u, reason="": services.reopen_ticket(t, u, reason),
        "Ticket reopened.",
    )


@login_required
@require_POST
def ticket_cancel(request, pk):
    return _action(
        request, pk, CancelForm,
        lambda t, u, reason="": services.cancel_ticket(t, u, reason),
        "Ticket cancelled.",
    )


@login_required
@require_POST
def ticket_comment(request, pk):
    return _action(
        request, pk, CommentForm,
        lambda t, u, body="": services.add_public_comment(t, u, body),
        "Comment posted.",
    )


@login_required
@require_POST
def ticket_internal_note(request, pk):
    return _action(
        request, pk, InternalNoteForm,
        lambda t, u, body="": services.add_internal_comment(t, u, body),
        "Internal note added.",
    )


@login_required
@require_POST
def ticket_attach(request, pk):
    return _action(
        request, pk, AttachmentForm,
        lambda t, u, file=None, description="": services.add_attachment(
            t, u, file, description=description,
        ),
        "Attachment added.",
    )


@login_required
@require_POST
def ticket_delete_comment(request, pk, comment_id):
    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)
    comment = get_object_or_404(TicketComment, pk=comment_id, ticket=ticket)
    try:
        services.delete_comment(ticket, request.user, comment)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Comment deleted.")
    return redirect("tickets:detail", pk=ticket.pk)


@login_required
@require_POST
def ticket_delete_attachment(request, pk, attachment_id):
    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)
    attachment = get_object_or_404(TicketAttachment, pk=attachment_id, ticket=ticket)
    try:
        services.delete_attachment(ticket, request.user, attachment)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, "Attachment deleted.")
    return redirect("tickets:detail", pk=ticket.pk)


# ---------------------------------------------------------------------------
# Timeline partial (for future SSE-driven live updates)
# ---------------------------------------------------------------------------
@login_required
def ticket_timeline_partial(request, pk):
    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)
    activities = ticket.activities.select_related("actor").order_by("created_at")
    return render(request, "tickets/_timeline.html", {
        "ticket": ticket,
        "activities": activities,
    })
    
    
TICKET_POLL_INTERVAL = 5


@login_required
@require_GET
def ticket_stream(request, pk):
    """SSE stream for a single ticket's activity timeline.

    Same contract as the workflow version: client passes ?since=<max
    activity pk it already has>, server streams only newer rows.
    """
    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)
    try:
        since = int(request.GET.get("since") or 0)
    except (TypeError, ValueError):
        since = 0

    def event_stream():
        last = since
        yield ": connected\n\n"
        try:
            while True:
                time.sleep(TICKET_POLL_INTERVAL)
                new_pks = list(
                    TicketActivity.objects
                    .filter(ticket_id=ticket.pk, pk__gt=last)
                    .order_by("pk")
                    .values_list("pk", flat=True)[:50]
                )
                if new_pks:
                    last = new_pks[-1]
                    payload = json.dumps({"last_pk": last, "count": len(new_pks)})
                    yield f"event: activity\ndata: {payload}\n\n"
                else:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            return

    resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp    
    
    