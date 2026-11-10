"""v2.3: one honorarium file per institution, SOP-01 -> SOP-03.

Owner decision (5 Nov 2026): once work on an institution completes, its
honoraria generate themselves, whether or not the Director of Finance (DoF)
scheduled anything:

* SOP-01 — every document review approved by the director earns the
  internal officer ``honoraria.rate.internal_item`` (HonorariumEntry,
  accrued by ``assignments.work.complete``). When ALL documents of the
  application are cleared (``onboarding_v2.maybe_clear``) the stage is
  complete and the file is announced.
* SOP-02 — each SSQ review earns ``honoraria.rate.internal_item`` (staff) or
  ``honoraria.rate.external_ssq_review`` (resource person). The stage is
  announced when no SSQ review on the institution is still open.
* SOP-03 — the visit personnel on the case cost sheet, per day at the state
  rates frozen on each CostLine, plus academic resource persons per visit
  (``honoraria.rate.academic_resource_person``). Announced when the visit
  closes or the case completes.

All lines land on the institution's ONE file (``HonorariumFile``); unpaid lines
from a later stage add to it and paid lines stay as history. Each work item
is added once (``HonorariumFileLine.source_key`` is unique), so re-running a
sync never double counts.

Paying: the DoF names a pay officer once (standing) or per file. The pay
officer ticks ready lines and prepares a batch (a PaymentSchedule draft) —
seeing MASKED bank numbers only. The DoF approves with one click, which
records the payment (ledger + audit, ``schedules.mark_paid``) and tells every
payee "Paid — please confirm receipt". Payees confirm on My earnings
(``schedules.confirm_receipt``) and the file updates.
"""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal

import config
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (HonorariumEntry, HonorariumFile, HonorariumFileLine, HonorariumServiceRecordClaim,
                     PaymentSchedule, PaymentScheduleLine, PayOfficerDelegation)

logger = logging.getLogger(__name__)

FL = HonorariumFileLine
SOP01, SOP02, SOP03 = FL.SOP01, FL.SOP02, FL.SOP03
STAGE_LABELS = dict(FL.STAGE_CHOICES)
STAGE_SHORT = {SOP01: "SOP-01", SOP02: "SOP-02", SOP03: "SOP-03"}
ENTRY_STAGE = {HonorariumEntry.DOCUMENT_ANALYSIS: SOP01, HonorariumEntry.SSQ_REVIEW: SOP02,
               HonorariumEntry.SSQ_REVIEW_EXTERNAL: SOP02}


def _truthy(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------------
def is_dof(user):
    from .schedules import can_approve

    return can_approve(user)


def standing_officer():
    row = PayOfficerDelegation.objects.filter(is_active=True).select_related("officer", "assigned_by").first()
    return row if row is not None and row.officer.is_active else None


def officer_for(file):
    """The pay officer for ``file``: the one the DoF named on it, else the standing one."""
    if file.pay_officer_id and file.pay_officer.is_active:
        return file.pay_officer
    row = standing_officer()
    return row.officer if row else None


def is_pay_officer(user, file=None):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if file is not None:
        officer = officer_for(file)
        return bool(officer and officer.pk == user.pk)
    row = standing_officer()
    return bool((row and row.officer_id == user.pk)
                or HonorariumFile.objects.filter(pay_officer=user).exists())


def can_view_files(user):
    from .schedules import can_view_all

    return bool(can_view_all(user) or is_pay_officer(user))


def can_view_file(user, file):
    from .schedules import can_view_all

    return bool(can_view_all(user) or is_pay_officer(user, file))


def can_prepare(user, file):
    """The DoF, or the pay officer of this file."""
    return bool(is_dof(user) or is_pay_officer(user, file))


def pay_officer_candidates():
    """Active staff of the Finance department, plus holders of the Pay Officer
    permission (``accreditation.release_funds``)."""
    from accounts.models import User
    from payments.services import finance_department

    dept = finance_department()
    qs = User.objects.filter(is_active=True, user_type="staff")
    cond = Q(pk__in=[])
    if dept is not None:
        cond |= Q(department=dept) | Q(staff_profile__department=dept)
    cond |= Q(user_permissions__codename="release_funds") | Q(groups__permissions__codename="release_funds") \
        | Q(role__groups__permissions__codename="release_funds")
    try:
        return list(qs.filter(cond).distinct().order_by("first_name", "last_name", "email"))
    except Exception:  # a role model without groups: fall back to the department only
        cond = Q(department=dept) | Q(staff_profile__department=dept) if dept is not None else Q(pk__in=[])
        return list(qs.filter(cond).distinct().order_by("first_name", "last_name", "email"))


# ---------------------------------------------------------------------------
# The file and its lines
# ---------------------------------------------------------------------------
def file_for(institution):
    file = HonorariumFile.objects.filter(institution=institution).first()
    if file is not None:
        return file
    from . import references

    with transaction.atomic():
        file, _ = HonorariumFile.objects.get_or_create(
            institution=institution,
            defaults={"reference": references.issue(
                "honoraria_file", unique_check=lambda ref: HonorariumFile.objects.filter(reference=ref).exists())})
    return file


def _name(user):
    return (user.get_full_name() or user.email) if user is not None else ""


def _role_of(user):
    role = getattr(user, "role", None)
    return getattr(role, "name", "") or ""


def _entry_candidates(institution):
    for e in (HonorariumEntry.objects.filter(institution=institution).exclude(status=HonorariumEntry.VOIDED)
              .select_related("payee_user", "payee_user__role")):
        stage = ENTRY_STAGE.get(e.kind)
        if stage is None:
            continue
        rp = None
        if e.kind == HonorariumEntry.SSQ_REVIEW_EXTERNAL:
            from inspection.models import ResourcePerson

            rp = ResourcePerson.objects.filter(user=e.payee_user).first()
        yield {
            "source_key": f"entry:{e.pk}", "stage": stage, "entry": e, "payee_user": e.payee_user,
            "resource_person": rp, "name": _name(e.payee_user),
            "role": "External resource person" if rp else (_role_of(e.payee_user) or "Internal officer"),
            "work_item": _entry_work_item(e), "rate_key": e.rate_key, "quantity": Decimal(e.quantity),
            "rate": e.rate, "amount": e.amount, "accrued_at": e.accrued_at,
        }


def _entry_work_item(entry):
    label = entry.get_kind_display()
    try:
        source = entry.source
    except Exception:
        source = None
    subject = None
    if source is not None:
        subject_obj = getattr(source, "subject", None)
        kind = getattr(subject_obj, "kind", None)
        subject = getattr(kind, "name", None) or (str(subject_obj) if subject_obj is not None else None)
    return f"{label} — {subject}"[:255] if subject else label


def _visit_done(case):
    if case.completed_at is not None:
        return True
    from inspection.models import Inspection

    return Inspection.unscoped.filter(case=case, closed_at__isnull=False).exists()


def _sop03_candidates(institution):
    from accreditation.models import CostLine, QACase

    for case in QACase.unscoped.filter(institution=institution).order_by("pk"):
        if not _visit_done(case):
            continue
        lines = (CostLine.unscoped.filter(sheet__case=case, sheet__is_current=True, staff__isnull=False,
                                          amount__gt=0)
                 .select_related("staff__user", "staff__user__role", "rate", "sheet").order_by("sort_order", "pk"))
        for cl in lines:
            user = getattr(cl.staff, "user", None)
            if user is None:
                continue
            days = Decimal(cl.days or 0)
            unit = cl.unit_amount or (cl.amount / days if days else cl.amount)
            per = "day" if getattr(cl.rate, "unit", "per_day") == "per_day" else "trip"
            qty_text = f"{days.normalize():f} {per}{'s' if days != 1 else ''}"
            yield {
                "source_key": f"cost:{cl.pk}", "stage": SOP03, "cost_line": cl, "case": case, "payee_user": user,
                "resource_person": None, "name": _name(user), "role": cl.role_on_exercise or "Visit team",
                "work_item": f"{cl.get_kind_display()} · {qty_text} · {case.reference}"[:255],
                "rate_key": f"rate_table:{cl.rate.code}" if cl.rate_id else "", "quantity": days,
                "rate": unit, "amount": cl.amount,
                "accrued_at": case.completed_at or cl.sheet.approved_at or timezone.now(),
            }
    from inspection.models import ServiceRecord

    from .schedules import academic_rate

    rate = academic_rate()
    records = (ServiceRecord.unscoped.filter(institution=institution, resource_person__isnull=False,
                                             visit_count__gt=0, voided_by__isnull=True)
               .select_related("resource_person", "resource_person__user", "case", "programme"))
    for rec in records:
        rp = rec.resource_person
        what = " · ".join(filter(None, [rec.get_duty_display(), rec.programme.name if rec.programme_id else "",
                                         rec.case.reference if rec.case_id else ""]))
        yield {
            "source_key": f"svc:{rec.pk}", "stage": SOP03, "service_record": rec, "case": rec.case,
            "payee_user": rp.user, "resource_person": rp, "name": rp.full_name,
            "role": "Academic resource person", "work_item": what[:255],
            "rate_key": "honoraria.rate.academic_resource_person", "quantity": Decimal(rec.visit_count),
            "rate": rate, "amount": rate * rec.visit_count,
            "accrued_at": timezone.make_aware(datetime.datetime.combine(rec.served_to, datetime.time.min))
            if rec.served_to else timezone.now(),
        }


def candidates(institution):
    yield from _entry_candidates(institution)
    yield from _sop03_candidates(institution)


@transaction.atomic
def sync(institution, *, stage=None, announce=False, actor=None):
    """Add every payable work item of ``institution`` not yet on its file
    (idempotent), refresh line statuses, and — when ``announce`` — tell the
    DoF, the ES, super admins and the pay officer about lines not yet
    announced. Returns (file, new_lines)."""
    if not _truthy(config.get("honoraria.enabled", True)):
        return None, []
    items = list(candidates(institution))
    file = HonorariumFile.objects.filter(institution=institution).first()
    if file is None and not items:
        return None, []
    file = file or file_for(institution)
    file = HonorariumFile.objects.select_for_update().get(pk=file.pk)
    known = set(FL.objects.filter(source_key__in=[i["source_key"] for i in items])
                .values_list("source_key", flat=True))
    new = []
    for item in items:
        if item["source_key"] in known:
            continue
        line = FL.objects.create(file=file, **item)
        new.append(line)
    reconcile(file)
    if new:
        latest = max(new, key=lambda line: line.stage).stage
        file.last_stage = max(filter(None, [file.last_stage, latest]))
        file.save(update_fields=["last_stage", "updated_at"])
        from core import audit

        audit.log(audit._action("state_change"), file, actor=actor,
                  changes={"lines_added": {"new": [f"{line.name}: {line.amount}" for line in new][:50]}},
                  note=f"honorarium file: {len(new)} line(s) added")
    if announce:
        announce_ready(file, stage=stage)
    return file, new


def announce_ready(file, *, stage=None):
    """Notify about payable lines not yet announced (once per line)."""
    lines = list(file.lines.filter(announced_at__isnull=True, status=FL.PENDING))
    if not lines:
        return 0
    now = timezone.now()
    FL.objects.filter(pk__in=[line.pk for line in lines]).update(announced_at=now)
    HonorariumFile.objects.filter(pk=file.pk).update(last_announced_at=now)
    stage = stage or max(line.stage for line in lines)
    total = sum((line.amount for line in lines), Decimal("0"))
    people = len({line.payee_key for line in lines})
    inst = file.institution
    title = f"Honoraria ready to settle — {inst.name} ({STAGE_SHORT.get(stage, stage)})"
    body = (f"{len(lines)} payable item(s) for {people} person(s), ₦{total:,.2f}, were added to "
            f"{file.reference}. Open the file to prepare and approve the payment.")
    url = file_url(file)
    recipients = recipients_for_ready(file)
    transaction.on_commit(lambda: _send(recipients, title, body, url))
    return len(lines)


def recipients_for_ready(file):
    import org
    from payments.services import final_approvers

    people = list(final_approvers())
    people += list(org.users_with_role("executive-secretary", staff_only=True))
    people += list(org.users_with_role("super-admin"))
    officer = officer_for(file)
    if officer is not None:
        people.append(officer)
    seen, out = set(), []
    for p in people:
        if p is not None and p.pk not in seen and p.is_active:
            seen.add(p.pk)
            out.append(p)
    return out


def _send(users, title, body, url, *, level="info", email=True):
    from notifications.models import Notification
    from notifications.services import notify

    for user in users:
        try:
            notify(user, title=title, body=body, url=url, category=Notification.QA, level=level)
        except Exception:
            logger.exception("honoraria notification failed")
    if email:
        try:
            from django.utils.html import escape

            from qa_core.notify import _email

            _email(title, f"<p>{escape(body)}</p><p><a href=\"{escape(url)}\">Open</a></p>", users)
        except Exception:
            logger.exception("honoraria e-mail failed")


def file_url(file):
    from django.urls import reverse

    return reverse("honoraria_sched:file", args=[file.pk])


# ---------------------------------------------------------------------------
# Status: lines follow their payment (wherever it was prepared)
# ---------------------------------------------------------------------------
def _schedule_line_of(line):
    if line.schedule_line_id and line.schedule_line.schedule.status != "cancelled":
        return line.schedule_line
    if line.entry_id:
        sl = (PaymentScheduleLine.objects.filter(entries=line.entry_id).exclude(schedule__status="cancelled")
              .select_related("schedule").order_by("-pk").first())
        if sl is not None:
            return sl
    if line.service_record_id:
        claim = (HonorariumServiceRecordClaim.objects.filter(
            service_record_id=line.service_record_id,
            status__in=(HonorariumServiceRecordClaim.RESERVED, HonorariumServiceRecordClaim.PAID))
                 .select_related("line__schedule").first())
        if claim is not None:
            return claim.line
    return None


def _fund_release_settles(line):
    if line.stage != SOP03 or not line.cost_line_id or not line.case_id:
        return None
    if not _truthy(config.get("honoraria.files.fund_release_settles_visit", True)):
        return None
    from accreditation.models import FundRelease

    return FundRelease.unscoped.filter(case_id=line.case_id, status="released").order_by("-released_at").first()


def status_of(line):
    if line.entry_id and line.entry and line.entry.status == HonorariumEntry.VOIDED:
        return FL.VOIDED, None
    sl = _schedule_line_of(line)
    if sl is not None:
        if sl.confirmed_at:
            return FL.CONFIRMED, sl
        if sl.paid_at:
            return FL.PAID, sl
        if sl.schedule.status == "approved":
            return FL.APPROVED, sl
        return FL.PREPARED, sl
    if line.entry_id and line.entry:
        if line.entry.status == HonorariumEntry.RECEIVED:
            return FL.CONFIRMED, None
        if line.entry.status == HonorariumEntry.PAID:
            return FL.PAID, None
    release = _fund_release_settles(line)
    if release is not None:
        return FL.SETTLED, release
    return FL.PENDING, None


def reconcile(file):
    changed = 0
    for line in file.lines.select_related("entry", "schedule_line__schedule"):
        status, link = status_of(line)
        fields = []
        if status != line.status:
            line.status = status
            fields.append("status")
        if isinstance(link, PaymentScheduleLine) and line.schedule_line_id != link.pk:
            line.schedule_line = link
            fields.append("schedule_line")
        if status == FL.SETTLED and link is not None:
            note = f"Paid through case fund release {link.released_reference or link.pk}"[:255]
            if line.note != note:
                line.note = note
                fields.append("note")
        if fields:
            line.save(update_fields=fields + ["updated_at"])
            changed += 1
    return changed


def summary(file, lines=None):
    """Totals by status and stage for one file."""
    lines = list(lines if lines is not None else file.lines.all())
    live = [line for line in lines if line.status != FL.VOIDED]
    by_status = {code: Decimal("0") for code, _ in FL.STATUS_CHOICES}
    counts = {code: 0 for code, _ in FL.STATUS_CHOICES}
    for line in live:
        by_status[line.status] += line.amount
        counts[line.status] += 1
    stages = []
    for code, label in FL.STAGE_CHOICES:
        rows = [line for line in live if line.stage == code]
        stages.append({"code": code, "label": label, "short": STAGE_SHORT[code], "lines": rows,
                       "total": sum((r.amount for r in rows), Decimal("0")),
                       "owed": sum((r.amount for r in rows if r.status in FL.OUTSTANDING), Decimal("0")),
                       "people": len({r.payee_key for r in rows})})
    total = sum((line.amount for line in live), Decimal("0"))
    outstanding = sum((by_status[s] for s in FL.OUTSTANDING), Decimal("0"))
    done = by_status[FL.PAID] + by_status[FL.CONFIRMED] + by_status[FL.SETTLED]
    phase = _phase(counts)
    return {"total": total, "outstanding": outstanding, "pending": by_status[FL.PENDING],
            "prepared": by_status[FL.PREPARED] + by_status[FL.APPROVED], "paid": by_status[FL.PAID],
            "confirmed": by_status[FL.CONFIRMED] + by_status[FL.SETTLED], "done": done,
            "counts": counts, "stages": stages, "people": len({line.payee_key for line in live}),
            "lines": len(live), "percent": int(done * 100 / total) if total else 0, "phase": phase}


PHASES = [("generated", "Generated"), ("prepared", "Prepared"), ("approved", "Approved"), ("paid", "Paid"),
          ("confirmed", "Confirmed")]


def _phase(counts):
    """Where the file stands on generated -> prepared -> approved -> paid -> confirmed:
    the earliest step any live line is still on."""
    if counts[FL.PENDING]:
        return "generated"
    if counts[FL.PREPARED]:
        return "prepared"
    if counts[FL.APPROVED]:
        return "approved"
    if counts[FL.PAID]:
        return "paid"
    if counts[FL.CONFIRMED] or counts[FL.SETTLED]:
        return "confirmed"
    return "generated"


def steps(phase):
    keys = [k for k, _ in PHASES]
    at = keys.index(phase)
    complete_all = phase == "confirmed"
    return [{"key": k, "label": label, "done": i < at or complete_all, "current": i == at and not complete_all}
            for i, (k, label) in enumerate(PHASES)]


# ---------------------------------------------------------------------------
# Bank status (masked only)
# ---------------------------------------------------------------------------
def bank_status(lines):
    """Attach ``line.bank`` (the active account, masked use only) and
    ``line.bank_ok`` (confirmed by its owner) to each line."""
    from .finance_view import _accounts_for_rows

    rows = [{"user": line.payee_user, "resource_person": line.resource_person} for line in lines]
    pick = _accounts_for_rows(rows)
    for line in lines:
        acc = pick(line.payee_user, line.resource_person)
        line.bank = acc
        line.bank_ok = bool(acc and acc.confirmed_at)
    return lines


# ---------------------------------------------------------------------------
# Pay officer
# ---------------------------------------------------------------------------
@transaction.atomic
def assign_pay_officer(officer, *, user, file=None, standing=False):
    """The DoF names who prepares payments: for one file, or standing for all."""
    if not is_dof(user):
        raise PermissionDenied("Only the Director of Finance can assign a pay officer.")
    if officer is None or not officer.is_active or getattr(officer, "user_type", "staff") != "staff":
        raise ValidationError("Choose an active member of staff.")
    if officer.pk not in {u.pk for u in pay_officer_candidates()}:
        raise ValidationError("Choose a Finance officer (Finance department or Pay Officer permission).")
    from core import audit

    now = timezone.now()
    if standing:
        for row in PayOfficerDelegation.objects.select_for_update().filter(is_active=True):
            row.is_active, row.ended_at = False, now
            row.save(update_fields=["is_active", "ended_at", "updated_at"])
        row = PayOfficerDelegation.objects.create(officer=officer, assigned_by=user)
        audit.log(audit._action("state_change"), row, actor=user,
                  changes={"pay_officer": {"new": officer.pk}}, note="standing honoraria pay officer")
    if file is not None:
        old = file.pay_officer_id
        file.pay_officer, file.pay_officer_assigned_by, file.pay_officer_assigned_at = officer, user, now
        file.save(update_fields=["pay_officer", "pay_officer_assigned_by", "pay_officer_assigned_at", "updated_at"])
        audit.log(audit._action("state_change"), file, actor=user,
                  changes={"pay_officer": {"old": old, "new": officer.pk}}, note="honoraria pay officer")
        _open_work(file, officer, user)
    else:
        for f in HonorariumFile.objects.filter(pay_officer__isnull=True, lines__status=FL.PENDING).distinct():
            _open_work(f, officer, user)
    title = "You prepare honoraria payments" if standing or file is None else \
        f"Prepare honoraria — {file.institution.name}"
    body = (f"{_name(user)} asked you to prepare honoraria payments"
            + (f" for {file.institution.name}." if file is not None else " for every institution's file.")
            + " Tick who to pay and prepare the batch; the Director of Finance approves it.")
    url = file_url(file) if file is not None else _files_url()
    transaction.on_commit(lambda: _send([officer], title, body, url, email=False))
    return officer


def _files_url():
    from django.urls import reverse

    return reverse("honoraria_sched:files")


def _open_work(file, officer, by):
    """Put the file in the pay officer's My work (WorkAssignment ``honoraria_schedule``)."""
    try:
        from assignments import work
        from assignments.models import WorkAssignment
        from payments.services import finance_department

        with transaction.atomic():
            work.assign(file.institution, WorkAssignment.HONORARIA_SCHEDULE, officer, subject=file,
                        department=finance_department(), assigned_by=by, role_on_work="analyst",
                        accept_now=True, notify_assignee=False)
    except Exception:
        logger.warning("could not open the pay officer's work item for %s", file.reference, exc_info=True)


def _close_work(file):
    try:
        from assignments import work
        from assignments.models import WorkAssignment

        for wa in WorkAssignment.objects.for_subject(file).filter(
                kind=WorkAssignment.HONORARIA_SCHEDULE, status__in=WorkAssignment.OPEN):
            work.complete(wa, None)
    except Exception:
        logger.warning("could not close the pay officer's work item for %s", file.reference, exc_info=True)


# ---------------------------------------------------------------------------
# Prepare (pay officer or DoF) -> approve and pay (DoF, one click)
# ---------------------------------------------------------------------------
@transaction.atomic
def prepare_batch(file, *, user, line_ids, funding_account_id=None, payment_reference=""):
    if not can_prepare(user, file):
        raise PermissionDenied("Only the pay officer of this file or the Director of Finance can prepare payment.")
    file = HonorariumFile.objects.select_for_update().get(pk=file.pk)
    reconcile(file)
    try:
        ids = {int(v) for v in line_ids}
    except (TypeError, ValueError):
        raise ValidationError("Choose the people to pay.")
    lines = list(file.lines.select_for_update().filter(pk__in=ids)
                 .select_related("entry", "payee_user", "resource_person", "service_record"))
    if not lines or len(lines) != len(ids):
        raise ValidationError("Choose one or more lines that are ready to pay.")
    if any(line.status != FL.PENDING for line in lines):
        raise ValidationError("Some of the chosen lines are already in a batch or paid. Refresh and try again.")
    bank_status(lines)
    missing = [line.name for line in lines if not line.bank_ok]
    if missing:
        raise ValidationError("Confirmed bank details are missing for: " + ", ".join(sorted(set(missing))[:6]) + ".")
    funding = None
    if funding_account_id not in (None, ""):
        from payments.models import BankAccount

        try:
            funding = BankAccount.objects.filter(pk=int(funding_account_id), is_active=True,
                                                 is_placeholder=False).first()
        except (TypeError, ValueError):
            funding = None
        if funding is None:
            raise ValidationError("Choose an active NBTE bank account to pay from.")
    payment_reference = str(payment_reference or "").strip()[:100]
    from . import references

    today = timezone.localdate()
    start = min(timezone.localdate(line.accrued_at) for line in lines)
    schedule = PaymentSchedule.objects.create(
        reference=references.issue(
            "honoraria", unique_check=lambda ref: PaymentSchedule.objects.filter(reference=ref).exists()),
        title=f"Honoraria — {file.institution.name}"[:200], period_start=min(start, today), period_end=today,
        created_by=user, honorarium_file=file, funding_account=funding, payment_reference=payment_reference,
        notes=f"Prepared from {file.reference} by {_name(user)}.")
    from .schedules import _recompute, _reserve_service_records

    for line in lines:
        qty = line.quantity
        whole = qty == qty.to_integral_value() and qty > 0
        sl = PaymentScheduleLine.objects.create(
            schedule=schedule, user=line.payee_user, resource_person=line.resource_person, name=line.name[:150],
            role=f"{STAGE_SHORT[line.stage]} · {line.work_item}"[:150],
            source=PaymentScheduleLine.ACADEMIC if line.resource_person_id else PaymentScheduleLine.INTERNAL,
            quantity=int(qty) if whole else 1, rate=line.rate if whole else line.amount,
            note=("" if whole else f"{qty} × ₦{line.rate:,.2f}")[:255])
        if sl.amount != line.amount:  # never let rounding change what the file says is owed
            PaymentScheduleLine.objects.filter(pk=sl.pk).update(amount=line.amount)
        if line.entry_id:
            updated = HonorariumEntry.objects.filter(pk=line.entry_id, status=HonorariumEntry.ACCRUED).update(
                schedule=schedule, status=HonorariumEntry.SCHEDULED, updated_at=timezone.now())
            if not updated:
                raise ValidationError(f"{line.name}'s item is already on another payment schedule.")
            sl.entries.set([line.entry_id])
        if line.service_record_id:
            _reserve_service_records(sl, [line.service_record])
        line.schedule_line, line.status = sl, FL.PREPARED
        line.save(update_fields=["schedule_line", "status", "updated_at"])
    _recompute(schedule)
    from core import audit

    audit.log(audit._action("state_change"), schedule, actor=user,
              changes={"status": {"new": "draft"}, "lines": {"new": [f"{x.name}: {x.amount}" for x in lines]}},
              note=f"honoraria batch prepared from {file.reference}")
    if not is_dof(user):
        from payments.services import final_approvers

        title = f"Approve honoraria — {file.institution.name}"
        body = (f"{_name(user)} prepared {schedule.reference}: {len(lines)} item(s), ₦{schedule.total:,.2f}. "
                "Approve it to record the payment and tell each payee.")
        url = file_url(file)
        approvers = list(final_approvers())
        transaction.on_commit(lambda: _send(approvers, title, body, url, level="warning"))
    return schedule


def can_approve_batch(user, schedule):
    return bool(is_dof(user) and schedule.honorarium_file_id and schedule.status == "draft")


@transaction.atomic
def approve_and_pay(schedule, *, user, payment_reference="", funding_account_id=None):
    """The DoF's one click: approve the prepared batch and record it paid.
    ``schedules.mark_paid`` posts each line to the ledger, writes the audit
    entry and tells each payee "Paid — please confirm receipt"."""
    from . import schedules as sched

    if not is_dof(user):
        raise PermissionDenied("Only the Director of Finance can approve honoraria.")
    schedule = PaymentSchedule.objects.select_for_update().get(pk=schedule.pk)
    if schedule.honorarium_file_id is None:
        raise ValidationError("This schedule was not prepared from an honorarium file.")
    if schedule.status != "draft":
        raise ValidationError("This batch has already been decided.")
    funding = funding_account_id or schedule.funding_account_id
    if not funding:
        from payments.models import BankAccount

        accounts = list(BankAccount.objects.filter(is_active=True, is_placeholder=False)[:2])
        if len(accounts) == 1:
            funding = accounts[0].pk
    if not funding:
        raise ValidationError("Choose the NBTE bank account the payment was made from.")
    reference = (str(payment_reference or "").strip() or schedule.payment_reference or schedule.reference)
    sched.approve(schedule, user=user)
    sched.mark_paid(schedule, user=user, funding_account_id=funding, payment_reference=reference)
    file = schedule.honorarium_file
    reconcile(file)
    from core import audit

    audit.log(audit._action("state_change"), file, actor=user,
              changes={"batch": {"new": schedule.reference}}, note="honoraria batch approved and paid")
    officer = schedule.created_by
    if officer is not None and officer.pk != user.pk:
        title = f"Approved and paid — {file.institution.name}"
        body = f"{_name(user)} approved {schedule.reference} (₦{schedule.total:,.2f}). Payees were asked to confirm."
        url = file_url(file)
        transaction.on_commit(lambda: _send([officer], title, body, url, email=False))
    if not file.lines.filter(status__in=FL.OUTSTANDING).exists():
        _close_work(file)
    return schedule


@transaction.atomic
def cancel_batch(schedule, *, user, reason=""):
    """Withdraw a prepared (draft) batch: its lines become ready to pay again."""
    if not (is_dof(user) or (schedule.created_by_id == getattr(user, "pk", None)
                             and schedule.honorarium_file_id and can_prepare(user, schedule.honorarium_file))):
        raise PermissionDenied("Only the Director of Finance or whoever prepared it can withdraw this batch.")
    schedule = PaymentSchedule.objects.select_for_update().get(pk=schedule.pk)
    if schedule.status != "draft":
        raise ValidationError("Only a batch waiting for approval can be withdrawn.")
    schedule.status = "cancelled"
    schedule.save(update_fields=["status", "updated_at"])
    HonorariumEntry.objects.filter(schedule=schedule, status=HonorariumEntry.SCHEDULED).update(
        schedule=None, status=HonorariumEntry.ACCRUED, updated_at=timezone.now())
    HonorariumServiceRecordClaim.objects.filter(line__schedule=schedule,
                                                status=HonorariumServiceRecordClaim.RESERVED).update(
        status=HonorariumServiceRecordClaim.RELEASED, updated_at=timezone.now())
    FL.objects.filter(schedule_line__schedule=schedule).update(schedule_line=None, status=FL.PENDING)
    from core import audit

    audit.log(audit._action("state_change"), schedule, actor=user,
              changes={"status": {"old": "draft", "new": "cancelled"}}, note=(reason or "batch withdrawn")[:255])
    if schedule.honorarium_file_id:
        reconcile(schedule.honorarium_file)
    return schedule


# ---------------------------------------------------------------------------
# Stage hooks (never break the workflow that calls them)
# ---------------------------------------------------------------------------
def _safe_sync(institution_id, stage, announce):
    from institutions.models import Institution

    try:
        inst = Institution.unscoped.filter(pk=institution_id).first()
        if inst is None:
            return
        with transaction.atomic():
            sync(inst, stage=stage, announce=announce)
    except Exception:
        logger.exception("honorarium file sync failed for institution %s (%s)", institution_id, stage)


def stage_completed(institution, stage):
    """A stage of ``institution`` finished: after the commit, add its payable
    work to the file and announce it. Safe to call from any workflow."""
    try:
        inst_id = getattr(institution, "pk", institution)
        if inst_id:
            transaction.on_commit(lambda: _safe_sync(inst_id, stage, True))
    except Exception:
        logger.exception("could not schedule the honorarium file sync")


def entry_accrued(entry):
    """An honorarium accrued (document or SSQ review completed). SOP-01 lines
    wait for approval in principle to be announced; SOP-02 is announced when
    the institution's last open SSQ review is done."""
    try:
        if not entry.institution_id:
            return
        stage = ENTRY_STAGE.get(entry.kind)
        inst_id = entry.institution_id

        def run():
            announce = False
            if stage == SOP02:
                from assignments.models import WorkAssignment

                announce = not WorkAssignment.objects.filter(
                    institution_id=inst_id, kind=WorkAssignment.SSQ_REVIEW,
                    status__in=WorkAssignment.OPEN).exists()
            elif stage == SOP01:
                # SOP-01 was already completed (documents cleared) and announced:
                # a late document review joins and is announced too.
                announce = FL.objects.filter(file__institution_id=inst_id, stage=SOP01,
                                             announced_at__isnull=False).exists()
            _safe_sync(inst_id, stage, announce)

        transaction.on_commit(run)
    except Exception:
        logger.exception("could not schedule the honorarium file sync for an entry")


def sync_unfiled():
    """Bring in work done before files existed (and anything a hook missed):
    institutions with live honoraria entries not on a file yet."""
    filed = FL.objects.filter(entry__isnull=False).values_list("entry_id", flat=True)
    inst_ids = set(HonorariumEntry.objects.exclude(status=HonorariumEntry.VOIDED)
                   .exclude(pk__in=filed).exclude(institution__isnull=True)
                   .values_list("institution_id", flat=True))
    from institutions.models import Institution

    added = 0
    for inst in Institution.unscoped.filter(pk__in=inst_ids):
        try:
            with transaction.atomic():
                _file, new = sync(inst, announce=False)
            added += len(new)
        except Exception:
            logger.exception("honorarium file sync failed for %s", inst.pk)
    return added
