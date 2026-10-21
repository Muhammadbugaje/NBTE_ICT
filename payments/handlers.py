"""Two-step payment actions (Master Plan §7 list): waivers, cancelling a bill,
refund approval, bank accounts and fee schedules. Each is confirmed by the
Director of Finance (policy data), never by its initiator."""
# scoped-by-caller
from __future__ import annotations

from django.core.exceptions import ValidationError

from qa_core.actions import ActionHandler, DiffRow, register

from . import providers, services
from .models import BankAccount, FeeSchedule, PaymentObligation, Refund
from .money import naira


class FinanceHandler(ActionHandler):
    def scope_department(self, target, initiator):
        # Finance actions belong to the Finance department, not the
        # institution's handling department.
        return getattr(initiator, "department", None)


@register
class WaiveHandler(FinanceHandler):
    code = "payments.waive"
    target_model = PaymentObligation

    def target_label(self, target):
        return f"{target.reference} — {target.institution.name}" if target else ""

    def snapshot(self, target):
        return {"status": target.status, "balance": str(target.balance)}

    def validate(self, target, payload):
        if not providers.get("offline").is_enabled():
            raise ValidationError("Waivers are switched off (qa.payment.offline.enabled).")
        if target.status not in ("awaiting_payment", "rejected", "partially_paid"):
            raise ValidationError(f"Only an unpaid bill can be waived ({services.stage_label(target)}).")
        if len((payload.get("reason") or "").strip()) < 5:
            raise ValidationError("A waiver needs a reason.")

    def preview(self, target, payload):
        return [DiffRow("Status", services.stage_label(target), "Waived"),
                DiffRow("Balance", naira(target.balance), naira(0)),
                DiffRow("Revenue", "—", "Not counted (waivers are reported separately)")]

    def apply(self, target, payload, actor):
        services.waive(target, actor=actor, reason=payload.get("reason", ""))


@register
class CancelObligationHandler(FinanceHandler):
    code = "payments.cancel_obligation"
    target_model = PaymentObligation

    def target_label(self, target):
        return f"{target.reference} — {target.institution.name}" if target else ""

    def snapshot(self, target):
        return {"status": target.status}

    def validate(self, target, payload):
        if target.status not in ("issued", "awaiting_payment"):
            raise ValidationError("Only an unpaid bill can be cancelled.")
        if len((payload.get("reason") or "").strip()) < 5:
            raise ValidationError("Give a reason.")

    def preview(self, target, payload):
        return [DiffRow("Status", services.stage_label(target), "Cancelled"),
                DiffRow("Money received later", "—", "Goes to the refund queue")]

    def apply(self, target, payload, actor):
        services.cancel(target, actor=actor, reason=payload.get("reason", ""))


@register
class RefundHandler(FinanceHandler):
    code = "payments.refund"
    target_model = Refund

    def target_label(self, target):
        return f"Refund {naira(target.amount)} — {target.obligation.reference}" if target else ""

    def snapshot(self, target):
        return {"status": target.status, "amount": str(target.amount)}

    def validate(self, target, payload):
        if target.status != "requested":
            raise ValidationError("This refund is not waiting for approval.")

    def preview(self, target, payload):
        return [DiffRow("Refund", "Requested", "Approved"), DiffRow("Amount", "", naira(target.amount)),
                DiffRow("Reason", "", target.reason)]

    def apply(self, target, payload, actor):
        services.approve_refund(target, actor=actor)

    def on_closed(self, pa, target, actor):
        if target is not None and pa.status == "rejected":
            services.decline_refund(target, actor=actor, remark=pa.decision_remark)


BANK_FIELDS = ("bank_name", "account_name", "account_number", "sort_code", "is_default", "is_active")


@register
class UpdateBankAccountHandler(FinanceHandler):
    code = "payments.update_bank_account"

    @property
    def target_model(self):
        return BankAccount

    def target_label(self, target):
        return f"Bank account: {target}" if target else "New bank account"

    def snapshot(self, target):
        return {f: getattr(target, f) for f in BANK_FIELDS}

    def validate(self, target, payload):
        from .models import nuban_validator

        number = str(payload.get("account_number", "")).strip()
        try:
            nuban_validator(number)
        except ValidationError:
            raise ValidationError("A NUBAN account number is exactly 10 digits.") from None
        if number == "0000000000":
            raise ValidationError("Enter the real account number.")
        if not str(payload.get("bank_name", "")).strip() or not str(payload.get("account_name", "")).strip():
            raise ValidationError("Enter the bank and the account name.")
        if target is not None and all(str(getattr(target, f)) == str(payload.get(f, getattr(target, f)))
                                      for f in BANK_FIELDS) and not target.is_placeholder:
            raise ValidationError("Nothing would change.")

    def preview(self, target, payload):
        rows = []
        for f in BANK_FIELDS:
            before = getattr(target, f) if target else ""
            rows.append(DiffRow(f.replace("_", " ").capitalize(), str(before), str(payload.get(f, before))))
        return rows

    def apply(self, target, payload, actor):
        account = target or BankAccount()
        for f in BANK_FIELDS:
            if f in payload:
                setattr(account, f, payload[f])
        account.is_placeholder = False
        account.save()
        if account.is_default:
            BankAccount.objects.exclude(pk=account.pk).update(is_default=False)
        FeeSchedule.objects.filter(revenue_account__is_placeholder=True).update(revenue_account=account)
        services.publish_waiting(actor=actor)


FEE_FIELDS = ("formula", "due_days", "remita_service_type_id", "revenue_account_id", "is_active", "description")


@register
class UpdateFeeScheduleHandler(FinanceHandler):
    code = "payments.update_fee_schedule"
    target_model = FeeSchedule

    def target_label(self, target):
        return f"Fee schedule: {target.name}" if target else ""

    def snapshot(self, target):
        return {f: getattr(target, f) for f in FEE_FIELDS}

    def validate(self, target, payload):
        from config.models import ConfigEntry

        key = payload.get("formula", target.formula)
        entry = ConfigEntry.objects.filter(key=key).first()
        if entry is None or entry.value_type != "formula":
            raise ValidationError(f"'{key}' is not a formula setting.")
        if all(str(getattr(target, f)) == str(payload.get(f, getattr(target, f))) for f in FEE_FIELDS):
            raise ValidationError("Nothing would change.")

    def preview(self, target, payload):
        return [DiffRow(f.replace("_id", "").replace("_", " ").capitalize(), str(getattr(target, f) or ""),
                        str(payload.get(f, getattr(target, f)) or "")) for f in FEE_FIELDS]

    def apply(self, target, payload, actor):
        for f in FEE_FIELDS:
            if f in payload:
                setattr(target, f, payload[f] if payload[f] != "" else None if f.endswith("_id") or f == "due_days"
                        else payload[f])
        target.save()
