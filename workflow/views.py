"""Workflow views.

All views are login-gated. Permission and step-specific checks are delegated
to the service layer (`approve_step`, `return_step`, etc.), which raises
`PermissionDenied` and `ValidationError` when a user shouldn't be acting.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from urllib.parse import urlencode


from core.sorting import apply_sort

from . import services
from .forms import (
    ApproveForm,
    AssignForm,
    AttachmentForm,
    CancelForm,
    CloseForm,
    CommentForm,
    DeleteRequestForm,
    FulfilledForm,
    RaiseRequestForm,
    RejectForm,
    ResubmitForm,
    ReturnForm,
    build_request_form,
    build_resubmit_form,
    extract_form_data,
)
from .models import (
    ApprovalStep,
    Request,
    RequestType,
    RequestCategory,
    RequestActivity,
    RequestComment,
    RequestAttachment,
)


PER_PAGE = 25

SORTABLE_REQUESTS = {
    "created":   "created_at",
    "updated":   "updated_at",
    "subject":   "subject",
    "reference": "reference",
    "status":    "status",
    "priority":  "priority",
}


def _can_view(request, req):
    """Who can see a request:
      - the requester
      - anyone currently acting on it (current approver, assignee)
      - anyone who acted on it before
      - users in the receiving department with the director role
      - superusers
    """
    return can_view_request(request.user, req)


def can_view_request(user, req):
    """User-based form of `_can_view` (used by attachment downloads).
    The rule lives in workflow/access.py."""
    from .access import can_view_request as _rule
    return _rule(user, req)


def _require_view(request, pk):
    req = get_object_or_404(
        Request.objects.select_related(
            "requester", "request_type", "receiving_department",
            "assigned_to", "current_step",
        ),
        pk=pk,
    )
    if not _can_view(request, req):
        raise PermissionDenied("You do not have access to this request.")
    return req


# ===========================================================================
# Lists
# ===========================================================================
@login_required
def my_requests(request):
    qs = (
        Request.objects
        .filter(requester=request.user)
        .select_related("request_type", "receiving_department", "assigned_to")
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(subject__icontains=q) | Q(reference__icontains=q))

    status_filter = request.GET.get("status", "all")
    if status_filter in dict(Request.STATUS_CHOICES):
        qs = qs.filter(status=status_filter)

    qs, sort_key, sort_dir = apply_sort(
        qs, request.GET.get("sort"), request.GET.get("direction"),
        SORTABLE_REQUESTS, default="created",
    )

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "workflow/my_requests.html", {
        "page": page,
        "q": q,
        "status_filter": status_filter,
        "status_choices": Request.STATUS_CHOICES,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "assigned_count": Request.objects.filter(
            assigned_to=request.user
        ).exclude(status__in=Request.CLOSED_STATUSES).count(),
        "counts": _request_counts(scope="mine", user=request.user),
    })




@login_required
def awaiting_action(request):
    """Show the user's permission-scoped, cross-module action queue."""
    from urllib.parse import urlencode

    from dashboards.action_queue import (
        AGE_FILTERS,
        PRIORITY_ORDER,
        SOURCE_LABELS,
        build_action_queue,
    )

    items, filters = build_action_queue(
        request.user,
        source=request.GET.get("source", "all"),
        priority=request.GET.get("priority", "all"),
        age=request.GET.get("age", "all"),
        query=request.GET.get("q", "").strip(),
    )
    page = Paginator(items, PER_PAGE).get_page(request.GET.get("page"))

    # `build_action_queue` returns filters as a plain dict. Read it as such.
    # (`filters["query"]` on a dict; `filters.query` on an object — we support
    # both so this view doesn't break if the queue service changes shape later.)
    def _f(name, default=""):
        if isinstance(filters, dict):
            return filters.get(name, default)
        return getattr(filters, name, default)

    filter_query    = _f("query", "")
    filter_source   = _f("source", "all")
    filter_priority = _f("priority", "all")
    filter_age      = _f("age", "all")

    has_active_filters = any([
        filter_query,
        filter_source != "all",
        filter_priority != "all",
        filter_age != "all",
    ])

    querystring = urlencode({
        key: value
        for key, value in {
            "q":        filter_query,
            "source":   filter_source,
            "priority": filter_priority,
            "age":      filter_age,
        }.items()
        if value and value != "all"
    })

    def _as_items(collection):
        """
        Normalise a mapping OR an iterable into a list of (value, label) pairs.

        Dict         →  [("urgent", "Urgent"), ...]
        List[str]    →  [("urgent", "Urgent"), ...]   # title-cased
        List[tuple]  →  unchanged
        """
        if hasattr(collection, "items"):
            return list(collection.items())
        result = []
        for entry in collection:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                result.append(tuple(entry))
            else:
                result.append(
                    (str(entry), str(entry).replace("-", " ").title())
                )
        return result

    return render(request, "dashboards/awaiting_action.html", {
        "page":               page,
        "total":              len(items),
        "filters":            filters,
        "source_options":     _as_items(SOURCE_LABELS),
        "priority_options":   _as_items(PRIORITY_ORDER),
        "age_options":        _as_items(AGE_FILTERS),
        "has_active_filters": has_active_filters,
        "querystring":        querystring,
    })

@login_required
@require_POST
def bulk_awaiting_action(request):
    """Run bulk decisions through the regular workflow service layer."""
    from dashboards.action_queue import get_bulk_actionable_requests

    action = request.POST.get("bulk_action", "")
    comment = (request.POST.get("comment") or "").strip()
    raw_ids = request.POST.getlist("request_ids")
    try:
        request_ids = list(dict.fromkeys(int(value) for value in raw_ids))
    except (TypeError, ValueError):
        messages.error(request, "The selected request list is invalid.")
        return redirect("workflow:awaiting_action")

    if action not in {"approve", "return"}:
        messages.error(request, "Choose approve or return for the selected requests.")
        return redirect("workflow:awaiting_action")
    if not request_ids:
        messages.error(request, "Select at least one approval request.")
        return redirect("workflow:awaiting_action")
    if len(request_ids) > 50:
        messages.error(request, "Select no more than 50 requests at a time.")
        return redirect("workflow:awaiting_action")
    if action == "return" and not comment:
        messages.error(request, "A comment is required when returning requests.")
        return redirect("workflow:awaiting_action")

    actionable = get_bulk_actionable_requests(request.user, request_ids)
    if len(actionable) != len(request_ids):
        messages.error(
            request,
            "One or more selected requests are no longer available for this action.",
        )
        return redirect("workflow:awaiting_action")

    completed = []
    failed = []
    for request_id in request_ids:
        req = actionable[request_id]
        try:
            if action == "approve":
                services.approve_step(req, request.user, comment=comment)
            else:
                services.return_to_requester(req, request.user, comment=comment)
        except (PermissionDenied, ValidationError):
            failed.append(req.reference)
        else:
            completed.append(req.reference)

    if completed:
        verb = "approved" if action == "approve" else "returned to their requesters"
        messages.success(
            request,
            f"{len(completed)} request{'s' if len(completed) != 1 else ''} {verb}.",
        )
    if failed:
        messages.error(
            request,
            "Could not process: " + ", ".join(failed) + ". Review their current status.",
        )
    return redirect("workflow:awaiting_action")


DEPARTMENT_LEAD_ROLE_CODES = (
    "executive-secretary", "director", "deputy-director",
    "office-head-admin", "office-head-ts", "office-head-dev", "division-head",
)


def can_see_incoming(user):
    """Review M7: the incoming queue is for the receiving department's leads,
    not every member of the department."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    from .access import is_department_request_viewer
    return bool(is_department_request_viewer(user) or (
        user.department_id and user.role_id
        and user.role.code in DEPARTMENT_LEAD_ROLE_CODES))


@login_required
def incoming(request):
    """Requests routed to my department, awaiting the receiving director."""
    if not can_see_incoming(request.user):
        raise PermissionDenied("Only department leads can view incoming requests.")
    if not request.user.department_id:
        qs = Request.objects.none()
    else:
        qs = (
            Request.objects
            .filter(receiving_department=request.user.department)
            .exclude(status__in=Request.CLOSED_STATUSES)
            .select_related("requester", "request_type", "assigned_to")
        )

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(subject__icontains=q) | Q(reference__icontains=q))

    qs, sort_key, sort_dir = apply_sort(
        qs, request.GET.get("sort"), request.GET.get("direction"),
        SORTABLE_REQUESTS, default="created",
    )

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "workflow/incoming.html", {
        "page": page,
        "department": request.user.department,
        "q": q,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
    })

# ===========================================================================
# Director & admin views
# ===========================================================================
def _is_director(user):
    return bool(user.is_authenticated and user.department_id and user.role_id
                and user.role.code == "director")


def _can_see_office_requests(user):
    from .access import is_department_request_viewer
    return is_department_request_viewer(user) or user.is_superuser


def _can_see_all_requests(user):
    from .access import can_view_all_requests
    return can_view_all_requests(user)


@login_required
def office_requests(request):
    if not _can_see_office_requests(request.user):
        raise PermissionDenied("Only department directors can view office requests.")

    dept = request.user.department
    if dept is None:
        return redirect("workflow:all_requests")

    qs = (
        Request.objects
        .filter(Q(requester__department=dept) | Q(receiving_department=dept))
        .select_related("requester", "request_type", "receiving_department", "assigned_to")
        .distinct()
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(subject__icontains=q) | Q(reference__icontains=q))

    status_filter = request.GET.get("status", "all")
    if status_filter in dict(Request.STATUS_CHOICES):
        qs = qs.filter(status=status_filter)

    qs, sort_key, sort_dir = apply_sort(
        qs, request.GET.get("sort"), request.GET.get("direction"),
        SORTABLE_REQUESTS, default="created",
    )

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "workflow/office_requests.html", {
        "page": page,
        "department": dept,
        "q": q,
        "status_filter": status_filter,
        "status_choices": Request.STATUS_CHOICES,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "counts": _request_counts(scope="office", department=dept),
    })

@login_required
def all_requests(request):
    if not _can_see_all_requests(request.user):
        raise PermissionDenied("You do not have permission to view all requests.")

    qs = (
        Request.objects
        .select_related("requester", "request_type", "receiving_department",
                        "assigned_to", "requester__department")
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(subject__icontains=q) | Q(reference__icontains=q))

    status_filter = request.GET.get("status", "all")
    if status_filter in dict(Request.STATUS_CHOICES):
        qs = qs.filter(status=status_filter)

    dept_filter = request.GET.get("department")
    if dept_filter:
        qs = qs.filter(
            Q(requester__department__code=dept_filter)
            | Q(receiving_department__code=dept_filter)
        )

    qs, sort_key, sort_dir = apply_sort(
        qs, request.GET.get("sort"), request.GET.get("direction"),
        SORTABLE_REQUESTS, default="created",
    )

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    from staff.models import Department
    return render(request, "workflow/all_requests.html", {
        "page": page,
        "q": q,
        "status_filter": status_filter,
        "status_choices": Request.STATUS_CHOICES,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "counts": _request_counts(scope="all"),
        "departments": Department.objects.filter(is_active=True).order_by("name"),
        "dept_filter": dept_filter or "",
    })

def _request_counts(scope="mine", department=None, user=None):
    """Return a dict of {status: count, 'all': total, 'active': open_count}
    scoped to the given view."""
    base = Request.objects.all()
    if scope == "mine":
        base = base.filter(requester=user)
    elif scope == "office" and department is not None:
        base = base.filter(
            Q(requester__department=department) | Q(receiving_department=department)
        ).distinct()
    # scope == "all" uses base unfiltered

    out = {"all": base.count()}
    for value, _label in Request.STATUS_CHOICES:
        out[value] = base.filter(status=value).count()
    # "active" = anything not closed
    out["active"] = base.exclude(status__in=Request.CLOSED_STATUSES).count()
    return out


@login_required
def assigned_to_me(request):
    """Requests where I'm the assigned staff member."""
    qs = (
        Request.objects
        .filter(assigned_to=request.user)
        .select_related("requester", "request_type", "receiving_department")
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(subject__icontains=q) | Q(reference__icontains=q))

    status_filter = request.GET.get("status", "all")
    if status_filter not in dict(Request.STATUS_CHOICES):
        status_filter = "all"
    else:
        qs = qs.filter(status=status_filter)

    qs, sort_key, sort_dir = apply_sort(
        qs, request.GET.get("sort"), request.GET.get("direction"),
        SORTABLE_REQUESTS, default="created",
    )

    paginator = Paginator(qs, PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "workflow/assigned_to_me.html", {
        "page": page,
        "q": q,
        "status_filter": status_filter,
        "status_choices": Request.STATUS_CHOICES,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
    })

# ===========================================================================
# Raise
# ===========================================================================
@login_required
def raise_request_view(request, type_code=None):
    """Two modes:

    - No type_code → show a grid of active request types to pick from.
    - With type_code → show the type-specific form (schema-driven).
    """
    # --- Mode 1: pick a type ---
    if not type_code:
        categories = (
            RequestCategory.objects
            .filter(is_active=True, request_types__is_active=True)
            .distinct()
            .prefetch_related(
                Prefetch(
                    "request_types",
                    queryset=RequestType.objects.filter(is_active=True).order_by("sort_order", "name"),
                )
            )
            .order_by("sort_order", "name")
        )
        return render(request, "workflow/raise_pick.html", {
            "categories": categories,
        })

    # --- Mode 2: fill the form ---
    request_type = get_object_or_404(
        RequestType.objects.filter(is_active=True),
        code=type_code,
    )

    if request.method == "POST":
        form = build_request_form(
            request_type, request.user,
            data=request.POST, files=request.FILES,
        )
        if form.is_valid():
            form_data = extract_form_data(form, request_type)
            try:
                if request_type.code == "annual-leave":
                    from leave.services import validate_application
                    validate_application(request.user, form_data)
                with transaction.atomic():
                    req = services.raise_request(
                        requester=request.user,
                        request_type=request_type,
                        subject=form.cleaned_data["subject"],
                        body=form.cleaned_data["body"],
                        priority=form.cleaned_data["priority"],
                        form_data=form_data,
                    )
                    if request_type.code == "annual-leave":
                        from leave.services import sync_application
                        sync_application(req, form_data)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(request, f"Request {req.reference} raised.")
                return redirect("workflow:detail", pk=req.pk)
    else:
        form = build_request_form(request_type, request.user)

    return render(request, "workflow/raise.html", {
        "form": form,
        "request_type": request_type,
        "schema_fields": _schema_field_names(request_type),
    })


def _schema_field_names(request_type):
    """Ordered list of (key, definition) for the schema fields, so the
    template can iterate them."""
    from .forms import validate_schema
    from django.core.exceptions import ValidationError
    if not request_type or not request_type.form_schema:
        return []
    try:
        defs = validate_schema(request_type.form_schema)
    except ValidationError:
        return []
    return [(d["key"], d) for d in defs]


# ===========================================================================
# Detail
# ===========================================================================
@login_required
def detail(request, pk):
    req = _require_view(request, pk)
    current_step = services._resolve_current_step(req)
    current_actor = services.resolve_approver(current_step, req) if current_step else None
    leave_summary = None
    if req.request_type.code == "annual-leave":
        from leave.services import request_summary
        leave_summary = request_summary(req)

    # What can the current user do here?
    can_act = bool(current_actor and current_actor.pk == request.user.pk)
    is_assignee = req.assigned_to_id == request.user.pk
    is_requester = req.requester_id == request.user.pk
    # Review M2: internal notes are for everyone who can view EXCEPT the requester.
    show_internal = request.user.is_superuser or not is_requester
    comments_qs = req.comments.select_related("author").order_by("created_at")
    if not show_internal:
        comments_qs = comments_qs.filter(is_internal=False)

    timeline_qs = req.timeline().select_related("actor")
    timeline_last_pk = (
        RequestActivity.objects
        .filter(request=req)
        .order_by("-pk")
        .values_list("pk", flat=True)
        .first() or 0
    )

    ctx = {
        "req": req,
        "leave_summary": leave_summary,
        "current_step": current_step,
        "current_actor": current_actor,
        "can_act": can_act,
        "is_assignee": is_assignee,
        "is_requester": is_requester,
        "timeline": req.timeline().select_related("actor"),
        "approvals": req.approvals.select_related("step", "actor").order_by("decided_at"),
        "comments": comments_qs,
        "show_internal": show_internal,
        "attachments": req.attachments.select_related("uploaded_by"),
        "approve_form": ApproveForm(),
        "return_form": ReturnForm(),
        "reject_form": RejectForm(),
        "assign_form": AssignForm(handling_department=req.receiving_department),
        "fulfilled_form": FulfilledForm(),
        "close_form": CloseForm(),
        "cancel_form": CancelForm(),
        "comment_form": CommentForm(),
        "attachment_form": AttachmentForm(),
        "timeline": timeline_qs,
        "timeline_last_pk": timeline_last_pk,
        "resubmit_form": build_resubmit_form(req, request.user) if req.status == Request.RETURNED else None,
        "resubmit_schema_fields": _schema_field_names(req.request_type) if req.status == Request.RETURNED else [],
        "is_closed": req.is_closed,
    }
    # Link the asset only if the viewer may open it (otherwise a dead link).
    ctx["asset_link_ok"] = False
    if getattr(req, "asset_id", None):
        from assets.access import can_view_asset
        ctx["asset_link_ok"] = can_view_asset(request.user, req.asset)
    return render(request, "workflow/detail.html", ctx)


# ===========================================================================
# Actions
# ===========================================================================
def _handle_action(request, pk, form_class, service_fn, success_msg):
    """Shared wrapper for POST-only actions on a request."""
    req = _require_view(request, pk)
    form = form_class(request.POST, request.FILES) if request.FILES else form_class(request.POST)
    if not form.is_valid():
        messages.error(request, "Please check the form and try again.")
        return redirect("workflow:detail", pk=req.pk)
    try:
        service_fn(req, request.user, **form.cleaned_data)
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(request, success_msg)
    return redirect("workflow:detail", pk=req.pk)


@login_required
@require_POST
def approve(request, pk):
    return _handle_action(
        request, pk, ApproveForm,
        lambda req, user, comment="": services.approve_step(req, user, comment=comment),
        "Approved.",
    )


@login_required
@require_POST
def return_step(request, pk):
    return _handle_action(
        request, pk, ReturnForm,
        lambda req, user, comment="": services.return_step(req, user, comment=comment),
        "Returned.",
    )


@login_required
@require_POST
def reject(request, pk):
    return _handle_action(
        request, pk, RejectForm,
        lambda req, user, comment="": services.reject_step(req, user, comment=comment),
        "Rejected.",
    )


@login_required
@require_POST
def assign(request, pk):
    req = _require_view(request, pk)
    form = AssignForm(request.POST, handling_department=req.receiving_department)
    if not form.is_valid():
        messages.error(request, "Please select a valid assignee.")
        return redirect("workflow:detail", pk=req.pk)
    try:
        services.assign_staff(
            req, request.user,
            staff_user=form.cleaned_data["assignee"],
            comment=form.cleaned_data.get("comment") or "",
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(request, "Assigned.")
    return redirect("workflow:detail", pk=req.pk)


@login_required
@require_POST
def fulfilled(request, pk):
    return _handle_action(
        request, pk, FulfilledForm,
        lambda req, user, comment="": services.mark_fulfilled(req, user, comment=comment),
        "Marked as fulfilled.",
    )


@login_required
@require_POST
def close_request(request, pk):
    return _handle_action(
        request, pk, CloseForm,
        lambda req, user, comment="": services.confirm_handover(req, user, comment=comment),
        "Request closed.",
    )


@login_required
@require_POST
def cancel(request, pk):
    return _handle_action(
        request, pk, CancelForm,
        lambda req, user, comment="": services.cancel_request(req, user, comment=comment),
        "Request cancelled.",
    )


@login_required
@require_POST
def comment(request, pk):
    req = _require_view(request, pk)
    if req.is_closed:
        messages.error(request, "This request is closed. No further comments can be added.")
        return redirect("workflow:detail", pk=req.pk)
    form = CommentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Comment cannot be empty.")
        return redirect("workflow:detail", pk=req.pk)
    services.add_comment(
        req, request.user,
        body=form.cleaned_data["body"],
        internal=form.cleaned_data.get("is_internal", False),
    )
    messages.success(request, "Comment added.")
    return redirect("workflow:detail", pk=req.pk)


@login_required
@require_POST
def attach(request, pk):
    req = _require_view(request, pk)
    if req.is_closed:
        messages.error(request, "This request is closed. No further files can be attached.")
        return redirect("workflow:detail", pk=req.pk)
    form = AttachmentForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Please choose a file.")
        return redirect("workflow:detail", pk=req.pk)
    services.add_attachment(
        req, request.user,
        file=form.cleaned_data["file"],
        description=form.cleaned_data.get("description") or "",
    )
    messages.success(request, "Attachment added.")
    return redirect("workflow:detail", pk=req.pk)


# ===========================================================================
# PDF record
# ===========================================================================
@login_required
def record_pdf(request, pk):
    req = _require_view(request, pk)
    if not req.record_pdf:
        raise Http404("No record PDF has been generated for this request yet.")
    # Reuse the same permission check as the detail page.
    return FileResponse(
        req.record_pdf.open("rb"),
        content_type="application/pdf",
        as_attachment=False,
        filename=req.record_original_name or f"{req.reference}.pdf",
    )
    
@login_required
@require_POST
def delete_request(request, pk):
    """Delete a request with a reason. Only the requester or a superuser."""
    req = get_object_or_404(Request, pk=pk)
    # Permission check happens inside the service — this is a defence in depth
    if req.requester_id != request.user.pk and not request.user.is_superuser:
        raise PermissionDenied("You cannot delete this request.")

    form = DeleteRequestForm(request.POST)
    if not form.is_valid():
        messages.error(
            request,
            "; ".join(form.errors.get("reason", []) + form.errors.get("confirm", []))
            or "Please check the form and try again.",
        )
        return redirect("workflow:detail", pk=req.pk)

    try:
        services.delete_request(
            req, request.user,
            reason=form.cleaned_data["reason"],
        )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        return redirect("workflow:detail", pk=req.pk)

    messages.success(request, f"Request {req.reference} was deleted.")
    return redirect("workflow:my_requests")

@login_required
@require_POST
def return_to_requester(request, pk):
    return _handle_action(
        request, pk, ReturnForm,
        lambda req, user, comment="": services.return_to_requester(req, user, comment=comment),
        "Returned to the requester for revision.",
    )

@login_required
@require_POST
def resubmit(request, pk):
    req = _require_view(request, pk)
    if req.requester_id != request.user.pk:
        raise PermissionDenied("Only the requester can resubmit.")
    if req.status != Request.RETURNED:
        messages.error(request, "This request is not currently returned for revision.")
        return redirect("workflow:detail", pk=req.pk)

    form = build_resubmit_form(
        req, request.user,
        data=request.POST, files=request.FILES,
    )
    if not form.is_valid():
        messages.error(request, "Please check the form and try again.")
        return redirect("workflow:detail", pk=req.pk)

    form_data = extract_form_data(form, req.request_type)
    try:
        if req.request_type.code == "annual-leave":
            from leave.services import validate_application
            validate_application(request.user, form_data, exclude_request_id=req.pk)
        with transaction.atomic():
            services.resubmit(
                req, request.user,
                subject=form.cleaned_data["subject"],
                body=form.cleaned_data["body"],
                comment=form.cleaned_data.get("comment") or "",
                form_data=form_data,
            )
            if req.request_type.code == "annual-leave":
                from leave.services import sync_application
                sync_application(req, form_data)
            if form.cleaned_data.get("file"):
                services.add_attachment(
                    req, request.user,
                    file=form.cleaned_data["file"],
                    description=form.cleaned_data.get("file_description") or "",
                )
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(request, "Request revised and resubmitted.")
    return redirect("workflow:detail", pk=req.pk)
        
@login_required
@require_POST
def delete_comment(request, pk, comment_id):
    req = _require_view(request, pk)
    if req.is_closed:
        messages.error(request, "This request is closed.")
        return redirect("workflow:detail", pk=req.pk)
    comment = get_object_or_404(RequestComment, pk=comment_id, request=req)
    try:
        services.delete_comment(req, request.user, comment)
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(request, "Comment deleted.")
    return redirect("workflow:detail", pk=req.pk)


@login_required
@require_POST
def delete_attachment(request, pk, attachment_id):
    req = _require_view(request, pk)
    if req.is_closed:
        messages.error(request, "This request is closed.")
        return redirect("workflow:detail", pk=req.pk)
    attachment = get_object_or_404(RequestAttachment, pk=attachment_id, request=req)
    try:
        services.delete_attachment(req, request.user, attachment)
    except (PermissionDenied, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(request, "Attachment deleted.")
    return redirect("workflow:detail", pk=req.pk)    
    
import json
import time
from django.http import StreamingHttpResponse
from django.views.decorators.http import require_GET


@login_required
@require_GET
def timeline_partial(request, pk):
    """Re-render the timeline only — HTMX swaps this into the container."""
    req = _require_view(request, pk)
    timeline = req.timeline().select_related("actor")
    return render(request, "workflow/_timeline.html", {
        "req": req,
        "timeline": timeline,
    })


POLL_INTERVAL = 5   # seconds between checks


@login_required
@require_GET
def request_stream(request, pk):
    """Timeline poll for one request (was an endless SSE stream - review H2).

    `?since=<highest activity pk on the page>`; answers immediately with
    {"last_pk", "count"} (JSON) or one SSE frame. Never sleeps.
    """
    from core.polling import activity_response

    req = _require_view(request, pk)
    return activity_response(request, RequestActivity.objects.filter(request_id=req.pk))


