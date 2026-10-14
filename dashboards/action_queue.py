"""Permission-aware aggregation for the unified action queue."""
from datetime import date, datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone


SOURCE_LABELS = {
    "contact": "Public message",
    "request": "Request approval",
    "asset": "Asset return",
    "ticket": "Escalated ticket",
}
PRIORITY_ORDER = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
AGE_FILTERS = {
    "fresh": "0–1 days",
    "week": "2–7 days",
    "month": "8–30 days",
    "older": "Over 30 days",
}


def _age_days(timestamp):
    if isinstance(timestamp, datetime):
        return max(0, (timezone.now() - timestamp).days)
    if isinstance(timestamp, date):
        return max(0, (timezone.localdate() - timestamp).days)
    return 0


def _make_item(*, source, title, reference, detail, url, priority, timestamp,
               age_days=None, selectable=False, object_id=None, request_type="",
               step="", requester="", department=""):
    return {
        "source": source,
        "source_label": SOURCE_LABELS[source],
        "title": title,
        "reference": reference,
        "detail": detail,
        "url": url,
        "priority": priority or "normal",
        "timestamp": timestamp,
        "age_days": _age_days(timestamp) if age_days is None else age_days,
        "selectable": selectable,
        "object_id": object_id,
        "request_type": request_type,
        "step": step,
        "requester": requester,
        "department": department,
    }


def _contact_items(user):
    if not user.has_perm("core.can_handle_contact_messages"):
        return []

    from core.views import _contact_messages_for

    messages = (
        _contact_messages_for(user)
        .filter(is_handled=False)
        .filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))
        .select_related("handling_department", "assigned_to")
    )
    return [
        _make_item(
            source="contact",
            title=message.subject or "Public enquiry",
            reference=f"Message #{message.pk}",
            detail=(
                f"From {message.name} · "
                f"{message.handling_department.name if message.handling_department else 'General inbox'}"
            ),
            url=reverse("core:contact_detail", args=[message.pk]),
            priority="normal",
            timestamp=message.created_at,
        )
        for message in messages
    ]


def _request_items(user):
    from workflow import services
    from workflow.models import ApprovalStep, Request

    requests = (
        Request.objects
        .exclude(status__in=Request.CLOSED_STATUSES)
        .select_related(
            "requester", "request_type", "receiving_department",
            "assigned_to", "current_step",
        )
        .order_by("current_step_started_at", "created_at")
    )
    items = []
    for request in requests:
        try:
            step, _actor = services._ensure_actor(request, user)
        except (PermissionDenied, ValidationError):
            continue

        request.active_step = step
        items.append(_make_item(
            source="request",
            title=request.subject,
            reference=request.reference,
            detail=(
                f"{step.name} · "
                f"{request.requester.get_full_name() or request.requester.email}"
            ),
            url=reverse("workflow:detail", args=[request.pk]),
            priority=request.priority,
            timestamp=request.current_step_started_at or request.created_at,
            selectable=step.kind == ApprovalStep.KIND_APPROVAL,
            object_id=request.pk if step.kind == ApprovalStep.KIND_APPROVAL else None,
            request_type=request.request_type.name,
            step=step.name,
            requester=request.requester.get_full_name() or request.requester.email,
            department=request.receiving_department.name if request.receiving_department else "",
        ))
    return items


def _asset_items(user):
    from assets.models import AssetAssignment
    from assets.views import _can_edit_asset, _visible_assets_qs

    visible_assets = _visible_assets_qs(user)
    assignments = (
        AssetAssignment.objects
        .filter(
            asset__in=visible_assets,
            returned_at__isnull=True,
            expected_return_at__lt=timezone.localdate(),
        )
        .select_related("asset", "asset__category", "assigned_to", "assigned_by")
    )
    items = []
    for assignment in assignments:
        if not (
            assignment.assigned_to_id == user.pk
            or assignment.assigned_by_id == user.pk
            or _can_edit_asset(user, assignment.asset)
        ):
            continue
        overdue_days = _age_days(assignment.expected_return_at)
        items.append(_make_item(
            source="asset",
            title=assignment.asset.display_name,
            reference=assignment.asset.asset_tag,
            detail=(
                f"Held by {assignment.assigned_to.get_full_name() or assignment.assigned_to.email} · "
                f"Due {assignment.expected_return_at:%b %d, %Y}"
            ),
            url=reverse("assets:detail", args=[assignment.asset.asset_tag]),
            priority="normal",
            timestamp=assignment.expected_return_at,
            age_days=overdue_days,
        ))
    return items


def _ticket_items(user):
    from tickets.models import Ticket
    from tickets.views import _visible_tickets_qs

    role_code = user.role.code if user.role_id else ""
    tickets = (
        _visible_tickets_qs(user)
        .filter(state=Ticket.ESCALATED, escalation_level__gt=0)
        .select_related("handling_department", "assigned_to")
    )
    items = []
    for ticket in tickets:
        level = ticket.escalation_level
        in_department = user.department_id == ticket.handling_department_id
        is_recipient = ticket.assigned_to_id == user.pk and level >= 1
        is_recipient = is_recipient or (
            in_department and level >= 1 and role_code in {
                "office-head-admin", "office-head-ts", "office-head-dev",
            }
        )
        is_recipient = is_recipient or (
            in_department and level >= 2 and role_code == "deputy-director"
        )
        is_recipient = is_recipient or (
            in_department and level >= 3 and role_code == "director"
        )
        if not (user.is_superuser or is_recipient):
            continue

        items.append(_make_item(
            source="ticket",
            title=ticket.subject,
            reference=ticket.reference,
            detail=f"Level {level} escalation · {ticket.handling_department.name}",
            url=reverse("tickets:detail", args=[ticket.pk]),
            priority=ticket.priority,
            timestamp=ticket.escalated_at or ticket.updated_at,
        ))
    return items


def build_action_queue(user, *, source="all", priority="all", age="all", query=""):
    """Return action items visible to `user`, filtered and priority-sorted."""
    valid_sources = {"all", *SOURCE_LABELS}
    valid_priorities = {"all", *PRIORITY_ORDER}
    source = source if source in valid_sources else "all"
    priority = priority if priority in valid_priorities else "all"
    age = age if age in {"all", *AGE_FILTERS} else "all"
    query = (query or "").strip().casefold()

    builders = {
        "contact": _contact_items,
        "request": _request_items,
        "asset": _asset_items,
        "ticket": _ticket_items,
    }
    selected_sources = SOURCE_LABELS if source == "all" else {source: SOURCE_LABELS[source]}
    items = []
    for key in selected_sources:
        items.extend(builders[key](user))

    if priority != "all":
        items = [item for item in items if item["priority"] == priority]
    if age != "all":
        if age == "fresh":
            items = [item for item in items if item["age_days"] <= 1]
        elif age == "week":
            items = [item for item in items if 1 < item["age_days"] <= 7]
        elif age == "month":
            items = [item for item in items if 7 < item["age_days"] <= 30]
        else:
            items = [item for item in items if item["age_days"] > 30]
    if query:
        items = [
            item for item in items
            if query in " ".join((
                item["title"], item["reference"], item["detail"],
            )).casefold()
        ]

    items.sort(key=lambda item: (
        PRIORITY_ORDER.get(item["priority"], PRIORITY_ORDER["normal"]),
        -item["age_days"],
        item["reference"],
    ))
    return items, {
        "source": source,
        "priority": priority,
        "age": age,
        "query": query,
    }


def get_bulk_actionable_requests(user, request_ids):
    """Re-resolve request permissions and active steps before bulk actions."""
    from workflow import services
    from workflow.models import ApprovalStep, Request

    requests = Request.objects.filter(
        pk__in=request_ids,
    ).exclude(status__in=Request.CLOSED_STATUSES).select_related(
        "requester", "request_type", "receiving_department",
        "assigned_to", "current_step",
    )
    actionable = {}
    for request in requests:
        try:
            step, _actor = services._ensure_actor(request, user)
        except (PermissionDenied, ValidationError):
            continue
        if step.kind == ApprovalStep.KIND_APPROVAL:
            actionable[request.pk] = request
    return actionable
