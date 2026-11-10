"""v2.1 X40: the structured visit report, form NBTE/QA/ICT/VVT/001.

Sections A–D are prefilled from the case, the institution and the SSQ; the
panel records found vs declared quantities for the curriculum catalogue; a
falsified item adds a fine to the institution's bill. Every panel member must
declare any conflict of interest before the report can be written, and a
declared conflict blocks it until that person is replaced.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import config
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from inspection.models import PostVisitReport

from .models import CatalogueItem, VisitConflictDeclaration, VisitFinding


def panel_people(inspection):
    """The people who attended, or the planned team before the visit is
    recorded. Each row carries ``staff``, ``resource_person`` and ``person``."""
    from inspection.models import InspectionTeamMember, PostVisitPersonnel

    rows = list(PostVisitPersonnel.unscoped.filter(inspection=inspection).select_related(
        "staff", "resource_person", "qa_personnel"))
    attended = [r for r in rows if r.attended]
    if attended or rows:
        return [SimpleNamespace(staff=r.staff, resource_person=r.resource_person, person=r.person,
                                role=r.role_on_team, duty=r.duty) for r in (attended or rows)]
    team = (InspectionTeamMember.unscoped.filter(inspection=inspection)
            .select_related("staff", "qa_mapping__resource_person", "qa_mapping__qa_personnel__staff"))
    out = []
    for member in team:
        resource_person = member.qa_mapping.resource_person if member.qa_mapping_id else None
        staff = member.staff
        if staff is None and member.qa_mapping_id and member.qa_mapping.qa_personnel_id:
            staff = member.qa_mapping.qa_personnel.staff
        out.append(SimpleNamespace(staff=staff, resource_person=resource_person,
                                   person=resource_person or staff, role=member.role_on_team, duty=""))
    return out


def panel(inspection):
    return panel_people(inspection)


def declarations(inspection):
    return {_key(staff=d.staff, resource_person=d.resource_person): d
            for d in VisitConflictDeclaration.unscoped.filter(inspection=inspection)}


def _key(staff=None, resource_person=None):
    if staff is not None:
        return f"staff:{staff.pk}"
    if resource_person is not None:
        return f"rp:{resource_person.pk}"
    return ""


def declare(inspection, *, user, staff=None, resource_person=None, has_conflict=False, details=""):
    """Record one panel member's declaration after the visit is completed."""
    if inspection.status != "closed":
        raise ValidationError("Record the visit before completing post-visit declarations.")
    if staff is None and resource_person is None:
        raise ValidationError("Choose the panel member declaring.")
    if has_conflict and not (details or "").strip():
        raise ValidationError("Describe the conflict so it can be reviewed.")
    declaration, _created = VisitConflictDeclaration.unscoped.update_or_create(
        inspection=inspection, staff=staff, resource_person=resource_person,
        defaults={"has_conflict": bool(has_conflict), "details": (details or "").strip(),
                  "declared_by": user})
    return declaration


def missing_declarations(inspection):
    out = []
    declared = declarations(inspection)
    for person in panel_people(inspection):
        key = _key(staff=person.staff, resource_person=person.resource_person)
        if key and key not in declared:
            out.append(person)
    return out


def conflicting(inspection):
    return [d for d in VisitConflictDeclaration.unscoped.filter(inspection=inspection, has_conflict=True)]


def submitted_ssq(row):
    """Return the SSQ and immutable submission snapshot used as the report reference."""
    from .models import Submission

    submission = (Submission.unscoped.filter(ssq__qa_request_programme=row)
                  .select_related("ssq__template")
                  .order_by("-submitted_at", "-ssq__version", "-version").first())
    return (submission.ssq, submission) if submission is not None else (None, None)


def prefill(report):
    """Sections A–D of the form, prefilled from the record."""
    inspection = report.inspection
    case = inspection.case
    inst = case.institution
    programme = report.qa_request_programme.programme
    panel_rows = [{"name": str(person.person), "role": person.role,
                   "duty": person.duty} for person in panel_people(inspection)]
    return {
        "form": PostVisitReport.FORM_REFERENCE,
        "A": {"institution": inst.name, "code": inst.code,
              "state": inst.state.name if inst.state_id else "",
              "programme": programme.name, "level": programme.level.code.upper() if programme.level_id else "",
              "case": case.reference, "visit_reference": inspection.reference,
              "dates": [inspection.scheduled_start.isoformat() if inspection.scheduled_start else "",
                        inspection.scheduled_end.isoformat() if inspection.scheduled_end else ""]},
        "B": {"panel": panel_rows},
        "C": {"documents": []},
        "D": {"facilities": []},
    }


@transaction.atomic
def save_report(report, *, user, sections=None, summary="", recommendation="", findings=None):
    """Save the structured report. Refused until every panel member has
    declared and nobody declared a conflict."""
    _require_writable(report, user)
    if conflicting(report.inspection):
        raise ValidationError("A panel member declared a conflict of interest; replace them before writing "
                              "the report.")
    missing = missing_declarations(report.inspection)
    if missing:
        names = ", ".join(str(p.person) for p in missing[:3])
        raise ValidationError(f"Conflict-of-interest declarations are missing for: {names}.")
    if sections is not None:
        report.sections = sections
    if summary:
        report.summary = summary
    if recommendation:
        if recommendation not in dict(PostVisitReport.RECOMMENDATION_CHOICES):
            raise ValidationError("Choose a valid recommendation.")
        report.recommendation = recommendation
    report.form_reference = PostVisitReport.FORM_REFERENCE
    report.submitted_by = user
    from django.utils import timezone

    report.submitted_at = report.submitted_at or timezone.now()
    report.save()
    if findings is not None:
        save_findings(report, user=user, rows=findings)
    return report


@transaction.atomic
def save_findings(report, *, user, rows):
    """``rows`` = [{name, kind, declared_quantity, found_quantity, is_falsified,
    remark, item_id}] — the panel's found vs declared figures."""
    _require_writable(report, user)
    if conflicting(report.inspection):
        raise ValidationError("Resolve the conflict of interest before recording findings.")
    if missing_declarations(report.inspection):
        raise ValidationError("Every panel member must declare before findings are recorded.")
    ssq_quantities = _ssq_declared_quantities(report)
    saved = []
    for row in rows or []:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        item = CatalogueItem.objects.filter(pk=row.get("item_id") or 0, is_active=True).first()
        try:
            declared_value = row.get("declared_quantity") or 0
            if item is not None and ssq_quantities is not None:
                declared_value = ssq_quantities.get(str(item.pk), 0)
            declared = Decimal(str(declared_value))
            found_value = row.get("found_quantity")
            existing = (VisitFinding.unscoped.filter(report=report, item=item).first()
                        if item is not None else None)
            if found_value in (None, "") and existing is not None:
                found_value = existing.found_quantity
            found = Decimal(str(found_value or 0))
        except (ArithmeticError, ValueError):
            raise ValidationError(f"{name}: quantities must be numbers.") from None
        falsified = bool(row.get("is_falsified")) or (declared > 0 and found == 0)
        defaults = {
            "name": name[:200],
            "kind": row.get("kind") if row.get("kind") in dict(CatalogueItem.KIND_CHOICES)
            else (item.kind if item else CatalogueItem.EQUIPMENT),
            "declared_quantity": declared, "found_quantity": found, "is_falsified": falsified,
            "remark": (row.get("remark") or "").strip()[:255],
        }
        if item is not None:
            finding, _created = VisitFinding.unscoped.update_or_create(
                report=report, item=item, defaults=defaults)
        else:
            finding = VisitFinding.unscoped.create(report=report, item=None, **defaults)
        saved.append(finding)
    report.fines_total = fine_amount(report)
    report.save(update_fields=["fines_total", "updated_at"])
    return saved


def fine_amount(report):
    count = VisitFinding.unscoped.filter(report=report, is_falsified=True).count()
    if not count:
        return Decimal("0.00")
    rate = Decimal(str(config.get("qa.visit.fine_per_falsified_item", 500000) or 0))
    return (rate * count).quantize(Decimal("0.01"))


@transaction.atomic
def raise_fine(report, *, user):
    """Put the falsified-item fine on the institution's bill (X40)."""
    if not (user.is_superuser or user.has_perm("payments.view_obligation")):
        raise PermissionDenied("Only Finance may raise the fine.")
    if report.inspection.status != "closed":
        raise ValidationError("Record the visit before billing a falsification fine.")
    count = VisitFinding.unscoped.filter(report=report, is_falsified=True).count()
    if not count:
        raise ValidationError("No falsified items on this report.")
    if report.fine_obligation_id:
        raise ValidationError("The fine for this report has already been billed.")
    from payments import services as payments

    obligation = payments.issue_obligation(
        report.institution, "visit_fine", source=report, actor=user,
        variables={"falsified": count},
        description=f"Falsified items on visit report {report.inspection.reference} ({count})")
    report.fine_obligation = obligation
    report.fines_total = obligation.amount
    report.save(update_fields=["fine_obligation", "fines_total", "updated_at"])
    return obligation


def _require_writable(report, user):
    from inspection import services

    allowed, reason = services.can_write(report, user)
    if not allowed:
        raise PermissionDenied(reason)


def _parse_point(text):
    text = str(text or "").strip()
    if not text:
        return None
    parts = [p for p in text.replace(",", " ").split() if p]
    if len(parts) < 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None


def distance_km(a, b):
    """Great-circle distance between two (lat, lng) points."""
    import math

    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


@transaction.atomic
def record_gps(report, *, user, lat=None, lng=None, accuracy=None, point=""):
    """X40: where the report was written (from the panel's device). Compared
    with the institution's recorded GPS; more than qa.visit.gps_max_km (plus
    qa.visit.gps_offset_m) away is flagged for the Director — never blocked.

    ``point`` is the manual one-field form ("lat, lng" or "lat lng"); the
    browser capture passes ``lat``/``lng`` directly."""
    _require_writable(report, user)
    from django.utils import timezone

    if point and (lat in (None, "") or lng in (None, "")):
        parsed = _parse_point(point)
        if parsed is None:
            raise ValidationError("Enter the location as latitude, longitude (for example 10.5105, 7.4165).")
        lat, lng = parsed
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        raise ValidationError("The device did not give a valid location.") from None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValidationError("That is not a valid latitude/longitude pair.")
    report.gps_lat, report.gps_lng = round(lat, 6), round(lng, 6)
    report.gps_accuracy_m = int(float(accuracy)) if accuracy not in (None, "") else None
    report.gps_at = timezone.now()
    site = _parse_point(getattr(report.institution, "gps", ""))
    report.gps_distance_km = round(distance_km(site, (lat, lng)), 2) if site else None
    limit = float(config.get("qa.visit.gps_max_km", 5) or 5)
    limit += float(config.get("qa.visit.gps_offset_m", 200) or 0) / 1000
    report.gps_flag = bool(report.gps_distance_km is not None and report.gps_distance_km > limit)
    report.save(update_fields=["gps_lat", "gps_lng", "gps_accuracy_m", "gps_at", "gps_distance_km", "gps_flag",
                               "updated_at"])
    return report


def catalogue_rows(report):
    """Section D rows: the programme's catalogue items with what the SSQ declared."""
    programme = report.qa_request_programme.programme
    from . import register_link, ssq as engine

    register_link.ensure(programme)  # v2.4: NBTE's list from the facilities register
    items = []
    for kind in (CatalogueItem.FACILITY, CatalogueItem.EQUIPMENT):
        items.extend(engine.catalogue_items({"catalogue": {"kind": kind}}, programme)[:400 - len(items)])
    existing = {f.item_id: f for f in VisitFinding.unscoped.filter(report=report)}
    declared = _declared_quantities(report)
    return [{"item": it, "finding": existing.get(it.pk), "declared": declared.get(str(it.pk), "")} for it in items]


def _declared_quantities(report):
    """v2.4: what the institution declared on its latest SSQ submission for
    each catalogue item ({item id: quantity})."""
    return _ssq_declared_quantities(report) or {}


def _ssq_declared_quantities(report):
    """Return submitted catalogue quantities, or None when no submission exists."""
    ssq, submission = submitted_ssq(report.qa_request_programme)
    if submission is None:
        return None
    out = {}
    fmap = ssq.template.field_map()
    for key, value in (submission.data_snapshot or {}).items():
        if (fmap.get(key) or {}).get("type") == "catalogue" and isinstance(value, dict):
            out.update({str(k): v for k, v in value.items()})
    return out


__all__ = ["record_gps", "catalogue_rows", "distance_km", "submitted_ssq", "panel", "panel_people", "declarations", "declare", "missing_declarations", "conflicting", "prefill",
           "save_report", "save_findings", "fine_amount", "raise_fine"]
