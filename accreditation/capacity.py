"""Capacity and resource engine (Spec §7; Requirements 5–10) — Phase 8.

One assessment per request programme and resource kind:

* **classroom / laboratory** — how many streams the rooms can carry:

      standard_hours    = hours_per_day × days_per_week × weeks_per_semester × use_factor
                          (usable hours of one room in a semester)
      total_size_factor = Σ size_factor(seats of the room) × number of such rooms
      contact_hours     = weekly contact hours of one stream × weeks_per_semester
      other_load        = weekly hours other programmes use the rooms × weeks_per_semester
      additional        = qa.capacity.additional_streams
                        = ((standard_hours × total_size_factor) − (contact_hours × approved_streams
                          + other_load)) / contact_hours
      supportable       = approved_streams + ⌊additional⌋   (never below 0)

  Classroom size factors are bands ``[from seats, to seats, factor]`` and
  laboratory bands ``[from places, factor]``; a room takes the factor of the
  highest band whose lower bound it reaches (below the first band: 0).
* **library** — students = students per stream × streams asked for;
  seats needed = ⌈students × seat_ratio⌉, titles needed = ⌈students ×
  material_ratio⌉; supportable = the smaller of ⌊seats ÷ (seat_ratio ×
  students per stream)⌋ and ⌊titles ÷ (material_ratio × students per stream)⌋.
* **teaching_staff** — teaching hours available = (full-time lecturers −
  lecturers with an administrative post) × lecturer_max_hours_week +
  those with a post × admin_post_hours_week; supportable = ⌊hours ÷ weekly
  contact hours of one stream⌋. The rule of thumb (qa.capacity.rule_of_thumb)
  and the staff–student ratio (qa.ratio.staff_student) are shown as
  cross-checks only.
* **support_staff** — needed = ⌈students ÷ service_staff_ratio⌉;
  supportable = ⌊support staff × service_staff_ratio ÷ students per stream⌋.

Every run stores the inputs, a snapshot of every constant and formula in
effect (``parameters_snapshot``) and the result. ``compute`` works only from
the snapshot, so a stored assessment can always be reproduced and changing a
setting later never changes it.
"""
# scoped-by-caller
from __future__ import annotations

import json
import math
from decimal import ROUND_FLOOR, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

import config
from qa_core import timeline
from qa_core.formula import FormulaError, evaluate

from .models import CapacityAssessment, QARequestProgramme

KINDS = [k for k, _l in CapacityAssessment.KIND_CHOICES]
KIND_LABELS = dict(CapacityAssessment.KIND_CHOICES)

CONSTANT_KEYS = [
    ("qa.capacity.hours_per_day", "int", 8), ("qa.capacity.days_per_week", "int", 5),
    ("qa.capacity.weeks_per_semester", "int", 15), ("qa.capacity.classroom.use_factor", "decimal", "0.8"),
    ("qa.capacity.lab.use_factor", "decimal", "0.75"),
    ("qa.capacity.classroom.size_factors", "json", [[15, 20, 0.5], [30, 40, 1.0], [45, 60, 1.5], [60, None, 2.0]]),
    ("qa.capacity.lab.size_factors", "json", [[15, 0.5], [30, 1.0], [45, 1.5]]),
    ("qa.capacity.library.seat_ratio", "decimal", "0.1"), ("qa.capacity.library.material_ratio", "decimal", "0.3333"),
    ("qa.capacity.lecturer_max_hours_week", "int", 18), ("qa.capacity.admin_post_hours_week", "int", 8),
    ("qa.capacity.service_staff_ratio", "int", 20), ("qa.ratio.hnd_min_rank", "str", "lecturer-i"),
]
FORMULA_KEYS = {
    "qa.capacity.additional_streams": {
        "expr": "((standard_hours * total_size_factor) - (contact_hours * approved_streams + other_load)) "
                "/ contact_hours",
        "vars": ["standard_hours", "total_size_factor", "contact_hours", "approved_streams", "other_load"]},
    "qa.capacity.rule_of_thumb": {"expr": "4 + 2 * (streams - 1)", "vars": ["streams"]},
    "qa.ratio.staff_student": {"expr": "students / counted_staff", "vars": ["students", "counted_staff"]},
}


# ===========================================================================
# Parameters (the snapshot)
# ===========================================================================
def _read(key, kind, default):
    value = config.get(key, default)
    try:
        if kind == "int":
            return int(value)
        if kind == "decimal":
            return str(Decimal(str(value)))
        if kind == "json":
            return json.loads(value) if isinstance(value, str) else value
        return str(value)
    except (TypeError, ValueError, ArithmeticError):
        return default


def _formula(key):
    from config.models import ConfigEntry

    entry = ConfigEntry.objects.filter(key=key, scope="global").first()
    if entry is not None and entry.value_type == "formula":
        data = entry.parsed() or {}
        if data.get("expr"):
            return {"expr": data["expr"], "vars": list(data.get("vars", []))}
    return dict(FORMULA_KEYS[key])


def parameters():
    """Every constant and formula in effect now (stored with each run)."""
    return {"constants": {key: _read(key, kind, default) for key, kind, default in CONSTANT_KEYS},
            "formulas": {key: _formula(key) for key in FORMULA_KEYS},
            "taken_at": timezone.now().isoformat()}


# ===========================================================================
# Pure computation (from inputs + a parameters snapshot)
# ===========================================================================
def _d(value):
    return Decimal(str(value))


def _nonneg_int(inputs, key, label, default=0):
    raw = inputs.get(key, default)
    try:
        value = int(str(raw).strip() or default)
    except (TypeError, ValueError):
        raise ValidationError(f"{label}: enter a whole number.") from None
    if value < 0:
        raise ValidationError(f"{label}: cannot be negative.")
    return value


def _nonneg_num(inputs, key, label, default=0):
    raw = inputs.get(key, default)
    try:
        value = Decimal(str(raw).strip() or str(default))
    except (ArithmeticError, ValueError):
        raise ValidationError(f"{label}: enter a number.") from None
    if value < 0:
        raise ValidationError(f"{label}: cannot be negative.")
    return value


def size_factor(seats, bands, kind):
    """Factor of the highest band whose lower bound ``seats`` reaches."""
    factor = Decimal("0")
    for band in sorted(bands or [], key=lambda b: b[0]):
        low = band[0]
        value = band[2] if kind == "classroom" else band[1]
        if seats >= low:
            factor = _d(value)
    return factor


def _eval(params, key, variables):
    spec = params["formulas"][key]
    result, _used = evaluate(spec["expr"], variables, declared=spec["vars"], cfg_getter=_no_cfg)
    return result


def _no_cfg(key):
    raise FormulaError("Capacity formulas cannot read other settings; put the value in the snapshot.")


def _floor(value):
    return int(Decimal(value).to_integral_value(rounding=ROUND_FLOOR))


def _verdict(supportable, requested, extra=None):
    supportable = max(0, int(supportable))
    out = {"supportable_streams": supportable, "requested_streams": requested,
           "shortfall": max(0, requested - supportable), "adequate": supportable >= requested,
           "adequacy": "adequate" if supportable >= requested else "inadequate"}
    out.update(extra or {})
    return out


def _rooms(inputs, label):
    rooms = []
    for i, room in enumerate(inputs.get("rooms") or []):
        seats = _nonneg_int(room, "seats", f"{label} row {i + 1}: seats / places")
        count = _nonneg_int(room, "count", f"{label} row {i + 1}: number of rooms", 1)
        if count:
            rooms.append({"label": str(room.get("label") or "")[:120], "seats": seats, "count": count})
    return rooms


def compute(kind, inputs, params):
    """Return the result dict for ``kind``. Raises ValidationError on bad inputs."""
    c = params["constants"]
    requested = _nonneg_int(inputs, "requested_streams", "Streams asked for", 1) or 1
    if kind in ("classroom", "laboratory"):
        label = "Classrooms" if kind == "classroom" else "Laboratories"
        rooms = _rooms(inputs, label)
        use = _d(c["qa.capacity.classroom.use_factor" if kind == "classroom" else "qa.capacity.lab.use_factor"])
        bands = c["qa.capacity.classroom.size_factors" if kind == "classroom" else "qa.capacity.lab.size_factors"]
        weeks = _d(c["qa.capacity.weeks_per_semester"])
        standard = _d(c["qa.capacity.hours_per_day"]) * _d(c["qa.capacity.days_per_week"]) * weeks * use
        factors = [(r, size_factor(r["seats"], bands, kind)) for r in rooms]
        total_factor = sum((f * r["count"] for r, f in factors), Decimal("0"))
        contact_week = _nonneg_num(inputs, "contact_hours_week", "Weekly contact hours of one stream")
        if contact_week <= 0:
            raise ValidationError("Weekly contact hours of one stream must be more than 0.")
        contact = contact_week * weeks
        other = _nonneg_num(inputs, "other_load_week", "Weekly hours used by other programmes") * weeks
        approved = _nonneg_int(inputs, "approved_streams", "Streams already approved")
        variables = {"standard_hours": standard, "total_size_factor": total_factor, "contact_hours": contact,
                     "approved_streams": approved, "other_load": other}
        additional = _eval(params, "qa.capacity.additional_streams", variables)
        return _verdict(approved + _floor(additional), requested, {
            "detail": {"standard_hours": str(standard), "total_size_factor": str(total_factor),
                       "contact_hours": str(contact), "other_load": str(other), "approved_streams": approved,
                       "additional_streams": str(Decimal(additional).quantize(Decimal("0.01"))),
                       "rooms": [{**r, "factor": str(f)} for r, f in factors]},
        })
    sps = _nonneg_int(inputs, "students_per_stream", "Students per stream", 30)
    if sps <= 0:
        raise ValidationError("Students per stream must be more than 0.")
    students = sps * requested
    if kind == "library":
        seat_ratio = _d(c["qa.capacity.library.seat_ratio"])
        material_ratio = _d(c["qa.capacity.library.material_ratio"])
        seats = _nonneg_int(inputs, "seats", "Library seats available to the programme")
        titles = _nonneg_int(inputs, "titles", "Programme titles")
        need_seats = math.ceil(_d(students) * seat_ratio)
        need_titles = math.ceil(_d(students) * material_ratio)
        by_seats = _floor(_d(seats) / (seat_ratio * sps)) if seat_ratio else 0
        by_titles = _floor(_d(titles) / (material_ratio * sps)) if material_ratio else 0
        return _verdict(min(by_seats, by_titles), requested, {
            "detail": {"students": students, "seats_needed": need_seats, "titles_needed": need_titles,
                       "seats": seats, "titles": titles, "streams_by_seats": by_seats,
                       "streams_by_titles": by_titles,
                       "seats_short": max(0, need_seats - seats), "titles_short": max(0, need_titles - titles)}})
    if kind == "teaching_staff":
        full = _nonneg_int(inputs, "full_time_lecturers", "Full-time lecturers")
        admin = min(full, _nonneg_int(inputs, "admin_post_lecturers", "Lecturers with an administrative post"))
        contact_week = _nonneg_num(inputs, "contact_hours_week", "Weekly teaching hours of one stream")
        if contact_week <= 0:
            raise ValidationError("Weekly teaching hours of one stream must be more than 0.")
        hours = (_d(full - admin) * _d(c["qa.capacity.lecturer_max_hours_week"])
                 + _d(admin) * _d(c["qa.capacity.admin_post_hours_week"]))
        counted = _nonneg_int(inputs, "counted_staff", "Lecturers counted for the ratio", full)
        thumb = _eval(params, "qa.capacity.rule_of_thumb", {"streams": requested})
        ratio = (_eval(params, "qa.ratio.staff_student", {"students": students, "counted_staff": counted})
                 if counted else None)
        return _verdict(_floor(hours / contact_week), requested, {
            "detail": {"students": students, "teaching_hours_available": str(hours),
                       "hours_needed": str(contact_week * requested), "full_time_lecturers": full,
                       "admin_post_lecturers": admin, "rule_of_thumb_lecturers": _floor(thumb),
                       "meets_rule_of_thumb": full >= _floor(thumb), "counted_staff": counted,
                       "students_per_lecturer": str(Decimal(ratio).quantize(Decimal("0.1"))) if ratio else None}})
    if kind == "support_staff":
        ratio = _d(c["qa.capacity.service_staff_ratio"])
        staff = _nonneg_int(inputs, "support_staff", "Technologists and technicians")
        needed = math.ceil(_d(students) / ratio) if ratio else 0
        return _verdict(_floor(_d(staff) * ratio / sps), requested, {
            "detail": {"students": students, "support_staff": staff, "support_staff_needed": needed,
                       "short_by": max(0, needed - staff)}})
    raise ValidationError("Unknown kind of assessment.")


# ===========================================================================
# Prefill from the SSQ (Requirement 3: never ask twice)
# ===========================================================================
def _ssq_data(row):
    from .qa_services import current_ssq

    ssq = current_ssq(row)
    if ssq is None:
        return {}
    from .models import Submission

    submission = Submission.unscoped.filter(ssq=ssq).order_by("-version").first()
    return dict((submission.data_snapshot if submission is not None else ssq.data) or {})


def _int(value, default=0):
    try:
        return max(0, int(float(str(value).strip())))
    except (TypeError, ValueError):
        return default


def prefill(row):
    """Starting inputs for every kind, from the submitted SSQ where possible."""
    from .models import CatalogueItem

    data = _ssq_data(row)
    programme = row.programme
    sps = programme.category.students_per_stream if programme.category_id else 30
    requested = row.requested_streams or 1
    approved = programme.approved_streams or 0
    seats = _int(config.get("qa.capacity.default_room_seats", 40), 40)
    contact = _int(config.get("qa.capacity.default_contact_hours_week", 20), 20)
    base = {"requested_streams": requested}
    labs = []
    for item_id, qty in (data.get("facilities") or {}).items():
        if _int(qty):
            item = CatalogueItem.objects.filter(pk=_int(item_id)).first()
            labs.append({"label": item.name if item else "Laboratory / workshop", "seats": seats, "count": _int(qty)})
    staff_rows = [r for r in (data.get("academic_staff") or []) if isinstance(r, dict)]
    full_time = sum(1 for r in staff_rows if str(r.get("status", "")).lower().startswith("full")) or len(staff_rows)
    return {
        "classroom": {**base, "approved_streams": approved, "contact_hours_week": contact, "other_load_week": 0,
                      "rooms": [{"label": "Classrooms (from the SSQ)", "seats": seats,
                                 "count": _int(data.get("classrooms"))}] if data.get("classrooms") else []},
        "laboratory": {**base, "approved_streams": approved, "contact_hours_week": contact, "other_load_week": 0,
                       "rooms": labs},
        "library": {**base, "students_per_stream": sps, "seats": _int(data.get("library_seats")),
                    "titles": _int(data.get("titles", data.get("library_volumes")))},
        "teaching_staff": {**base, "students_per_stream": sps, "full_time_lecturers": full_time,
                           "admin_post_lecturers": 0, "contact_hours_week": contact, "counted_staff": full_time},
        "support_staff": {**base, "students_per_stream": sps,
                          "support_staff": _int(data.get("technologists")) + _int(data.get("technicians"))},
    }


# ===========================================================================
# Runs
# ===========================================================================
def current(row):
    """{kind: the current assessment or None}."""
    found = {a.kind: a for a in CapacityAssessment.unscoped.filter(qa_request_programme=row, is_current=True)}
    return {kind: found.get(kind) for kind in KINDS}


def older_versions(row, kind, limit=5):
    return list(CapacityAssessment.unscoped.filter(qa_request_programme=row, kind=kind, is_current=False)
                .order_by("-version")[:limit])


def overall(row):
    """Summary across the kinds assessed so far: the smallest supportable
    number of streams and whether everything assessed is adequate."""
    done = [a for a in current(row).values() if a is not None]
    if not done:
        return None
    supportable = min(a.result.get("supportable_streams", 0) for a in done)
    return {"supportable_streams": supportable, "adequate": all(a.result.get("adequate") for a in done),
            "assessed": len(done), "of": len(KINDS),
            "short": [KIND_LABELS[a.kind] for a in done if not a.result.get("adequate")]}


@transaction.atomic
def run(row, kind, inputs, *, user=None):
    """Compute and store a new version (the previous one stays, not current)."""
    if kind not in KINDS:
        raise ValidationError("Unknown kind of assessment.")
    row = QARequestProgramme.unscoped.select_for_update().select_related("programme", "qa_request").get(pk=row.pk)
    params = parameters()
    try:
        result = compute(kind, inputs, params)
    except FormulaError as exc:
        raise ValidationError(f"The capacity formula could not be worked out: {exc}") from None
    last = CapacityAssessment.unscoped.filter(qa_request_programme=row, kind=kind).order_by("-version").first()
    CapacityAssessment.unscoped.filter(qa_request_programme=row, kind=kind, is_current=True).update(
        is_current=False, updated_at=timezone.now())
    assessment = CapacityAssessment.unscoped.create(
        qa_request_programme=row, kind=kind, version=(last.version + 1) if last else 1, is_current=True,
        inputs=json.loads(json.dumps(inputs, default=str)), parameters_snapshot=params, result=result,
        computed_by=user if getattr(user, "pk", None) else None)
    timeline.record(row.qa_request.institution, "capacity.assessed", subject=assessment, actor=user,
                    visibility="internal",
                    internal_text=f"Capacity ({KIND_LABELS[kind]}) for {row.programme}: "
                                  f"{result['supportable_streams']} stream(s) supportable, "
                                  f"{result['requested_streams']} asked for — {result['adequacy']}.")
    return assessment


def reproduce(assessment):
    """Recompute a stored assessment from its own snapshot (must equal ``result``)."""
    return compute(assessment.kind, assessment.inputs, assessment.parameters_snapshot)
