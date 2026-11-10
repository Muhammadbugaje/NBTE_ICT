"""v2.1 X32–X34: honoraria payment schedules.

Work done earns a rate from settings (X32):

* ``honoraria.rate.internal_item`` — per internal SSQ / document item reviewed;
* ``honoraria.rate.academic_resource_person`` — per academic resource person
  per visit served.

A schedule is built for a period from the append-only ``ServiceRecord`` rows,
approved and paid by the Director of Finance using the logged, confirmed
payee bank details (``honoraria.disbursement``), and each payee confirms
receipt (X34). A person
sees their own lines; a director also sees their department's lines; the
Director of Finance and the ES see every line.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

import config
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import references as hon_references

from .models import HonorariumEntry, HonorariumServiceRecordClaim, PaymentSchedule, PaymentScheduleLine


def _rate(key, default):
    try:
        return Decimal(str(config.get(key, default) or default))
    except (ArithmeticError, ValueError):
        return Decimal(str(default))


def internal_rate():
    return _rate("honoraria.rate.internal_item", 10000)


def academic_rate():
    return _rate("honoraria.rate.academic_resource_person", 30000)


@transaction.atomic
def accrue_assignment(wa):
    """Create one earning for a completed payable SSQ or document review."""
    import config as config_registry

    kinds = {
        "document_analysis": (HonorariumEntry.DOCUMENT_ANALYSIS, "Document analysis"),
        "ssq_review": (HonorariumEntry.SSQ_REVIEW, "SSQ review"),
    }
    mapped = kinds.get(wa.kind)
    if mapped is None or not config_registry.get("honoraria.enabled", True):
        return None
    payee = wa.assignee
    if not getattr(payee, "is_active", False):
        return None
    rate_key = "honoraria.rate.internal_item"
    if getattr(payee, "user_type", "") == "resource_person" and wa.kind == "ssq_review":
        # v2.2: an SSQ reviewed by an external resource person is paid at the external rate.
        mapped = (HonorariumEntry.SSQ_REVIEW_EXTERNAL, "External SSQ review")
        rate_key = "honoraria.rate.external_ssq_review"
        rate = _rate(rate_key, 30000)
    elif getattr(payee, "user_type", "") != "staff":
        return None
    else:
        rate = internal_rate()
    if rate <= 0:
        return None
    from django.contrib.contenttypes.models import ContentType

    kind, label = mapped
    content_type = ContentType.objects.get_for_model(wa, for_concrete_model=False)
    entry, created = HonorariumEntry.objects.get_or_create(
        kind=kind, source_ct=content_type, source_id=str(wa.pk), payee_user=payee,
        defaults={
            "institution": wa.institution,
            "department": wa.department,
            "rate_key": rate_key,
            "quantity": 1,
            "rate": rate,
            "amount": rate,
        },
    )
    if created:
        institution_code = getattr(wa.institution, "code", "institution")
        def notify_payee():
            from notifications.services import notify

            notify(payee, title="Honorarium accrued",
                   body=f"₦{entry.amount:,.2f} for {label.lower()} at {institution_code}.",
                   url="/me/earnings/")

        transaction.on_commit(notify_payee)
        from . import files  # v2.3: the institution's honorarium file picks it up

        files.entry_accrued(entry)
    return entry


def can_view_all(user):
    from org import is_es, is_super_admin
    from payments.services import is_final_approver

    return bool(user and user.is_authenticated
                and (user.is_superuser or is_es(user) or is_super_admin(user)
                     or user.has_perm("honoraria.view_all_payment_schedules")
                     or is_final_approver(user)))


def can_approve(user):
    from payments.services import is_final_approver

    return bool(user and user.is_authenticated and is_final_approver(user))


def can_pay(user):
    from payments.services import is_final_approver

    return bool(user and user.is_authenticated and is_final_approver(user))


def can_view_finance_report(user):
    import org
    from payments.services import is_final_approver

    return bool(user and user.is_authenticated and (
        user.is_superuser or org.is_es(user) or org.is_super_admin(user) or is_final_approver(user)))


def payment_details(schedule, *, user, lines=None):
    """Release confirmed payee bank details to the Finance Director only."""
    if not can_pay(user):
        raise PermissionDenied("Only the Director of Finance may view payee bank details.")
    if schedule.status != "approved":
        raise ValidationError("Payee bank details are available only for an approved schedule.")
    from . import disbursement

    lines = list(lines if lines is not None else schedule.lines.filter(paid_at__isnull=True))
    if any(line.schedule_id != schedule.pk or line.paid_at is not None for line in lines):
        raise ValidationError("Choose only unpaid payees on this schedule.")
    users = [line.user for line in lines if line.user_id and not line.resource_person_id]
    resource_persons = [line.resource_person for line in lines if line.resource_person_id]
    released = disbursement.details_for_schedule(
        users, resource_persons=resource_persons, schedule_reference=schedule.reference, generated_by=user)
    by_line = {}
    missing = []
    for line in lines:
        key = (("resource_person", line.resource_person_id) if line.resource_person_id else line.user_id)
        details = released.get(key)
        if details is None:
            missing.append(line)
        else:
            by_line[line.pk] = details
    return by_line, missing


def _staff_department(user):
    staff = getattr(user, "staff_profile", None)
    return getattr(staff, "department_id", None) or getattr(user, "department_id", None)


def _department_user_ids(department):
    from accounts.models import User

    return User.objects.filter(Q(department_id=department) | Q(staff_profile__department_id=department)) \
        .values_list("pk", flat=True)


def lines_for(user, schedule):
    """The lines ``user`` may see on ``schedule``."""
    qs = schedule.lines.select_related("user", "resource_person")
    if can_view_all(user):
        return qs
    if not getattr(user, "is_authenticated", False):
        return qs.none()
    own = qs.filter(Q(user=user) | Q(resource_person__user=user))
    if (str(config.get("honoraria.visibility.director_sees_department", True)).strip().lower()
            in ("1", "true", "yes", "on")):
        from org import is_director

        department = _staff_department(user)
        if department and is_director(user):
            return qs.filter(user_id__in=_department_user_ids(department))
    return own


def schedules_for(user):
    qs = PaymentSchedule.objects.all()
    if can_view_all(user):
        return qs
    if not getattr(user, "is_authenticated", False):
        return qs.none()
    visible = Q(lines__user=user) | Q(lines__resource_person__user=user)
    if (str(config.get("honoraria.visibility.director_sees_department", True)).strip().lower()
            in ("1", "true", "yes", "on")):
        from org import is_director

        department = _staff_department(user)
        if department and is_director(user):
            visible |= Q(lines__user_id__in=_department_user_ids(department))
    return qs.filter(visible).distinct()


def payee_lines_for(user):
    """Schedule lines belonging to the signed-in payee, never their department."""
    if not getattr(user, "is_authenticated", False):
        return PaymentScheduleLine.objects.none()
    return (PaymentScheduleLine.objects.filter(
        Q(user=user) | Q(resource_person__user=user))
            .select_related("schedule", "resource_person", "user"))


def earning_lines_for(user):
    """Schedule lines visible to the payee, their department director, or Finance."""
    qs = PaymentScheduleLine.objects.exclude(schedule__status="cancelled").select_related(
        "schedule", "resource_person", "user")
    if can_view_all(user):
        return qs
    if not getattr(user, "is_authenticated", False):
        return qs.none()
    if (str(config.get("honoraria.visibility.director_sees_department", True)).strip().lower()
            in ("1", "true", "yes", "on")):
        from org import is_director

        department = _staff_department(user)
        if department and is_director(user):
            return qs.filter(user_id__in=_department_user_ids(department))
    return qs.filter(Q(user=user) | Q(resource_person__user=user))


def unclaimed_resource_person_records(user):
    """Completed academic visit work not yet included in a live payment run."""
    from inspection.models import ResourcePerson, ServiceRecord

    person = ResourcePerson.objects.filter(user=user).first()
    if person is None:
        return ServiceRecord.unscoped.none()
    return (ServiceRecord.unscoped.filter(
        resource_person=person, visit_count__gt=0, allowance_paid=False)
            .exclude(honorarium_claims__status__in=(
                HonorariumServiceRecordClaim.RESERVED, HonorariumServiceRecordClaim.PAID))
            .order_by("served_to", "pk").distinct())


def earnings_for(user):
    """Earnings visible to the payee, their director, Finance or the ES."""
    qs = HonorariumEntry.objects.select_related("payee_user", "department", "institution", "schedule")
    if can_view_all(user):
        return qs
    if not getattr(user, "is_authenticated", False):
        return qs.none()
    own = qs.filter(payee_user=user)
    if (str(config.get("honoraria.visibility.director_sees_department", True)).strip().lower()
            in ("1", "true", "yes", "on")):
        from org import is_director

        department = _staff_department(user)
        if department and is_director(user):
            return qs.filter(payee_user_id__in=_department_user_ids(department))
    return own


@transaction.atomic
def confirm_entry_received(entry, *, user):
    if entry.payee_user_id != getattr(user, "pk", None):
        raise PermissionDenied("Only the payee can confirm receipt.")
    if entry.status != HonorariumEntry.PAID:
        raise ValidationError("The honorarium has not been marked paid.")
    entry.status = HonorariumEntry.RECEIVED
    entry.received_at = timezone.now()
    entry.save(update_fields=["status", "received_at", "updated_at"])
    return entry


@transaction.atomic
def build_schedule(period_start, period_end, *, user, title="", payee_keys=None):
    """Create a draft schedule for the period from completed service rows."""
    from inspection.models import ServiceRecord

    if not can_approve(user):
        raise PermissionDenied("Only the Director of Finance may build a payment schedule.")
    if not period_start or not period_end or period_end < period_start:
        raise ValidationError("Choose a valid period.")
    HonorariumServiceRecordClaim.objects.filter(
        line__schedule__status="cancelled",
        status=HonorariumServiceRecordClaim.RESERVED,
    ).update(status=HonorariumServiceRecordClaim.RELEASED, updated_at=timezone.now())
    records = list(ServiceRecord.unscoped.select_for_update().filter(
        served_from__lte=period_end, served_to__gte=period_start, allowance_paid=False)
                   .exclude(honorarium_claims__status__in=(
                       HonorariumServiceRecordClaim.RESERVED, HonorariumServiceRecordClaim.PAID))
                   .select_related("staff__user", "resource_person", "qa_personnel").distinct())
    entries = list(HonorariumEntry.objects.select_for_update().filter(
        status=HonorariumEntry.ACCRUED, accrued_at__date__range=(period_start, period_end))
                   .select_related("payee_user"))
    if payee_keys is not None:
        # v2.2 roller: only the payees Finance picked ("pay these next").
        users = {k[1] for k in payee_keys if k[0] == "user"}
        rps = {k[1] for k in payee_keys if k[0] == "rp"}
        entries = [e for e in entries if e.payee_user_id in users]
        records = [r for r in records if (r.resource_person_id in rps)
                   or (r.staff_id and getattr(r.staff, "user_id", None) in users)]
    schedule = PaymentSchedule.objects.create(
        reference=hon_references.issue(
            "honoraria", unique_check=lambda ref: PaymentSchedule.objects.filter(reference=ref).exists()),
        title=(title or f"Honoraria {period_start:%b %Y}").strip()[:200],
        period_start=period_start, period_end=period_end, created_by=user)
    internal = {}
    academic = {}
    entry_days = {(entry.payee_user_id, timezone.localdate(entry.accrued_at))
                  for entry in entries if entry.kind == HonorariumEntry.SSQ_REVIEW}
    for entry in entries:
        if entry.kind not in (HonorariumEntry.SSQ_REVIEW, HonorariumEntry.DOCUMENT_ANALYSIS,
                              HonorariumEntry.SSQ_REVIEW_EXTERNAL):
            continue
        key = (entry.payee_user_id, entry.rate)
        group = internal.setdefault(key, {"user": entry.payee_user, "quantity": 0, "entries": [], "records": []})
        group["quantity"] += entry.quantity
        group["entries"].append(entry)
    for record in records:
        if record.staff_id and record.ssq_count > 0:
            account = getattr(record.staff, "user", None)
            if account is not None and (account.pk, record.served_from) not in entry_days:
                key = (account.pk, internal_rate())
                group = internal.setdefault(key, {"user": account, "quantity": 0, "entries": [], "records": []})
                group["quantity"] += record.ssq_count
                group["records"].append(record)
        if record.resource_person_id and record.visit_count > 0:
            group = academic.setdefault(record.resource_person_id, {"quantity": 0, "records": []})
            group["quantity"] += record.visit_count
            group["records"].append(record)
    from inspection.models import ResourcePerson

    for (_user_id, rate), group in internal.items():
        external = getattr(group["user"], "user_type", "") == "resource_person"
        rp = None
        if external:
            from inspection.models import ResourcePerson as _RP

            rp = _RP.objects.filter(user=group["user"]).first()
        line = PaymentScheduleLine.objects.create(
            schedule=schedule, user=group["user"], resource_person=rp, name=str(group["user"]),
            role="External SSQ review" if external else "Internal SSQ / document review",
            source=PaymentScheduleLine.ACADEMIC if external else PaymentScheduleLine.INTERNAL,
            quantity=group["quantity"], rate=rate)
        if group["entries"]:
            line.entries.set(group["entries"])
            HonorariumEntry.objects.filter(pk__in=[entry.pk for entry in group["entries"]]).update(
                schedule=schedule, status=HonorariumEntry.SCHEDULED, updated_at=timezone.now())
        if group["records"]:
            _reserve_service_records(line, group["records"])
    for rp_id, group in academic.items():
        person = ResourcePerson.objects.filter(pk=rp_id).first()
        line = PaymentScheduleLine.objects.create(
            schedule=schedule, user=getattr(person, "user", None), resource_person=person,
            name=str(person) if person else "", role="Academic resource person",
            source=PaymentScheduleLine.ACADEMIC, quantity=group["quantity"], rate=academic_rate())
        _reserve_service_records(line, group["records"])
    _recompute(schedule)
    if not schedule.lines.exists():
        raise ValidationError("No unpaid payable work was found in that period.")
    return schedule


def _reserve_service_records(line, records):
    claims = [
        HonorariumServiceRecordClaim(
            line=line, service_record=record, status=HonorariumServiceRecordClaim.RESERVED)
        for record in records
    ]
    try:
        with transaction.atomic():
            HonorariumServiceRecordClaim.objects.bulk_create(claims)
    except IntegrityError as exc:
        raise ValidationError(
            "Some completed work was already claimed by another payment schedule. "
            "Refresh the schedule and try again."
        ) from exc


def _recompute(schedule):
    total = schedule.lines.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    PaymentSchedule.objects.filter(pk=schedule.pk).update(total=total, updated_at=timezone.now())
    schedule.total = total
    return total


@transaction.atomic
def approve(schedule, *, user):
    if not can_approve(user):
        raise PermissionDenied("Only Finance may approve a payment schedule.")
    if schedule.status != "draft":
        raise ValidationError("Only a draft schedule can be approved.")
    if not schedule.lines.exists():
        raise ValidationError("The schedule has no lines.")
    schedule.status = "approved"
    schedule.approved_by = user
    schedule.approved_at = timezone.now()
    schedule.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    return schedule


@transaction.atomic
def mark_paid(schedule, *, user, funding_account_id, payment_reference, line_ids=None, amounts=None):
    if not can_pay(user):
        raise PermissionDenied("Only the Director of Finance may record a payment.")
    schedule = PaymentSchedule.objects.select_for_update().get(pk=schedule.pk)
    if schedule.status != "approved":
        raise ValidationError("Approve the schedule before paying it.")
    unpaid = list(schedule.lines.select_for_update().filter(paid_at__isnull=True)
                  .select_related("user", "resource_person__user"))
    if not unpaid:
        raise ValidationError("Every payee on this schedule has already been paid.")
    if line_ids is None:
        selected = unpaid
    else:
        raw_ids = [str(value) for value in line_ids]
        if not raw_ids or len(set(raw_ids)) != len(raw_ids) or any(not value.isdigit() for value in raw_ids):
            raise ValidationError("Choose one or more unpaid payees.")
        selected_ids = {int(value) for value in raw_ids}
        selected = [line for line in unpaid if line.pk in selected_ids]
        if len(selected) != len(selected_ids):
            raise ValidationError("A selected payee is not unpaid on this schedule.")
    from payments.models import BankAccount

    try:
        funding_account_id = int(funding_account_id)
    except (TypeError, ValueError):
        raise ValidationError("Choose an NBTE source bank account.")
    funding_account = BankAccount.objects.filter(
        pk=funding_account_id, is_active=True, is_placeholder=False).first()
    if funding_account is None or not funding_account.usable:
        raise ValidationError("Choose an active, verified NBTE bank account.")
    payment_reference = str(payment_reference or "").strip()
    if not payment_reference:
        raise ValidationError("Enter the bank transfer or batch payment reference.")
    if len(payment_reference) > 100:
        raise ValidationError("The payment reference must be 100 characters or fewer.")
    _details, missing = payment_details(schedule, user=user, lines=selected)
    if missing:
        names = ", ".join(line.name or str(line.person) for line in missing[:5])
        raise ValidationError(f"Payment cannot be recorded: confirmed bank details are missing for {names}.")

    now = timezone.now()
    amounts = amounts or {}
    audit_lines = []
    for line in selected:
        default_amount = line.rate * line.quantity
        raw_amount = amounts.get(str(line.pk), amounts.get(line.pk))
        if raw_amount is not None and str(raw_amount).strip():
            try:
                amount = Decimal(str(raw_amount).strip())
            except (ArithmeticError, ValueError):
                raise ValidationError(f"Enter a valid payment amount for {line.name or line.person}.")
            if (not amount.is_finite() or amount <= 0 or amount > Decimal("999999999999.99")
                    or amount.quantize(Decimal("0.01")) != amount):
                raise ValidationError(
                    f"Payment amount for {line.name or line.person} must be a positive amount "
                    "with no more than two decimal places.")
            line.amount = amount
            line.amount_override = amount != default_amount
            line.note = (f"Finance adjusted the calculated amount from ₦{default_amount:,.2f} "
                         f"to ₦{amount:,.2f}." if line.amount_override else "")
        line.paid_at = now
        line.funding_account = funding_account
        line.funding_bank_name = funding_account.bank_name
        line.funding_account_last4 = funding_account.account_number[-4:]
        line.payment_reference = payment_reference
        line.paid_by = user
        line.save(update_fields=[
            "amount", "amount_override", "note", "paid_at", "funding_account", "funding_bank_name",
            "funding_account_last4", "payment_reference", "paid_by", "updated_at",
        ])
        audit_lines.append({
            "payee": line.name or str(line.person),
            "amount": str(line.amount),
            "calculated_amount": str(default_amount),
            "reference": payment_reference,
            "funding_bank": f"{funding_account.bank_name} ••{funding_account.account_number[-4:]}",
        })

    schedule.funding_account = funding_account
    schedule.funding_bank_name = funding_account.bank_name
    schedule.funding_account_last4 = funding_account.account_number[-4:]
    schedule.payment_reference = payment_reference
    schedule.paid_by = user
    all_paid = not schedule.lines.filter(paid_at__isnull=True).exists()
    fields = ["funding_account", "funding_bank_name", "funding_account_last4", "payment_reference",
              "paid_by", "updated_at"]
    if all_paid:
        schedule.status = "paid"
        schedule.paid_at = now
        fields.extend(["status", "paid_at"])
    schedule.save(update_fields=fields)
    _recompute(schedule)
    selected_ids = [line.pk for line in selected]
    HonorariumServiceRecordClaim.objects.filter(
        line_id__in=selected_ids,
        status=HonorariumServiceRecordClaim.RESERVED,
    ).update(status=HonorariumServiceRecordClaim.PAID, updated_at=now)
    HonorariumEntry.objects.filter(
        schedule=schedule, status=HonorariumEntry.SCHEDULED,
        schedule_lines__pk__in=selected_ids).update(
        status=HonorariumEntry.PAID, paid_at=now, paid_reference=payment_reference, updated_at=now)
    # v2.2: every honorarium paid is posted to the double-entry ledger.
    from payments import ledger

    for line in selected:
        ledger.post_honorarium(f"{schedule.reference}:{line.pk}", line.amount,
                               f"Honorarium {schedule.reference} — {line.name or line.person}", actor=user)
    from core import audit

    changes = {"paid_lines": {"new": audit_lines}}
    if all_paid:
        changes["status"] = {"old": "approved", "new": "paid"}
    audit.log(audit._action("state_change"), schedule, actor=user, changes=changes,
              note=f"honoraria payment recorded for {sum((line.amount for line in selected), Decimal('0')):,.2f}")
    from notifications.services import notify

    payee_totals = {}
    for line in selected:
        payee = getattr(line.resource_person, "user", None) or line.user
        if payee is not None:
            previous = payee_totals.get(payee.pk, (payee, Decimal("0")))[1]
            payee_totals[payee.pk] = (payee, previous + line.amount)
    for payee, amount in payee_totals.values():
        transaction.on_commit(
            lambda payee=payee, amount=amount: notify(
                payee,
                title="Paid — please confirm receipt",
                body=f"Finance paid you ₦{amount:,.2f} ({schedule.payment_reference}). "
                     "Please confirm receipt on My earnings once it reaches your account.",
                url="/me/earnings/",
            )
        )
    return schedule


@transaction.atomic
def confirm_receipt(line, *, user):
    """The payee confirms the money reached them (X34)."""
    line = PaymentScheduleLine.objects.select_for_update().select_related(
        "schedule", "resource_person__user").get(pk=line.pk)
    payee_user_id = (getattr(line.resource_person, "user_id", None)
                     if line.resource_person_id else line.user_id)
    if payee_user_id != getattr(user, "pk", None):
        raise PermissionDenied("Only the payee can confirm receipt.")
    if line.paid_at is None:
        raise ValidationError("The schedule has not been marked paid yet.")
    line.confirmed_at = timezone.now()
    line.confirmed_by = user
    line.save(update_fields=["confirmed_at", "confirmed_by", "updated_at"])
    HonorariumEntry.objects.filter(schedule_lines=line, status=HonorariumEntry.PAID).update(
        status=HonorariumEntry.RECEIVED, received_at=line.confirmed_at, updated_at=line.confirmed_at)
    return line


def income_expenditure(period_start, period_end, *, bank_account_id=None):
    """Confirmed fee income less recorded honoraria payments for a period.

    This is an internal ledger report, not a live bank balance.
    """
    from payments.models import PaymentObligation

    income_qs = PaymentObligation.unscoped.filter(
        confirmed_at__date__gte=period_start, confirmed_at__date__lte=period_end)
    spend_qs = PaymentScheduleLine.objects.filter(
        paid_at__date__gte=period_start, paid_at__date__lte=period_end,
        schedule__status__in=("approved", "paid"))
    if bank_account_id is not None:
        income_qs = income_qs.filter(fee_schedule__revenue_account_id=bank_account_id)
        spend_qs = spend_qs.filter(funding_account_id=bank_account_id)
    income = income_qs.aggregate(t=Sum("amount_paid"))["t"] or Decimal("0")
    spend = spend_qs.aggregate(t=Sum("amount"))["t"] or Decimal("0")
    income_by_fee = {}
    for obligation in income_qs.select_related("fee_schedule").order_by(
            "fee_schedule__description", "fee_schedule__name", "pk"):
        components = [component for component in obligation.breakdown if component["amount"] > 0]
        component_total = sum((component["amount"] for component in components), Decimal("0"))
        allocated = Decimal("0")
        amount_to_allocate = min(obligation.amount_paid, component_total)
        for index, component in enumerate(components):
            if index == len(components) - 1:
                amount = amount_to_allocate - allocated
            else:
                amount = (amount_to_allocate * component["amount"] / component_total).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP)
                allocated += amount
            label = (component.get("label") or component.get("description")
                     or obligation.fee_schedule.description or obligation.fee_schedule.name)
            row = income_by_fee.setdefault(label, {"label": label, "count": 0, "amount": Decimal("0")})
            row["count"] += 1
            row["amount"] += amount
        excess = obligation.amount_paid - amount_to_allocate
        if components and excess > 0:
            label = "Overpayment beyond fee components"
            row = income_by_fee.setdefault(label, {"label": label, "count": 0, "amount": Decimal("0")})
            row["count"] += 1
            row["amount"] += excess
        if not components:
            label = obligation.fee_schedule.description or obligation.fee_schedule.name
            row = income_by_fee.setdefault(label, {"label": label, "count": 0, "amount": Decimal("0")})
            row["count"] += 1
            row["amount"] += obligation.amount_paid

    paid_lines = list(spend_qs.select_related(
        "user", "resource_person", "schedule", "paid_by", "funding_account"
    ).prefetch_related(
        "entries", "service_record_claims__service_record__institution",
        "service_record_claims__service_record__programme",
    ).order_by("paid_at", "name", "pk"))
    for line in paid_lines:
        activities = {}
        for entry in line.entries.all():
            label = entry.get_kind_display()
            activities[label] = activities.get(label, 0) + entry.quantity
        for claim in line.service_record_claims.all():
            record = claim.service_record
            if record.ssq_count:
                label = "SSQ review"
                activities[label] = activities.get(label, 0) + record.ssq_count
            if record.visit_count:
                context = " · ".join(filter(None, [
                    record.programme.name if record.programme_id else "",
                    record.institution.name if record.institution_id else "",
                ]))
                label = record.get_duty_display()
                if context:
                    label = f"{label} · {context}"
                activities[label] = activities.get(label, 0) + record.visit_count
        line.report_payee = (
            line.resource_person.full_name if line.resource_person_id
            else (line.user.get_full_name() or line.user.email) if line.user_id
            else line.name
        )
        line.report_activities = [
            f"{label} × {quantity}" for label, quantity in activities.items()
        ] or [line.get_source_display()]
    return {"period_start": period_start, "period_end": period_end,
            "income": income, "expenditure": spend, "balance": income - spend,
            "income_by_fee": list(income_by_fee.values()), "paid_lines": paid_lines}
