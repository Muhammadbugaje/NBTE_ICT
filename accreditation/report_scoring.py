"""v2.4 — scoring rules for template-driven visit reports (pure functions).

Two methods, chosen by the template's top-level ``scoring.method``:

``rated`` — NBTE/P/4A programme evaluation
    Each rated field carries ``scoring = {weight, group, points, on}``. Points:
    Very good 4, Good 3, Fair 2, Poor 1, Absent 0; "not applicable" (null) is
    left out of the score.

        score   = 100 × Σ(weight × points ÷ 4) ÷ Σ(weight of items rated)
        section = the same over one group (academic, facilities, staffing …)

    Outcome (NBTE guide notes, NBTE/P/4A §7, with documented pass marks):
        denied  — a ``deny`` observation (no curriculum, no entrepreneurship
                  curriculum) or score < interim_at (50);
        interim — an ``interim`` observation (e.g. a deferring item rated
                  Poor), score < full_at (70) or a section < group_floor (50);
        full    — otherwise.

``ict_vvt`` — NBTE/QA/ICT/VVT/001
    points = Σ weight × score (0–4), max 400, percentage = points ÷ 4; band
    80/60/40; mandatory items not met; deficiencies by severity from Section L;
    students per working workstation = approved capacity ÷ working units; and
    the computed N3 recommendation. Every scored component must cite evidence
    references that exist in the Section K register.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

OUTCOME_LABELS = {"full": "Full accreditation", "interim": "Interim accreditation (deferred)",
                  "denied": "Accreditation denied"}
ICT_LABELS = {"adequate": "ICT provision adequate for the programmes verified",
              "adequate_subject": "Adequate subject to correction of the deficiencies at Section L",
              "not_adequate_capacity": "Not adequate for the declared carrying capacity",
              "not_adequate": "Not adequate for the programmes verified",
              "obstructed": "Verification obstructed or could not be completed"}
TWO = Decimal("0.1")


def _dec(value, default=None):
    try:
        return Decimal(str(value).replace(",", "").strip()) if value not in (None, "") else default
    except (InvalidOperation, ValueError):
        return default


def _fields(schema):
    for section in (schema or {}).get("sections", []):
        for field in section.get("fields", []):
            yield section, field


def _label(field, value):
    for option in field.get("options") or []:
        if isinstance(option, dict) and str(option.get("value")) == str(value):
            return option.get("label") or value
        if option == value:
            return value
    return value


# ===========================================================================
# NBTE/P/4A — rated programme evaluation
# ===========================================================================
def evaluate(schema, data):
    rules = (schema or {}).get("scoring") or {}
    full_at = Decimal(str(rules.get("full_at", 70)))
    interim_at = Decimal(str(rules.get("interim_at", 50)))
    floor = Decimal(str(rules.get("group_floor", 50)))
    group_labels = rules.get("groups") or {}
    groups, triggers, unrated, items = {}, [], [], []
    total_w = got = Decimal("0")
    for _section, field in _fields(schema):
        rule = field.get("scoring") or {}
        points = rule.get("points")
        if not points:
            continue
        value = (data or {}).get(field["key"])
        if value in (None, ""):
            unrated.append(field["label"])
            continue
        outcome = (rule.get("on") or {}).get(value)
        if outcome in ("interim", "deny"):
            triggers.append({"field": field["key"], "label": field["label"], "rating": _label(field, value),
                             "outcome": "denied" if outcome == "deny" else "interim"})
        pts = points.get(value)
        if pts is None:
            continue  # not applicable
        weight = Decimal(str(rule.get("weight", 1)))
        earned = weight * Decimal(str(pts)) / Decimal("4")
        total_w += weight
        got += earned
        g = groups.setdefault(rule.get("group") or "other", {"weight": Decimal("0"), "got": Decimal("0")})
        g["weight"] += weight
        g["got"] += earned
        items.append({"field": field["key"], "label": field["label"], "rating": _label(field, value),
                      "points": pts, "weight": str(weight)})
    score = (Decimal("100") * got / total_w).quantize(TWO) if total_w else None
    group_rows = []
    for key, g in groups.items():
        pct = (Decimal("100") * g["got"] / g["weight"]).quantize(TWO) if g["weight"] else None
        group_rows.append({"key": key, "label": group_labels.get(key, key.title()), "weight": str(g["weight"]),
                           "score": str(pct) if pct is not None else "", "below_floor": pct is not None and pct < floor})
    order = list(group_labels)
    group_rows.sort(key=lambda r: order.index(r["key"]) if r["key"] in order else 99)
    reasons = []
    if score is None:
        outcome = ""
    else:
        deny = [t for t in triggers if t["outcome"] == "denied"]
        interim = [t for t in triggers if t["outcome"] == "interim"]
        low_groups = [r for r in group_rows if r["below_floor"]]
        if deny or score < interim_at:
            outcome = "denied"
            reasons += [f"{t['label']}: {t['rating']}" for t in deny]
            if score < interim_at:
                reasons.append(f"Score {score} % is below {interim_at} %.")
        elif interim or score < full_at or low_groups:
            outcome = "interim"
            reasons += [f"{t['label']}: {t['rating']}" for t in interim]
            if score < full_at:
                reasons.append(f"Score {score} % is below {full_at} % for full accreditation.")
            reasons += [f"{r['label']} at {r['score']} % is below {floor} %." for r in low_groups]
        else:
            outcome = "full"
            reasons.append(f"Score {score} % with every section at or above {floor} % and no deferring "
                           "observation.")
    return {"method": "rated", "score": str(score) if score is not None else "", "outcome": outcome,
            "outcome_label": OUTCOME_LABELS.get(outcome, "Not yet computed"), "groups": group_rows,
            "triggers": triggers, "reasons": reasons, "unrated": unrated, "complete": not unrated,
            "items": items, "thresholds": {"full_at": str(full_at), "interim_at": str(interim_at),
                                           "group_floor": str(floor)}}


def rating_from_pct(pct, *, very_good=90, good=80, fair=70):
    pct = _dec(pct)
    if pct is None:
        return ""
    return "very_good" if pct >= very_good else "good" if pct >= good else "fair" if pct >= fair else "poor"


def suggestions(ssq_data, report_data, *, programme_type="technology", streams=1, stream_size=None):
    """Suggested ratings from the SSQ and the verified figures (shown beside
    the fields; the panel decides). Returns {field_key: (value, why)}."""
    ssq_data, report_data = ssq_data or {}, report_data or {}
    out = {}
    pct = _dec(ssq_data.get("entry_compliance_pct"))
    if pct is not None:
        value = "very_good" if pct >= 100 else "good" if pct >= 90 else "fair" if pct >= 80 else "poor"
        out["admission"] = (value, f"SSQ: {pct} % of students meet the entry requirements.")
    for key, ssq_key, what in (("success_rate", "success_rate_pct", "success rate"),
                               ("practical_coverage", "practical_coverage_pct", "practical coverage")):
        value = rating_from_pct(ssq_data.get(ssq_key))
        if value:
            out[key] = (value, f"SSQ: {what} {ssq_data.get(ssq_key)} %.")
    equipment = report_data.get("verified_equipment_pct")
    if equipment not in (None, ""):
        out["equipment"] = (rating_from_pct(equipment), f"Verified: {equipment} % of NBTE's list found working.")
    size = int(_dec(stream_size or ssq_data.get("stream_size"), Decimal("30")))
    streams = max(1, int(_dec(streams or ssq_data.get("running_streams"), Decimal("1"))))
    volumes = _dec(report_data.get("verified_volumes"), _dec(ssq_data.get("library_volumes")))
    if volumes is not None:
        norm = Decimal(size * streams * 45 * 3)
        ratio = volumes / norm if norm else Decimal("0")
        value = "very_good" if ratio >= 1 else "good" if ratio >= Decimal("0.8") else (
            "fair" if ratio >= Decimal("0.6") else "poor")
        out["library"] = (value, f"{volumes} volumes against the norm of {norm} ({size} × {streams} stream(s) × "
                                 "45 titles × 3).")
    lecturers = _dec(report_data.get("verified_lecturers"))
    enrolment = _dec(report_data.get("verified_enrolment"))
    if lecturers is not None:
        per = 15 if programme_type == "technology" else 20
        needed = max(Decimal("4"), (enrolment / per).to_integral_value(rounding="ROUND_CEILING")
                     if enrolment else Decimal("4"))
        ratio = lecturers / needed
        value = "very_good" if ratio >= Decimal("1.2") else "good" if ratio >= 1 else (
            "fair" if ratio >= Decimal("0.75") else "poor")
        out["core_staff"] = (value, f"{lecturers} core lecturers verified; NBTE minimum {needed} "
                                    f"(4 per stream, 1:{per} pro-rated).")
    rank = ssq_data.get("hod_rank")
    if rank:
        senior = rank in ("Chief Lecturer", "Principal Lecturer", "Senior Lecturer")
        out["headship"] = ("good" if senior and ssq_data.get("hod_registered") == "yes" else
                           "fair" if senior else "poor", f"SSQ: HOD is {rank}; registered: "
                                                         f"{ssq_data.get('hod_registered') or '—'}.")
    curriculum = ssq_data.get("ent_curriculum")
    if curriculum:
        out["ent_curriculum"] = ({"nbte_local": "very_good", "nbte": "good", "below": "fair", "none": "absent"}
                                 .get(curriculum, ""), "SSQ: entrepreneurship curriculum declared.")
    allocation = _dec(ssq_data.get("recurrent_allocation"))
    if allocation is not None and enrolment:
        per_head = allocation / enrolment
        top, mid = (Decimal("10000"), Decimal("5000")) if programme_type == "technology" else (
            Decimal("5000"), Decimal("3500"))
        value = "very_good" if per_head > top else "good" if per_head >= mid else (
            "fair" if per_head >= mid / 2 else "poor")
        out["funding"] = (value, f"₦{per_head:,.0f} per student a year for consumables and imprest.")
    return {k: v for k, v in out.items() if v[0]}


# ===========================================================================
# NBTE/QA/ICT/VVT/001 — ICT verification
# ===========================================================================
REF_RE = re.compile(r"[A-Za-z]\d{1,3}")


def _refs(text):
    return {m.group(0).upper() for m in REF_RE.finditer(str(text or ""))}


def ict(schema, data):
    rules = (schema or {}).get("scoring") or {}
    data = data or {}
    points = max_points = Decimal("0")
    components, unscored, missing_evidence = [], [], []
    registered = {str(r.get("ref") or "").strip().upper() for r in data.get("k_register") or [] if r.get("ref")}
    mandatory = {"met": 0, "partly": 0, "not_met": 0, "unanswered": 0}
    not_met = []
    for _section, field in _fields(schema):
        rule = field.get("scoring") or {}
        if rule.get("mandatory"):
            value = data.get(field["key"]) or "unanswered"
            mandatory[value] = mandatory.get(value, 0) + 1
            if value == "not_met":
                not_met.append(field["label"])
            continue
        pts_map = rule.get("points")
        if not pts_map:
            continue
        weight = Decimal(str(rule.get("weight", 1)))
        max_points += weight * 4
        value = data.get(field["key"])
        if value in (None, ""):
            unscored.append(field["label"])
            continue
        score = Decimal(str(pts_map.get(value, 0)))
        points += weight * score
        evidence_key = rule.get("evidence_field")
        refs = _refs(data.get(evidence_key)) if evidence_key else set()
        bad = sorted(refs - registered) if registered else sorted(refs)
        if evidence_key and (not refs or bad):
            missing_evidence.append({"label": field["label"], "refs": sorted(refs), "unknown": bad})
        components.append({"field": field["key"], "label": field["label"], "weight": str(weight),
                           "score": str(score), "points": str(weight * score), "evidence": sorted(refs)})
    pct = (points * Decimal("100") / max_points).quantize(TWO) if max_points else None
    band = ""
    for floor, text in rules.get("bands") or []:
        if pct is not None and pct >= Decimal(str(floor)):
            band = text
            break
    severities = {"Critical": 0, "Major": 0, "Minor": 0}
    for row in data.get("l_register") or []:
        sev = row.get("severity")
        if sev in severities:
            severities[sev] += 1
    capacity, working = _dec(data.get("c_capacity")), _dec(data.get("f_working"))
    counted = _dec(data.get("f_counted"))
    ratio = (capacity / working).quantize(TWO) if capacity is not None and working else None
    sampled = len(data.get("j_samples") or [])
    declared_units = _dec(data.get("j_declared_units"), _dec(data.get("c_workstations")))
    sample_needed = None
    if declared_units is not None:
        sample_needed = int(max(Decimal("10"), (declared_units / 10).to_integral_value(rounding="ROUND_CEILING")))
    computed = ""
    if pct is not None and not unscored:
        if severities["Critical"] or pct < 40:
            computed = "not_adequate"
        elif pct < 60:
            computed = "not_adequate_capacity"
        elif pct < 80 or severities["Major"] or not_met or (ratio is not None and ratio > 10):
            computed = "adequate_subject"
        else:
            computed = "adequate"
        if computed == "adequate_subject" and ratio is not None and ratio > 20:
            computed = "not_adequate_capacity"
    chosen = data.get("n3_recommendation") or ""
    final = computed if chosen in ("", "computed") else chosen
    suggestions = {}
    if ratio is not None:
        suggestions["e_computers"] = ("3" if ratio <= 10 else "2" if ratio <= 15 else "1",
                                      f"{ratio} students per working workstation (threshold for 3: ≤ 10).")
    readings = [r for r in data.get("g_readings") or [] if _dec(r.get("download")) is not None]
    if readings:
        lab = min(_dec(r.get("download")) for r in readings)
        declared_bw = _dec(data.get("c_bandwidth"))
        ok = lab >= 10 and (declared_bw is None or lab >= declared_bw)
        suggestions["e_connectivity"] = ("3" if ok else "2", f"Lowest measured download {lab} Mbps"
                                         + (f" against {declared_bw} declared." if declared_bw is not None else "."))
    return {"method": "ict_vvt", "points": str(points), "max_points": str(max_points),
            "score": str(pct) if pct is not None else "", "band": band, "components": components,
            "unscored": unscored, "missing_evidence": missing_evidence, "mandatory": mandatory,
            "mandatory_not_met": not_met, "deficiencies": severities,
            "students_per_workstation": str(ratio) if ratio is not None else "",
            "workstations_counted": str(counted) if counted is not None else "",
            "sampled": sampled, "sample_needed": sample_needed,
            "computed": computed, "computed_label": ICT_LABELS.get(computed, "Not yet computed"),
            "outcome": final, "outcome_label": ICT_LABELS.get(final, "Not yet computed"),
            "suggestions": {k: list(v) for k, v in suggestions.items()},
            "complete": not unscored}


def compute(schema, data):
    method = ((schema or {}).get("scoring") or {}).get("method")
    if method == "rated":
        return evaluate(schema, data)
    if method == "ict_vvt":
        return ict(schema, data)
    return {}
