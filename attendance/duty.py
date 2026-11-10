"""Official duty on QA visits (v2.1 Phase D, G7).

A confirmed NBTE member of an inspection's team is "On official duty" on
every working day of the visit: from the actual dates once the Team Lead
records them, else the scheduled dates. Those days never become absent and
need no check-in. Records carry ``source="visit"`` and ``duty_inspection``.

Who counts as a confirmed member:
  * a staff entry of the visit team confirmed by the division director
    (``InspectionTeamMember.staff`` + ``confirmed_by_division_director``);
  * an NBTE QA personnel mapped to the visit (``qa_mapping.qa_personnel``,
    mapping active or released after the visit).
Resource persons are not NBTE staff and have no attendance.

Public API:
    duty_days(inspection) -> list[date]
    duty_user_ids(inspection) -> set[int]
    duty_map(user_ids, days) -> {(user_id, day): inspection}
    sync_visit_duty(inspection) -> (written, removed)
"""
from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import AttendanceRecord

LIVE_MAPPING_STATUSES = ("active", "released")


def _inspection_model():
    from inspection.models import Inspection
    return Inspection


def visit_window(inspection):
    start = inspection.actual_start or inspection.scheduled_start
    end = inspection.actual_end or inspection.scheduled_end
    if not start or not end or end < start:
        return None, None
    return start, end


def duty_days(inspection):
    from .services import is_working_day

    if (inspection.status or "") == "cancelled":
        return []
    start, end = visit_window(inspection)
    if start is None:
        return []
    days, d = [], start
    while d <= end:
        if is_working_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


def duty_user_ids(inspection):
    from inspection.models import InspectionTeamMember

    ids = set()
    members = (InspectionTeamMember.unscoped.filter(inspection=inspection)
               .select_related("staff", "qa_mapping__qa_personnel__staff"))
    for m in members:
        if m.staff_id and m.confirmed_by_division_director and m.staff.user_id:
            ids.add(m.staff.user_id)
        mapping = m.qa_mapping
        if (mapping is not None and mapping.qa_personnel_id and mapping.status in LIVE_MAPPING_STATUSES
                and mapping.qa_personnel.staff.user_id):
            ids.add(mapping.qa_personnel.staff.user_id)
    if inspection.team_lead_id:
        ids.add(inspection.team_lead_id)
    return ids


def inspections_between(first_day, last_day):
    Inspection = _inspection_model()
    from django.db.models import Q

    qs = Inspection.unscoped.exclude(status="cancelled").filter(
        Q(actual_start__isnull=False, actual_start__lte=last_day, actual_end__gte=first_day)
        | Q(actual_start__isnull=True, scheduled_start__lte=last_day, scheduled_end__gte=first_day))
    return list(qs)


def duty_map(user_ids, days):
    """{(user_id, day): inspection} for the given people and days."""
    days = sorted(set(days))
    if not days:
        return {}
    wanted = set(user_ids)
    out = {}
    for insp in inspections_between(days[0], days[-1]):
        members = duty_user_ids(insp) & wanted
        if not members:
            continue
        covered = set(duty_days(insp)) & set(days)
        for uid in members:
            for d in covered:
                out.setdefault((uid, d), insp)
    return out


def duty_note(inspection):
    return f"Official duty: QA visit {inspection.reference}"


@transaction.atomic
def sync_visit_duty(inspection):
    """Write ON_DUTY for every confirmed member and visit day; remove what no
    longer applies (dates moved, member dropped, visit cancelled). A real
    check-in, approved leave or a holiday on that day is never replaced."""
    from leave.services import _restore_absence

    days = set(duty_days(inspection))
    users = duty_user_ids(inspection) if days else set()
    wanted = {(u, d) for u in users for d in days}

    linked = AttendanceRecord.objects.filter(duty_inspection=inspection, status=AttendanceRecord.ON_DUTY)
    removed = 0
    for rec in list(linked):
        if (rec.user_id, rec.date) not in wanted:
            user, day = rec.user, rec.date
            rec.delete()
            removed += 1
            _restore_absence(user, day)

    written = 0
    if wanted:
        existing = {(r.user_id, r.date): r for r in AttendanceRecord.objects.filter(
            user_id__in=users, date__in=days)}
        note = duty_note(inspection)
        for uid, day in sorted(wanted):
            rec = existing.get((uid, day))
            if rec is None:
                AttendanceRecord.objects.create(user_id=uid, date=day, status=AttendanceRecord.ON_DUTY,
                                                source=AttendanceRecord.SOURCE_VISIT, duty_inspection=inspection,
                                                notes=note)
                written += 1
            elif rec.status == AttendanceRecord.ABSENT:
                rec.status = AttendanceRecord.ON_DUTY
                rec.source = AttendanceRecord.SOURCE_VISIT
                rec.duty_inspection = inspection
                rec.notes = note
                rec.save(update_fields=["status", "source", "duty_inspection", "notes", "updated_at"])
                written += 1
    return written, removed


def sync_for_inspection_id(pk):
    Inspection = _inspection_model()
    insp = Inspection.unscoped.filter(pk=pk).first()
    if insp is not None:
        sync_visit_duty(insp)


def resync_recent(days_back=60, days_ahead=120):
    """Nightly safety net: re-sync every visit around today (signals miss
    queryset .update() calls)."""
    today = timezone.localdate()
    n = 0
    for insp in inspections_between(today - timedelta(days=days_back), today + timedelta(days=days_ahead)):
        sync_visit_duty(insp)
        n += 1
    # Cancelled visits drop their duty days.
    Inspection = _inspection_model()
    for insp in Inspection.unscoped.filter(status="cancelled", pk__in=AttendanceRecord.objects.filter(
            status=AttendanceRecord.ON_DUTY).values("duty_inspection_id")):
        sync_visit_duty(insp)
    return n
