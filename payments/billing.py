"""Bill lines (v2.1 F.4, P1): the base fee + the processing fee + any other
charge, per fee schedule (QA type) and institution type.

A schedule without active ``FeeComponent`` rows bills its formula as one line
(the v2 behaviour). With components, every line is computed now and frozen in
``PaymentObligation.formula_snapshot["components"]``; the obligation amount is
their sum. A line whose amount is 0 (an amount NBTE has not confirmed yet,
shown as [FEE AMOUNT] in Settings) is left off the bill. If no confirmed
component remains, billing is blocked rather than falling back to a formula.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import config
from django.core.exceptions import ValidationError
from qa_core.formula import evaluate_setting

from .money import to_money

ZERO = Decimal("0.00")


def _enabled(key, default=True):
    value = config.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def components_for(schedule, institution):
    """The active components that apply to ``institution``: a row for its
    institution type beats the untyped row of the same kind (base and
    processing); 'other' rows are kept per code."""
    from .models import FeeComponent

    type_id = getattr(institution, "institution_type_id", None)
    chosen = {}
    for comp in FeeComponent.objects.filter(schedule=schedule, is_active=True).order_by("order", "pk"):
        if comp.institution_type_id and comp.institution_type_id != type_id:
            continue
        key = comp.kind if comp.kind in ("base", "processing") else f"other:{comp.code}"
        current = chosen.get(key)
        if current is None or (comp.institution_type_id and not current.institution_type_id):
            chosen[key] = comp
    return sorted(chosen.values(), key=lambda c: (c.order, c.pk))


def _setting_amount(key, variables):
    from config.models import ConfigEntry

    entry = ConfigEntry.objects.filter(key=key, scope="global").first()
    if entry is not None and entry.value_type == "formula":
        return to_money(evaluate_setting(key, variables)["result"])
    raw = config.get(key, "0")
    try:
        return to_money(raw if raw not in (None, "") else 0)
    except (TypeError, ValueError):
        return ZERO  # a "[FEE AMOUNT]" placeholder: no line yet


# ---------------------------------------------------------------------------
# SOP-01 institution fee per institution category AND ownership (Settings)
# ---------------------------------------------------------------------------
# The ``institutional_processing`` bill used to take its base from the
# hard-coded formula ``4000000 if ownership == 'private' else 2000000``. It now
# reads ``qa.fee.institution.<category>.<public|private>`` (and the processing
# line ``qa.fee.institution_processing.<category>.<public|private>``), seeded
# with the same amounts and edited in Settings under dual control. A category
# with an active row in "Fees by institution category" still uses that row;
# a category/ownership with no setting falls back to the formula.
INSTITUTION_FEE_SCHEDULE = "institutional_processing"
OWNERSHIP_GROUPS = ("public", "private")


def ownership_group(code):
    """'private' for privately owned institutions, 'public' for federal/state ones."""
    return "private" if str(code or "").strip().lower() == "private" else "public"


def institution_fee_key(type_code, group):
    return f"qa.fee.institution.{type_code}.{group}"


def institution_processing_key(type_code, group):
    return f"qa.fee.institution_processing.{type_code}.{group}"


def _optional_amount(key):
    """The amount of setting ``key``, or None when it does not exist / is blank."""
    raw = config.get(key, None)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return to_money(raw)
    except (TypeError, ValueError, ArithmeticError):
        return None


def institution_fee_settings(schedule, variables):
    """(base_key, base_amount|None, processing_key, processing_amount|None) for
    the SOP-01 ``institutional_processing`` bill; all None for other schedules."""
    if getattr(schedule, "code", "") != INSTITUTION_FEE_SCHEDULE:
        return None, None, None, None
    type_code = str(variables.get("institution_type") or "").strip()
    if not type_code:
        return None, None, None, None
    group = ownership_group(variables.get("ownership"))
    bkey, pkey = institution_fee_key(type_code, group), institution_processing_key(type_code, group)
    return bkey, _optional_amount(bkey), pkey, _optional_amount(pkey)


def formula_base(schedule, variables):
    """(snapshot, base amount): the per-category/ownership setting when it
    applies, otherwise the schedule's formula."""
    snapshot = evaluate_setting(schedule.formula, variables)
    bkey, base, _pkey, _proc = institution_fee_settings(schedule, variables)
    if base is None:
        return snapshot, to_money(snapshot["result"])
    snapshot = {**snapshot, "key": bkey, "expr": f"setting {bkey}", "result": str(base),
                "formula_key": snapshot.get("key"), "source": "institution_fee_setting"}
    return snapshot, base


def _int_var(variables, key, default=0):
    try:
        return max(int(variables.get(key) or default), 0)
    except (TypeError, ValueError):
        return default


def category_lines(schedule, institution, variables):
    """v2.2: the bill lines from the per-category fee table, or None when the
    institution's category has no row (older settings then apply). Raises
    ValidationError when the row exists but has no amount for this bill."""
    from .models import InstitutionTypeFee

    code = getattr(schedule, "code", "") or ""
    family = ("registration" if code.startswith("institutional_processing")
              else "programme" if code.startswith("programme_processing")
              else "qa_request" if code.startswith("qa_request_processing") else None)
    if family is None:
        return None
    row = InstitutionTypeFee.for_institution(institution)
    if row is None:
        return None
    label = row.institution_type.name
    n = max(_int_var(variables, "programmes", 0), 0)
    streams = _int_var(variables, "streams", n)
    extra_streams = max(streams - n, 0)
    processing_on = _enabled("qa.fee.processing.enabled", True)
    lines = []

    def add(code_, title, amount, kind, description=""):
        amount = to_money(amount or 0)
        if amount > 0:
            lines.append({"code": code_, "label": title, "description": description, "kind": kind,
                          "amount": amount, "to_confirm": not row.is_confirmed})

    if family == "registration":
        add("registration", "Institutional registration fee", row.registration_fee, "base", label)
        if processing_on:
            add("processing", "Processing fee", row.registration_processing_fee, "processing", label)
    elif family == "programme":
        n = max(n, 1)
        add("programme", f"Programme fee × {n}", row.programme_fee * n, "base",
            f"{label}: {to_money(row.programme_fee):,.2f} per programme")
        add("extra-streams", f"Additional streams × {extra_streams}", row.extra_stream_fee * extra_streams, "other")
        if processing_on:
            times = n if row.programme_processing_per_programme else 1
            add("processing", "Processing fee" + (f" × {times}" if times > 1 else ""),
                row.programme_processing_fee * times, "processing", label)
    else:
        n = max(n, 1)
        add("qa-base", "QA request fee", row.qa_request_base_fee, "base", label)
        add("qa-programmes", f"QA request fee × {n} programme{'s' if n != 1 else ''}",
            row.qa_request_fee_per_programme * n, "base", label)
        add("extra-streams", f"Additional streams × {extra_streams}", row.extra_stream_fee * extra_streams, "other")
        if processing_on:
            add("processing", "Processing fee", row.qa_request_processing_fee, "processing", label)
    if not lines:
        raise ValidationError(
            f"{schedule.name}: no fee is set for {label} yet. NBTE Finance must enter it in "
            "Settings → Fees by institution category before a bill can be issued.")
    return lines


def compute(schedule, institution, variables):
    """(snapshot, lines). ``snapshot`` is the schedule formula's snapshot with
    ``components`` (the bill lines) and ``result`` (their total) when the
    schedule has components; ``lines`` = [{code, label, description, kind,
    amount (Decimal), to_confirm}]. Raises FormulaError like evaluate_setting."""
    cat = category_lines(schedule, institution, variables)
    if cat is not None:
        total = sum((line["amount"] for line in cat), ZERO)
        from django.utils import timezone

        snapshot = {"key": "payments.InstitutionTypeFee", "expr": "fees by institution category",
                    "vars": sorted(variables), "inputs": {k: str(v) for k, v in variables.items()},
                    "result": str(total), "returns": "decimal", "source": "institution_type_fee",
                    "evaluated_at": timezone.now().isoformat(),
                    "components": [{**line, "amount": str(line["amount"])} for line in cat]}
        return snapshot, cat
    snapshot, base = formula_base(schedule, variables)
    _bkey, _base_setting, pkey, proc_setting = institution_fee_settings(schedule, variables)
    comps = components_for(schedule, institution)
    if not comps:
        return snapshot, [{"code": "fee", "label": schedule.name, "description": "", "kind": "base",
                           "amount": base, "to_confirm": False}]
    processing_on = _enabled("qa.fee.processing.enabled", True)
    lines = []
    for comp in comps:
        if comp.kind == "processing" and not processing_on:
            continue
        if comp.kind == "base" and not comp.amount_setting:
            amount = base
        elif comp.kind == "processing" and proc_setting is not None:
            amount = proc_setting  # per category + ownership (Settings)
        else:
            amount = _setting_amount(comp.amount_setting, variables)
        if comp.mode == "percent":
            amount = (base * amount / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if amount <= 0:
            continue
        lines.append({"code": comp.code, "label": comp.label, "description": comp.description, "kind": comp.kind,
                      "amount": amount, "to_confirm": comp.to_confirm})
    if not lines:
        raise ValidationError(
            f"{schedule.name}: no confirmed fee is configured for this institution type. "
            "NBTE Finance must set the fee before a bill can be issued."
        )
    total = sum((line["amount"] for line in lines), ZERO)
    if proc_setting is not None and processing_on:
        snapshot = {**snapshot, "processing_key": pkey}
    snapshot = {**snapshot, "base_result": snapshot["result"], "result": str(total),
                "components": [{**line, "amount": str(line["amount"])} for line in lines]}
    return snapshot, lines


def extra_lines(schedule, institution, variables):
    """Lines other than the base fee (for live estimates that already show
    the base per programme)."""
    _snap, lines = compute(schedule, institution, variables)
    return [line for line in lines if line["kind"] != "base"]
