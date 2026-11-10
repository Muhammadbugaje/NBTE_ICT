"""v2.1 X38/X39: visit costing from per-state rates.

The state of the institution sets the travel mode and the distance from HQ
(``staff.StateOfOrigin``, seeded and editable). The cost sheet is built from
the rate table: a rate row for the state overrides the shared row of the same
kind (duty tour allowance per night, transport, fuel, airport taxi). The
department then audits the sheet and the ES approves it through the existing
SOP-03 stages; Finance records the actual spend afterwards.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import config
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import CostLine, CostSheet, PersonnelEntry, RateTable

# The order lines are added in; the panel member is the multiplier.
KINDS = ("dta", "transport", "fuel", "airport_taxi")


def state_for(case):
    return case.institution.state


def rates_for(state, department=None):
    """{kind: RateTable} for a visit to ``state``. A row for the state beats a
    shared row; a row for the department beats a shared department row."""
    qs = RateTable.objects.filter(is_active=True, allowance_type__in=KINDS)
    qs = qs.filter(department__isnull=True) | qs.filter(department=department) if department else \
        qs.filter(department__isnull=True)
    chosen = {}
    for rate in qs.select_related("department", "state").order_by("sort_order", "pk"):
        if rate.state_id not in (None, getattr(state, "pk", None)):
            continue
        current = chosen.get(rate.allowance_type)
        # state-specific and department-specific rows win over shared ones.
        score = (1 if rate.state_id else 0, 1 if rate.department_id else 0)
        if current is None or score > current[0]:
            chosen[rate.allowance_type] = (score, rate)
    return {kind: pair[1] for kind, pair in chosen.items()}


def panel_for(case):
    """The approved panel (every entry while the list is still being prepared)."""
    entries = list(PersonnelEntry.unscoped.filter(personnel_list__case=case).select_related("staff"))
    approved = [e for e in entries if e.status == "approved"]
    return approved or entries


@transaction.atomic
def build_from_rates(case, *, user, nights=None):
    """Add the state's rates × nights × panel to the current cost sheet.
    Returns the lines created. Only a department editor may run it."""
    from . import case_services

    if case_services.cost_editor(case, user) != "department":
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("The cost sheet is not open for you to edit.")
    state = state_for(case)
    rates = rates_for(state, case.institution.handling_department)
    if not rates:
        raise ValidationError(f"No active visit rates for {state.name if state else 'this state'}. "
                              "Add them in Settings → Rate table.")
    panel = panel_for(case)
    if not panel:
        # The cost sheet (stage 2) comes before the personnel list (stage 3):
        # price one panel member for now; the department adjusts the lines
        # when the panel is approved.
        panel = [None]
    if nights in (None, ""):
        nights = int(config.get("qa.cost.visit_nights_default", 3) or 3)
    nights = Decimal(str(nights))
    if nights <= 0:
        raise ValidationError("Nights must be more than zero.")
    travel_mode = getattr(state, "travel_mode", "road") if state else "road"
    # v2.2: the calculator rebuilds — earlier calculator lines on the open
    # sheet are replaced, so running it twice never doubles the cost.
    from .case_services import current_sheet as _current_sheet

    sheet = _current_sheet(case)
    if sheet is not None:
        CostLine.unscoped.filter(sheet=sheet, rate__isnull=False).delete()
    created = []
    for entry in panel:
        for kind in KINDS:
            rate = rates.get(kind)
            if rate is None:
                continue
            if kind == "airport_taxi" and travel_mode != "air":
                continue
            # DTA is per night; transport, fuel and the airport taxi are one
            # round trip each (whatever unit the rate row carries).
            days = nights if (kind == "dta" and rate.unit == RateTable.PER_DAY) else Decimal("1")
            role = rate.role
            if entry is not None and entry.duty == "team_lead":
                role = f"Team lead — {rate.role}"
            created.append(case_services.add_cost_line(
                case, user=user, rate=rate, days=days, role_on_exercise=role,
                staff=entry.staff if entry is not None else None, check_person=False))
    if not created:
        raise ValidationError("The state has no rate lines that apply to this panel.")
    sheet = _current_sheet(case)
    if sheet is not None:
        from .case_services import _recompute

        _recompute(sheet)
    return created


def current_sheet(case):
    """Same row as case_services.current_sheet — route through the per-request
    memo so the case screens read it once."""
    from .case_services import current_sheet as _memoised

    return _memoised(case)


@transaction.atomic
def record_actual(case, *, user, amount, note=""):
    """Finance records what the visit actually cost (X39). Allowed once the ES
    has approved the sheet."""
    from django.core.exceptions import PermissionDenied

    if not (user.is_superuser or user.has_perm("accreditation.audit_cost_sheet")):
        raise PermissionDenied("Only Audit / Finance may record the actual spend.")
    sheet = current_sheet(case)
    if sheet is None or sheet.status != "es_approved":
        raise ValidationError("The cost sheet must be approved by the ES first.")
    try:
        value = Decimal(str(amount).replace(",", "").strip())
    except (ArithmeticError, ValueError):
        raise ValidationError("Enter the amount in naira.") from None
    if value < 0:
        raise ValidationError("The actual spend cannot be negative.")
    sheet.actual_total = value.quantize(Decimal("0.01"))
    sheet.actual_note = (note or "").strip()[:255]
    sheet.actual_recorded_by = user
    sheet.actual_recorded_at = timezone.now()
    sheet.save(update_fields=["actual_total", "actual_note", "actual_recorded_by", "actual_recorded_at",
                              "updated_at"])
    return sheet


def summary(case):
    sheet = current_sheet(case)
    if sheet is None:
        return None
    lines = CostLine.unscoped.filter(sheet=sheet)  # system code: sheet already in hand
    nights = lines.filter(kind="dta").values_list("days", flat=True).first()  # first() is None when empty
    return {"sheet": sheet, "planned": sheet.total, "actual": sheet.actual_total,
            "variance": (sheet.actual_total - sheet.total) if sheet.actual_total is not None else None,
            "state": state_for(case), "travel_mode": getattr(state_for(case), "travel_mode", ""),
            "distance_km": getattr(state_for(case), "distance_from_hq_km", 0),
            "nights": nights}


def days_of_visit(inspection):
    if inspection is None:
        return 1
    start, end = inspection.scheduled_start, inspection.scheduled_end
    if start and end:
        return max((end - start).days + 1, 1)
    return 1


def planned_for_visit(inspection):
    """The number of nights a scheduled visit needs (X39 starting point)."""
    return max(days_of_visit(inspection) - 1, 1)


__all__ = ["KINDS", "state_for", "rates_for", "panel_for", "build_from_rates", "current_sheet",
           "record_actual", "summary", "days_of_visit", "planned_for_visit"]
