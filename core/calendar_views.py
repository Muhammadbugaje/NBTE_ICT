"""Add-to-calendar downloads (.ics) and the personal calendar feed.

Every item view re-uses the visibility rule of the page that shows the item,
so a person can only download what they can already open:

  internal door (nbte_cal:*, nbte_ict/urls.py)
    inspection   inspection.services.visible_inspections + cs.can_view_cases
    task         tasks.views._visible_tasks_qs (work.visibility)
    complaint    complaints.views.can_open + services.get_visible
  portal door (qa_portal:cal_*, qa_core/portal_urls.py)
    inspection   inspection.services.institution_inspections (institutions)
    query        Query.objects.for_user (institutions)

Outsiders get 404 (as on the detail pages: a 403 would confirm the item
exists). The feed (`…/feed/<token>.ics`) is for calendar apps, which cannot
sign in: the token is an HMAC of the user's id and password hash, so it
changes (and old links stop working) when the password changes.
"""
from __future__ import annotations

import datetime as dt

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.http import require_GET

from qa_core.portal_views import institution_required

from .ics import CalEvent, ics_response

FEED_SALT = "nbte.calendar.feed.v1"
EXTERNAL_TYPES = ("institution", "resource_person")


# ---------------------------------------------------------------------------
# Feed tokens
# ---------------------------------------------------------------------------
def feed_token(user) -> str:
    mac = salted_hmac(FEED_SALT, f"{user.pk}:{user.password}:{user.is_active}").hexdigest()[:32]
    return f"{user.pk}-{mac}"


def user_for_token(token: str):
    pk, _sep, _mac = (token or "").partition("-")
    if not pk.isdigit():
        return None
    user = get_user_model().objects.filter(pk=int(pk), is_active=True).first()
    if user is None or not constant_time_compare(feed_token(user), token):
        return None
    return user


# ---------------------------------------------------------------------------
# Events from records
# ---------------------------------------------------------------------------
def _abs(request, path):
    return request.build_absolute_uri(path) if request is not None else path


def _host_tag(request):
    return (request.get_host().split(":")[0] if request is not None else "nbte") or "nbte"


def inspection_event(inspection, request=None, url="", internal=True):
    inst = inspection.case.institution
    start = inspection.actual_start or inspection.scheduled_start
    end = inspection.actual_end or inspection.scheduled_end
    where = ", ".join(str(x) for x in (inst.name, getattr(inst, "lga", ""), getattr(inst, "state", None) or "") if x)
    lead = inspection.team_lead.get_full_name() if (internal and inspection.team_lead_id) else ""
    lines = [f"{inspection.get_kind_display()} · {inspection.reference}",
             f"Institution: {inst.name} ({inst.code})"]
    if lead:
        lines.append(f"Team lead: {lead}")
    return CalEvent(
        uid=f"inspection-{inspection.pk}@{_host_tag(request)}",
        summary=f"NBTE {inspection.get_kind_display().lower()}: {inst.name}",
        start=start, end=end, description="\n".join(lines), location=where, url=_abs(request, url) if url else "",
        status="CANCELLED" if inspection.status == "cancelled" else "CONFIRMED",
        updated=getattr(inspection, "updated_at", None), categories=("NBTE", "Inspection"))


def task_event(task, request=None, url=""):
    return CalEvent(
        uid=f"task-{task.pk}@{_host_tag(request)}",
        summary=f"Due: {task.title} ({task.reference})",
        start=task.due_date, description=f"Task {task.reference} is due today.\nPriority: "
                                         f"{task.get_priority_display()}",
        url=_abs(request, url) if url else "", updated=getattr(task, "updated_at", None),
        status="CANCELLED" if task.is_closed else "CONFIRMED", categories=("NBTE", "Task"))


def complaint_due(complaint):
    stage = complaint.open_stage()
    return (stage, stage.due_at) if stage is not None and stage.due_at else (stage, None)


def complaint_event(complaint, request=None, url=""):
    stage, due = complaint_due(complaint)
    return CalEvent(
        uid=f"complaint-{complaint.pk}-{getattr(stage, 'pk', 0)}@{_host_tag(request)}",
        summary=f"Complaint {complaint.reference} due: {complaint.subject}",
        start=due - dt.timedelta(minutes=30), end=due,
        description=f"{getattr(stage, 'stage_label', '') or complaint.status} — the service deadline for this stage.",
        url=_abs(request, url) if url else "", categories=("NBTE", "Complaint"))


def query_event(query, request=None, url=""):
    return CalEvent(
        uid=f"query-{query.pk}@{_host_tag(request)}",
        summary=f"Answer NBTE query {query.reference}: {query.title}",
        start=query.due_at - dt.timedelta(minutes=30), end=query.due_at,
        description=f"NBTE asked for more information ({query.subject_label or 'QA'}). Respond by this time.",
        url=_abs(request, url) if url else "",
        status="CANCELLED" if query.status == "closed" else "CONFIRMED", categories=("NBTE", "Query"))


def engagement_event(mapping, request=None, url=""):
    inst = mapping.case.institution
    return CalEvent(
        uid=f"engagement-{mapping.pk}@{_host_tag(request)}",
        summary=f"NBTE engagement: {inst.name}",
        start=mapping.from_date, end=mapping.to_date,
        description=f"{mapping.qa_request_programme.programme}" if mapping.qa_request_programme_id else "",
        location=inst.name, url=_abs(request, url) if url else "", categories=("NBTE", "Engagement"))


# ---------------------------------------------------------------------------
# Visibility helpers (one per item, same rule as the detail page)
# ---------------------------------------------------------------------------
def visible_inspection(user, pk):
    from accreditation import case_services as cs
    from inspection import services

    if not cs.can_view_cases(user):
        return None
    return (services.visible_inspections(user).filter(pk=pk)
            .select_related("case__institution", "team_lead").first())


def visible_task(user, pk):
    from tasks.views import _visible_tasks_qs

    return _visible_tasks_qs(user).filter(pk=pk).first()


def visible_complaint(user, pk):
    from complaints import services
    from complaints.views import can_open

    if not can_open(user):
        return None
    return services.get_visible(user, pk)


def portal_inspection(user, pk):
    from inspection import services

    return services.institution_inspections(user).select_related("case__institution", "team_lead").filter(pk=pk).first()


def portal_query(user, pk):
    from accreditation.models import Query

    return Query.objects.for_user(user).filter(pk=pk).first()


def _is_staff(user):
    return getattr(user, "user_type", "staff") not in EXTERNAL_TYPES


# ---------------------------------------------------------------------------
# Internal door
# ---------------------------------------------------------------------------
@login_required
@require_GET
def inspection_ics(request, pk):
    obj = visible_inspection(request.user, pk) if _is_staff(request.user) else None
    if obj is None:
        raise Http404
    ev = inspection_event(obj, request, reverse("qa_insp:inspection", args=[obj.pk]))
    return ics_response([ev], f"nbte-visit-{obj.reference}.ics", "NBTE visit")


@login_required
@require_GET
def task_ics(request, pk):
    obj = visible_task(request.user, pk) if _is_staff(request.user) else None
    if obj is None or not obj.due_date:
        raise Http404
    ev = task_event(obj, request, reverse("tasks:detail", args=[obj.pk]))
    return ics_response([ev], f"nbte-task-{obj.reference}.ics", "NBTE task")


@login_required
@require_GET
def complaint_ics(request, pk):
    obj = visible_complaint(request.user, pk) if _is_staff(request.user) else None
    if obj is None or complaint_due(obj)[1] is None:
        raise Http404
    ev = complaint_event(obj, request, reverse("qa_complaints:detail", args=[obj.pk]))
    return ics_response([ev], f"nbte-complaint-{obj.reference}.ics", "NBTE complaint")


def internal_feed_events(user, request=None):
    """Everything coming up that this staff member is personally involved in."""
    from complaints import services as complaint_services
    from tasks.models import Task

    today = timezone.localdate()
    events = []
    from accreditation import case_services as cs
    from inspection import services as insp

    if cs.can_view_cases(user):
        visits = (insp.visible_inspections(user)
                  .filter(Q(team_lead=user) | Q(team__staff__user=user))
                  .filter(scheduled_end__gte=today - dt.timedelta(days=30))
                  .select_related("case__institution", "team_lead").distinct()[:200])
        events += [inspection_event(v, request, reverse("qa_insp:inspection", args=[v.pk])) for v in visits]
    tasks = (visible_task_qs(user)
             .filter(due_date__gte=today - dt.timedelta(days=14))
             .filter(Q(assignee_rows__user=user) | Q(created_by=user))
             .exclude(state__in=Task.CLOSED_STATES).distinct()[:300])
    events += [task_event(t, request, reverse("tasks:detail", args=[t.pk])) for t in tasks]
    complaints = (complaint_services.visible_to(user).filter(closed_at__isnull=True)
                  .filter(Q(current_assignee=user) | Q(assigned_officer=user)).distinct()[:200])
    for c in complaints:
        if complaint_due(c)[1] is not None:
            events.append(complaint_event(c, request, reverse("qa_complaints:detail", args=[c.pk])))
    return events


def visible_task_qs(user):
    from tasks.views import _visible_tasks_qs

    return _visible_tasks_qs(user)


@require_GET
def internal_feed(request, token):
    user = user_for_token(token)
    if user is None or not _is_staff(user):
        raise Http404
    return ics_response(internal_feed_events(user, request), "nbte-my-dates.ics", "NBTE · my dates", feed=True)


# ---------------------------------------------------------------------------
# Portal door
# ---------------------------------------------------------------------------
@institution_required
@require_GET
def portal_inspection_ics(request, pk):
    obj = portal_inspection(request.user, pk)
    if obj is None:
        raise Http404
    ev = inspection_event(obj, request, reverse("qa_portal:inspections"), internal=False)
    return ics_response([ev], f"nbte-visit-{obj.reference}.ics", "NBTE visit")


@institution_required
@require_GET
def portal_query_ics(request, pk):
    obj = portal_query(request.user, pk)
    if obj is None or obj.due_at is None:
        raise Http404
    ev = query_event(obj, request, reverse("qa_portal:query", args=[obj.pk]))
    return ics_response([ev], f"nbte-query-{obj.reference}.ics", "NBTE query")


def portal_feed_events(user, request=None):
    from accreditation.models import Query
    from inspection import services as insp

    today = timezone.localdate()
    events = []
    if getattr(user, "user_type", "") == "institution":
        visits = insp.institution_inspections(user).filter(scheduled_end__gte=today - dt.timedelta(days=30))
        events += [inspection_event(v, request, reverse("qa_portal:inspections"), internal=False)
                   for v in visits.select_related("case__institution", "team_lead")[:200]]
        queries = Query.objects.for_user(user).filter(status=Query.OPEN, due_at__isnull=False)[:200]
        events += [query_event(q, request, reverse("qa_portal:query", args=[q.pk])) for q in queries]
    elif getattr(user, "user_type", "") == "resource_person":
        rp = insp.rp_of(user)
        if rp is not None:
            maps = insp.rp_engagements(rp).filter(to_date__gte=today - dt.timedelta(days=30))[:200]
            events += [engagement_event(m, request, reverse("qa_portal:rp_engagements")) for m in maps]
    return events


@require_GET
def portal_feed(request, token):
    user = user_for_token(token)
    if user is None or _is_staff(user):
        raise Http404
    return ics_response(portal_feed_events(user, request), "nbte-my-dates.ics", "NBTE · my dates", feed=True)
