"""Adding a fee without code, and issuing a bill by hand — Phase 8.

* ``new_fee_problems`` / ``test_formula``: a new fee schedule is a name, a
  code, a formula over the documented fee variables
  (``qa_core.settings_catalogue.FEE_VARIABLES``), due days and a Remita
  service type. It is created only when a second officer confirms
  (``payments.create_fee_schedule``); the formula becomes the dual-control
  setting ``qa.fee.<code>``.
* ``issuing_events``: which events issue which fee. The issuing code always
  names a schedule by its code — workflow effects
  ``create_payment_obligation:<code>`` and each QA type's fee schedule. A
  schedule nobody issues automatically is issued by Finance from an
  institution's bills page ("Issue a bill", ``payments.issue_bill``,
  two officers).
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError

from qa_core.formula import FormulaError, evaluate, validate_formula
from qa_core.settings_catalogue import FEE_VARIABLE_NAMES, FEE_VARIABLES

from .models import FeeSchedule

CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,50}$")
SAMPLE_DEFAULTS = {"ownership": "private", "institution_type": "polytechnic", "programmes": "2",
                   "new_programmes": "0", "streams": "3", "max_streams": "2"}
TEXT_VARIABLES = ("ownership", "institution_type")


def setting_key(code):
    return f"qa.fee.{code}"


def clean_spec(data):
    """Normalise and validate a new-fee payload. Returns the clean dict."""
    from config.models import ConfigEntry

    code = (data.get("code") or "").strip().lower()
    name = (data.get("name") or "").strip()
    expr = (data.get("expr") or "").strip()
    variables = [v for v in data.get("vars") or [] if v in FEE_VARIABLE_NAMES]
    if not CODE_RE.match(code):
        raise ValidationError("The code is 3–51 lower-case letters, digits or underscores, starting with a letter "
                              "(e.g. 'late_submission').")
    if FeeSchedule.objects.filter(code=code).exists():
        raise ValidationError(f"A fee schedule with the code '{code}' already exists.")
    if ConfigEntry.objects.filter(key=setting_key(code)).exists():
        raise ValidationError(f"The setting {setting_key(code)} already exists.")
    if len(name) < 3:
        raise ValidationError("Give the fee a name.")
    if FeeSchedule.objects.filter(name__iexact=name).exists():
        raise ValidationError(f"A fee called “{name}” already exists.")
    try:
        validate_formula(expr, variables, "decimal")
    except FormulaError as exc:
        raise ValidationError(f"Formula: {exc}") from None
    due = data.get("due_days")
    if due in ("", None):
        due = None
    else:
        try:
            due = int(due)
        except (TypeError, ValueError):
            raise ValidationError("Due days must be a whole number.") from None
        if not 1 <= due <= 365:
            raise ValidationError("Due days must be 1 to 365.")
    return {"code": code, "name": name, "description": (data.get("description") or name).strip()[:255],
            "expr": expr, "vars": variables, "due_days": due,
            "remita_service_type_id": (data.get("remita_service_type_id") or "").strip()[:40],
            "revenue_account_id": data.get("revenue_account_id") or None}


def sample_inputs(raw):
    """Sample values for the test panel: numbers as Decimal, text as text."""
    out = {}
    for name in FEE_VARIABLE_NAMES:
        value = (raw.get(name) if raw.get(name) not in (None, "") else SAMPLE_DEFAULTS[name])
        if name in TEXT_VARIABLES:
            out[name] = str(value).strip().lower()
        else:
            try:
                out[name] = Decimal(str(value).strip())
            except (InvalidOperation, ValueError):
                raise ValidationError(f"Sample {name}: enter a number.") from None
    return out


def test_formula(expr, variables, raw_inputs):
    """(amount or None, error) — evaluates the formula with sample inputs."""
    from .money import to_money

    try:
        inputs = sample_inputs(raw_inputs)
        validate_formula(expr, variables, "decimal")
        result, _used = evaluate(expr, inputs, declared=list(variables), returns="decimal")
    except (FormulaError, ValidationError) as exc:
        return None, " ".join(getattr(exc, "messages", None) or [str(exc)])
    amount = to_money(result)
    if amount <= 0:
        return amount, "The formula gives zero or less for these inputs; a bill must be more than zero."
    return amount, ""


def create_schedule(spec, *, actor=None):
    """Apply a confirmed new fee: the formula setting, then the schedule."""
    from config.models import ConfigEntry

    from qa_core.settings_catalogue import EDIT, G_FEES

    from .models import BankAccount

    ConfigEntry.objects.create(
        key=setting_key(spec["code"]), scope="global", value_type="formula", group=G_FEES,
        value=json.dumps({"expr": spec["expr"], "vars": spec["vars"], "returns": "decimal"}),
        description=f"{spec['name']} (₦). Added in Settings → Fees.", editable_by=EDIT, dual_control=True)
    account = BankAccount.objects.filter(pk=spec.get("revenue_account_id") or 0, is_placeholder=False).first()
    return FeeSchedule.objects.create(
        code=spec["code"], name=spec["name"], description=spec["description"], formula=setting_key(spec["code"]),
        due_days=spec["due_days"], remita_service_type_id=spec["remita_service_type_id"], revenue_account=account,
        is_active=True)


def issuing_events():
    """{schedule code: [plain-language events that issue it]}."""
    from qa_core.models import FlowTransition

    out = {}
    for t in (FlowTransition.objects.filter(flow__published_at__isnull=False, flow__is_active=True)
              .select_related("flow", "from_stage", "to_stage")):
        for effect in t.effects or []:
            if isinstance(effect, str) and effect.startswith("create_payment_obligation:"):
                code = effect.split(":", 1)[1]
                label = f"{t.flow.name}: “{t.label}” ({t.from_stage.label} → {t.to_stage.label})"
                if label not in out.setdefault(code, []):
                    out[code].append(label)
    from accreditation.models import QAType

    for qa_type in QAType.objects.filter(fee_schedule__isnull=False).select_related("fee_schedule"):
        out.setdefault(qa_type.fee_schedule.code, []).append(f"QA request submitted: {qa_type.name}")
    return out


VARIABLE_HELP = FEE_VARIABLES
