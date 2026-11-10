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
from qa_core.ui import htmx_fragment  # Phase 0 (N2): fragment URLs
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
    from .access import can_view_all_tickets
    return can_view_all_tickets(user)


def _visible_tickets_qs(user):
    """Tickets this user may see. The rule lives in tickets/access.py."""
    from .access import visible_tickets_queryset
    return visible_tickets_queryset(user)


def ticket_list_base(user, scope):
    """Phase 9: base queryset of each ticket list tab (website + mobile API).
    scope: my (raised by me) / assigned (assigned to me) / all (everything
    this user may see)."""
    if scope == "my":
        return (
            Ticket.objects
            .filter(raised_by=user)
            .select_related("category", "assigned_to", "handling_department")
        )
    if scope == "assigned":
        return (
            Ticket.objects
            .filter(assigned_to=user)
            .exclude(state=Ticket.CANCELLED)
            .select_related("category", "raised_by", "handling_department")
        )
    return _visible_tickets_qs(user)


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
        "can_see_counts": _can_see_counts(request.user),
    })


def _can_see_counts(user):
    from .routing import counts_scope
    return counts_scope(user)[0] is not None


@login_required
def my_tickets(request):
    qs = ticket_list_base(request.user, "my")

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
    qs = ticket_list_base(request.user, "assigned")

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

    # What can this user do? (Phase 9: rules in tickets.services.available_actions)
    user = request.user
    flags = services.available_actions(ticket, user)
    is_raiser, is_assignee = flags["is_raiser"], flags["is_assignee"]
    can_assign, can_start, can_await = flags["can_assign"], flags["can_start"], flags["can_await"]
    can_resolve, can_close, can_reopen = flags["can_resolve"], flags["can_close"], flags["can_reopen"]
    can_cancel, can_comment = flags["can_cancel"], flags["can_comment"]
    can_internal, can_attach = flags["can_internal"], flags["can_attach"]

    asset_link_ok = False
    asset_grant = None
    asset_panel = None
    if ticket.asset_id:
        from assets.access import can_view_asset
        asset_link_ok = can_view_asset(user, ticket.asset)
        if asset_link_ok:
            from assignments.access import grant_for
            asset_grant = grant_for(user, ticket.asset, "asset")
            a = ticket.asset
            asset_panel = {
                "asset": a,
                "holders": list(a.open_assignments()),
                "past_tickets": list(a.tickets.exclude(pk=ticket.pk).select_related("assigned_to")
                                     .order_by("-created_at")[:5]),
                "issues": list(a.issues.order_by("-reported_at")[:5]),
            }

    from .bridge import path_events
    from .routing import load_label, routable_divisions
    divisions = []
    if flags["can_route"]:
        divisions = list(routable_divisions(ticket))
        for d in divisions:
            d.load_label = load_label(d)
    assign_division = (ticket.division if ticket.division_id and ticket.division_state == Ticket.DIV_ACCEPTED
                       and services.is_division_head_of(user, ticket)
                       and not services._is_department_head(user, ticket.handling_department_id) else None)

    return render(request, "tickets/detail.html", {
        "ticket": ticket,
        "asset_link_ok": asset_link_ok,
        "asset_grant": asset_grant,
        "asset_panel": asset_panel,
        "path": path_events(ticket),
        "divisions": divisions,
        "flags": flags,
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
        "assign_form": AssignTicketForm(department=ticket.handling_department, division=assign_division),
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
        "can_signoff": flags["can_signoff"],
        "can_reassign": flags["can_reassign"],
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
@htmx_fragment("tickets:detail")
def ticket_timeline_partial(request, pk):
    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)
    activities = ticket.activities.select_related("actor").order_by("created_at")
    can_internal = (
        ticket.raised_by_id != request.user.pk
        and services._is_staff_on_ticket(ticket, request.user)
    ) or request.user.is_superuser
    return render(request, "tickets/_timeline.html", {
        "ticket": ticket,
        "activities": activities,
        "can_internal": can_internal,
    })
    
    
TICKET_POLL_INTERVAL = 5


@login_required
@require_GET
def ticket_stream(request, pk):
    """Timeline poll for one ticket (was an endless SSE stream - review H2)."""
    from core.polling import activity_response

    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)
    return activity_response(request, TicketActivity.objects.filter(ticket_id=ticket.pk))




# ===========================================================================
# v2.1 Phase C: routing (I2), funds (I8), rating (I4), report (I13), counts (I3, I10)
# ===========================================================================
def _post_service(request, pk, fn, success):
    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)
    try:
        fn(ticket)
    except (PermissionDenied, ValidationError) as exc:
        msgs = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, "; ".join(msgs))
    else:
        messages.success(request, success)
    return redirect("tickets:detail", pk=ticket.pk)


@login_required
@require_POST
def ticket_route(request, pk):
    from org.models import Division
    from .routing import route_to_division
    division = Division.objects.filter(pk=request.POST.get("division") or 0).first()
    return _post_service(request, pk, lambda t: route_to_division(t, request.user, division,
                                                                  request.POST.get("note", "")),
                         "Routed to the division. The head has been asked to accept it.")


@login_required
@require_POST
def ticket_division_accept(request, pk):
    from .routing import accept_division
    return _post_service(request, pk, lambda t: accept_division(t, request.user),
                         "Accepted. The division's members can now see this ticket.")


@login_required
@require_POST
def ticket_division_decline(request, pk):
    from .routing import decline_division
    return _post_service(request, pk, lambda t: decline_division(t, request.user, request.POST.get("reason", "")),
                         "Declined. The ticket is back with the director.")


@login_required
@require_POST
def ticket_request_funds(request, pk):
    from .bridge import request_funds
    return _post_service(request, pk, lambda t: request_funds(t, request.user, request.POST.get("amount", ""),
                                                              request.POST.get("purpose", "")),
                         "Funds requested. Finance and the Executive Secretary will see the ticket's full trail.")


@login_required
@require_POST
def ticket_rate(request, pk):
    from .rating import rate_ticket
    return _post_service(request, pk, lambda t: rate_ticket(t, request.user, request.POST.get("rating"),
                                                            request.POST.get("comment", "")),
                         "Thank you for rating this ticket.")


@login_required
@require_GET
def ticket_report_pdf(request, pk):
    from .pdf import render_ticket_pdf
    ticket = get_object_or_404(_visible_tickets_qs(request.user), pk=pk)
    resp = HttpResponse(render_ticket_pdf(ticket), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{ticket.reference}.pdf"'
    return resp


@login_required
def ticket_counts(request):
    """Division queue + counts (I3) and paths taken (I10), scoped to the viewer:
    division head (own division), director (department), ES / super admin (all)."""
    from .routing import counts_for, load_label
    data = counts_for(request.user)
    if data is None:
        raise PermissionDenied("Ticket counts are for division heads, directors and the ES.")
    queue = Ticket.objects.none()
    from org.models import Division
    headed = Division.objects.filter(head=request.user, is_active=True)
    if headed.exists():
        queue = (Ticket.objects.filter(division__in=headed, state__in=Ticket.OPEN_STATES)
                 .select_related("category", "assigned_to", "division").order_by("division_state", "-created_at"))
    for d in data["divisions"]:
        d.load_label = load_label(d)
    return render(request, "tickets/counts.html", {"data": data, "queue": queue})
