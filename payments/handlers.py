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



# ===========================================================================
# Phase 8: a new fee schedule, and a bill issued by hand (two officers each)
# ===========================================================================
@register
class CreateFeeScheduleHandler(FinanceHandler):
    code = "payments.create_fee_schedule"
    target_model = None

    def target_label(self, target):
        return ""

    def payload_label(self, payload):
        return f"New fee: {payload.get('name', '')} ({payload.get('code', '')})"

    def validate(self, target, payload):
        from . import fee_admin

        fee_admin.clean_spec(payload)
        amount, error = fee_admin.test_formula(payload.get("expr", ""), payload.get("vars") or [],
                                               payload.get("sample") or {})
        if amount is None:
            raise ValidationError(error)

    def preview(self, target, payload):
        from . import fee_admin

        amount, error = fee_admin.test_formula(payload.get("expr", ""), payload.get("vars") or [],
                                               payload.get("sample") or {})
        return [DiffRow("Fee", "—", f"{payload.get('name')} ({payload.get('code')})"),
                DiffRow("Formula setting", "—", fee_admin.setting_key(payload.get("code", ""))),
                DiffRow("Formula", "—", payload.get("expr", "")),
                DiffRow("Variables", "—", ", ".join(payload.get("vars") or []) or "none"),
                DiffRow("Due", "—", f"{payload.get('due_days')} days" if payload.get("due_days")
                        else "qa.payment.due_days"),
                DiffRow("Remita service type", "—", payload.get("remita_service_type_id") or "—"),
                DiffRow("Test with the sample inputs", "—", naira(amount) if amount is not None else error)]

    def apply(self, target, payload, actor):
        from . import fee_admin

        fee_admin.create_schedule(fee_admin.clean_spec(payload), actor=actor)


@register
class IssueBillHandler(FinanceHandler):
    code = "payments.issue_bill"

    @property
    def target_model(self):
        from institutions.models import Institution

        return Institution

    def target_label(self, target):
        return f"Bill for {target.name} ({target.code})" if target else ""

    def _schedule(self, payload):
        schedule = FeeSchedule.objects.filter(code=payload.get("fee_code") or "", is_active=True).first()
        if schedule is None:
            raise ValidationError("Choose an active fee schedule.")
        return schedule

    def _amount(self, target, payload):
        from qa_core.formula import FormulaError, evaluate_setting

        from .money import to_money

        schedule = self._schedule(payload)
        try:
            snap = evaluate_setting(schedule.formula, services.fee_variables(target, **(payload.get("variables") or {})))
        except FormulaError as exc:
            raise ValidationError(f"The fee formula could not be worked out: {exc}") from None
        return schedule, to_money(snap["result"])

    def validate(self, target, payload):
        if target is None or target.status == "delisted":
            raise ValidationError("Choose an institution that is not delisted.")
        if len((payload.get("reason") or "").strip()) < 5:
            raise ValidationError("Say why this bill is issued.")
        schedule, amount = self._amount(target, payload)
        if schedule.confirmed_setting and not services._bool(schedule.confirmed_setting, False):
            raise ValidationError(f"{schedule.name}: the amount is not confirmed yet ({schedule.confirmed_setting}).")
        if amount <= 0:
            raise ValidationError(f"The formula gives {naira(amount)}; a bill must be more than zero.")

    def preview(self, target, payload):
        schedule, amount = self._amount(target, payload)
        return [DiffRow("Institution", "", f"{target.name} ({target.code})"), DiffRow("Fee", "", schedule.name),
                DiffRow("Amount (from the formula now)", "", naira(amount)),
                DiffRow("Inputs given", "", ", ".join(f"{k} = {v}" for k, v in (payload.get("variables") or {})
                                                     .items()) or "from the institution's record"),
                DiffRow("Description on the slip", "", payload.get("description") or schedule.description),
                DiffRow("Reason", "", payload.get("reason", ""))]

    def apply(self, target, payload, actor):
        schedule = self._schedule(payload)
        services.issue_obligation(target, schedule.code, actor=actor, variables=payload.get("variables") or {},
                                  description=payload.get("description") or "")
