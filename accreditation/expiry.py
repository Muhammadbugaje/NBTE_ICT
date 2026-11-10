"""Programme expiry and renewal reminders — Phase 8 (Master Plan Phase 6;
Requirement 4; ``qa.programme.renewal_reminder_days``).

Run daily (``manage.py qa_programme_expiry``; Celery beat
``accreditation.programme_expiry``):

* a programme whose approval has passed its ``expiry_date`` becomes
  ``expired`` (the programme status vocabulary; programmes have no flow);
* the institution and its handling department are reminded at each threshold
  of ``qa.programme.renewal_reminder_days`` (default 180, 90, 30 and 7 days
  before expiry) — each threshold once per expiry date. A programme first seen
  inside several thresholds gets only the most urgent reminder;
* every step writes a timeline event (public for the institution, internal
  for NBTE).

Idempotent: running it twice on the same day sends nothing new.
"""
# scoped-by-caller
from __future__ import annotations

import json

from django.db import transaction
from django.utils import timezone

import config
from qa_core import timeline

ACTIVE_STATUSES = ("accredited", "interim", "approval_to_commence", "approved_pending_body_letter")


def reminder_days():
    value = config.get("qa.programme.renewal_reminder_days", [180, 90, 30, 7])
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = [180, 90, 30, 7]
    out = []
    for item in value or []:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return sorted({d for d in out if d >= 0}, reverse=True)


def _staff(inst):
    from institutions.services import staff_in_department

    return staff_in_department(inst.handling_department, perm="accreditation.view_qa_case") \
        if inst.handling_department_id else []


def _notify(programme, event, staff_event, context):
    from institutions.services import notify_institution, notify_role

    inst = programme.institution
    ctx = {"programme": str(programme), "institution": inst.name, **context}
    transaction.on_commit(lambda: notify_institution(inst, event, ctx, url="/programmes/"))
    users = _staff(inst)
    if users:
        transaction.on_commit(lambda: notify_role(staff_event, ctx, users, url=f"/qa/institutions/{inst.code}/"))


def _expire(programme, now):
    from institutions.models import Programme

    before = programme.get_status_display()
    Programme.unscoped.filter(pk=programme.pk).update(status="expired", expired_at=now, updated_at=now)
    timeline.record(programme.institution, "programme.expired", subject=programme, visibility="public",
                    public_text=f"{programme}: the approval ended on {programme.expiry_date:%d %b %Y}. "
                                "Request re-accreditation from the Programmes page.",
                    internal_text=f"{programme} {before} → Expired (expiry date {programme.expiry_date:%d %b %Y}).")
    _notify(programme, "programme.expired", "programme.expired_staff",
            {"expiry": programme.expiry_date.strftime("%d %b %Y")})


def _remind(programme, threshold, days_left, sent):
    from institutions.models import Programme

    Programme.unscoped.filter(pk=programme.pk).update(renewal_reminders_sent=sent, updated_at=timezone.now())
    timeline.record(programme.institution, "programme.renewal_reminder", subject=programme, visibility="public",
                    public_text=f"Reminder: {programme} expires on {programme.expiry_date:%d %b %Y} "
                                f"({days_left} days). Request re-accreditation from the Programmes page.",
                    internal_text=f"Renewal reminder ({threshold}-day threshold) sent for {programme}.")
    _notify(programme, "programme.renewal_due", "programme.renewal_due_staff",
            {"expiry": programme.expiry_date.strftime("%d %b %Y"), "days": days_left})


@transaction.atomic
def run(today=None, *, dry_run=False):
    """Returns {"expired": [...], "reminded": [(programme, threshold)], "checked": n}."""
    from institutions.models import Programme

    today = today or timezone.localdate()
    now = timezone.now()
    thresholds = reminder_days()
    out = {"expired": [], "reminded": [], "checked": 0}
    qs = (Programme.unscoped.filter(status__in=ACTIVE_STATUSES, expiry_date__isnull=False)
          .select_related("institution", "level").select_for_update())
    for programme in qs:
        out["checked"] += 1
        if programme.expiry_date < today:
            out["expired"].append(programme)
            if not dry_run:
                _expire(programme, now)
            continue
        days_left = (programme.expiry_date - today).days
        crossed = [d for d in thresholds if days_left <= d]
        if not crossed:
            continue
        sent = list(programme.renewal_reminders_sent or [])
        stamp = programme.expiry_date.isoformat()
        new = [d for d in crossed if f"{stamp}:{d}" not in sent]
        if not new:
            continue
        most_urgent = min(new)
        sent += [f"{stamp}:{d}" for d in new]
        out["reminded"].append((programme, most_urgent))
        if not dry_run:
            _remind(programme, most_urgent, days_left, sent)
    if dry_run:
        transaction.set_rollback(True)
    return out
