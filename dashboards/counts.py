"""One definition per dashboard number (Phase 8 card audit).

Every KPI card on the home page, the role dashboards (Super Admin, Director,
Deputy, ES, Chairman), the self-service page and the admin index reads its
number from here, so the same label always means the same query and a test
can compare each card with a direct query (dashboards/test_card_counts.py).

Before this module several cards quietly showed 0 or the wrong figure:
``Staff.objects.filter(is_active=True)`` (Staff has no ``is_active`` field,
the error was swallowed, so "Active staff" was always 0); "Awaiting filing"
looked for the filing step among requests that can never be at it; "My
tasks" dropped a task as soon as a *colleague* finished their part; and so
on — see the Phase 8 notes in the report for the full table.
"""
# scoped-by-caller
from __future__ import annotations

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------
def active_staff():
    """Staff records that are not soft-deleted (the nominal roll)."""
    from staff.models import Staff

    return Staff.objects.all()  # default manager hides soft-deleted rows


def active_users():
    from accounts.models import User

    return User.objects.filter(is_active=True)


# ---------------------------------------------------------------------------
# Workflow requests
# ---------------------------------------------------------------------------
def _request_model():
    from workflow.models import Request

    return Request


def pending_statuses():
    R = _request_model()
    return [R.AWAITING, R.AWAITING_RECEIVING]


def in_flight_statuses():
    """Approved and being worked on: fulfilment and handover. Disjoint from
    "pending" (still waiting for an approval) and from drafts, so the KPI
    matches the "In flight" bars and the per-office table."""
    R = _request_model()
    return [R.IN_PROGRESS, R.AWAITING_FULFILLMENT, R.READY]


ES_STEP_KEY = "syn:executive-secretary-approval"
FILING_STEP_KEY = "syn:records-filing"


def es_signoff(requests=None):
    """Requests whose current step is an Executive Secretary sign-off: either
    the ES gate a request type adds, or a chain step with the ES as
    approver."""
    from workflow.models import ApprovalStep

    R = _request_model()
    qs = requests if requests is not None else R.objects.all()
    return qs.filter(status__in=pending_statuses()).filter(
        Q(metadata__current_step_key=ES_STEP_KEY)
        | Q(current_step__approver_kind=ApprovalStep.APPROVER_ES))


def awaiting_filing(requests=None):
    """Requests waiting at the Records filing step (the gate a request type
    adds with "requires records filing")."""
    R = _request_model()
    qs = requests if requests is not None else R.objects.all()
    return qs.filter(status__in=pending_statuses(), metadata__current_step_key=FILING_STEP_KEY)


# ---------------------------------------------------------------------------
# Tasks and tickets
# ---------------------------------------------------------------------------
def open_tasks():
    from tasks.models import Task

    return Task.objects.exclude(state__in=list(Task.CLOSED_STATES) + [Task.DRAFT])


def my_open_tasks(user):
    """Open tasks where *this user's own* assignment is still live. (A plain
    ``.exclude(assignee_rows__user=…, assignee_rows__state__in=…)`` matches
    the two conditions on different rows, so a task vanished as soon as any
    colleague on it finished.)"""
    from tasks.models import Task, TaskAssignee

    mine = TaskAssignee.objects.filter(task=OuterRef("pk"), user=user).exclude(
        state__in=[TaskAssignee.DONE, TaskAssignee.DECLINED])
    return open_tasks().filter(Exists(mine))


def open_tickets():
    from tickets.models import Ticket

    return Ticket.objects.filter(state__in=Ticket.OPEN_STATES)


def my_visible_open_tickets(user):
    from tickets.access import visible_tickets_queryset
    from tickets.models import Ticket

    return visible_tickets_queryset(user).filter(state__in=Ticket.OPEN_STATES)


def my_active_requests(user):
    R = _request_model()
    return R.objects.filter(requester=user).exclude(status__in=R.CLOSED_STATUSES)


def my_assets(user):
    from assets.models import AssetAssignment

    return AssetAssignment.objects.filter(assigned_to=user, returned_at__isnull=True)


def unread_notifications(user):
    from notifications.models import Notification

    return Notification.objects.filter(recipient=user, is_read=False)


def now():
    return timezone.now()
