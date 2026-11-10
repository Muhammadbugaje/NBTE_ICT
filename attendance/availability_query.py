"""Availability query for the Phase P rotation engine (v2.1 Phase D, G7/X28).

    attendance.availability(user, date_from, date_to)
        -> {"leave": [date, ...], "holiday": [date, ...], "duty": [date, ...]}

* leave    working days of approved leave (any type). Only
           APPROVED leave; pending applications are listed under
           ``leave_pending`` so the engine may treat them as soft conflicts.
* holiday  days a holiday the person gets covers (weekends included).
* duty     working days the person is on an official QA visit.
Also ``unavailable``: the sorted union of leave, holiday and duty, and
``free_working_days``: working days left for assignments.
"""
from __future__ import annotations

from datetime import timedelta


def _days(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def availability(user, date_from, date_to):
    from leave.models import LeaveApplication
    from leave.services import APPROVED_REQUEST_STATUSES, PENDING_REQUEST_STATUSES

    from . import duty, services

    if date_to < date_from:
        date_from, date_to = date_to, date_from
    span = list(_days(date_from, date_to))

    leave, pending = set(), set()
    apps = (LeaveApplication.objects.filter(staff__user=user, start_date__lte=date_to, end_date__gte=date_from,
                                            request__is_deleted=False)
            .select_related("request"))
    for app in apps:
        status = app.request.status
        target = (leave if status in APPROVED_REQUEST_STATUSES
                  else pending if status in PENDING_REQUEST_STATUSES else None)
        if target is None:
            continue
        for d in _days(max(app.start_date, date_from), min(app.end_date, date_to)):
            if services.is_working_day(d):
                target.add(d)

    holiday = set()
    for d in span:
        hols = services.holidays_on(d)
        if hols and services.is_holiday_for(user, d, hols):
            holiday.add(d)

    on_duty = {d for (_uid, d) in duty.duty_map([user.pk], span)}
    unavailable = leave | holiday | on_duty
    return {
        "leave": sorted(leave),
        "leave_pending": sorted(pending - leave),
        "holiday": sorted(holiday),
        "duty": sorted(on_duty),
        "unavailable": sorted(unavailable),
        "free_working_days": [d for d in span if services.is_working_day(d) and d not in unavailable],
    }


def availability_many(users, date_from, date_to):
    """{user: availability(user, ...)} for many people with the same window.

    The same answer as ``availability`` per person — one leave query for
    everyone, the duty map asked once for all the people, holidays still per
    day (cached) — so the rotation's per-person availability never becomes a
    leave query per candidate."""
    from leave.models import LeaveApplication
    from leave.services import APPROVED_REQUEST_STATUSES, PENDING_REQUEST_STATUSES

    from . import duty, services

    if date_to < date_from:
        date_from, date_to = date_to, date_from
    span = list(_days(date_from, date_to))
    people = [u for u in users if getattr(u, "pk", None) is not None]
    out = {u.pk: {"leave": [], "leave_pending": [], "holiday": [], "duty": [],
                  "unavailable": [], "free_working_days": []} for u in people}
    if not people:
        return out
    by_uid = {u.pk: u for u in people}
    days_per_uid, pend_per_uid = {}, {}
    apps = (LeaveApplication.objects.filter(staff__user__in=people, start_date__lte=date_to,
                                            end_date__gte=date_from, request__is_deleted=False)
            .select_related("request", "staff"))
    for app in apps:
        uid = app.staff.user_id
        status = app.request.status
        target = leave_set = (days_per_uid if status in APPROVED_REQUEST_STATUSES
                              else pend_per_uid if status in PENDING_REQUEST_STATUSES else None)
        if target is None:
            continue
        for d in _days(max(app.start_date, date_from), min(app.end_date, date_to)):
            if services.is_working_day(d):
                target.setdefault(uid, set()).add(d)
    holiday_per_uid = {u.pk: set() for u in people}
    hols = {}
    for d in span:
        hol = services.holidays_on(d)
        hols[d] = hol
        if not hol:
            continue
        for u in people:
            if services.is_holiday_for(u, d, hol):
                holiday_per_uid[u.pk].add(d)
    duty_map = duty.duty_map(list(by_uid), span) if span else {}
    on_duty = {u.pk: set() for u in people}
    for (uid, d) in duty_map:
        if uid in on_duty:
            on_duty[uid].add(d)
    spans = [(d, services.is_working_day(d)) for d in span]
    for u in people:
        uid = u.pk
        leave = days_per_uid.get(uid, set())
        holiday = holiday_per_uid.get(uid, set())
        on = on_duty.get(uid, set())
        unavailable = leave | holiday | on
        out[uid] = {
            "leave": sorted(leave),
            "leave_pending": sorted(pend_per_uid.get(uid, set()) - leave),
            "holiday": sorted(holiday),
            "duty": sorted(on),
            "unavailable": sorted(unavailable),
            "free_working_days": [d for d, work in spans if work and d not in unavailable],
        }
    return out
