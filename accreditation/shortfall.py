"""Resource shortfall analysis at SSQ submission (v2.1 R.2, X36).

    w_i   = weight_i x (2 if critical_i else 1)
    ratio = min(declared_i / required_i, 1)          (0 when nothing declared)
    score = 100 x sum(w_i x ratio_i) / sum(w_i)       over items with required > 0

Bands: score >= 80 good, >= 60 fair, else major. Items with ratio < 1 are listed
worst first (missing = nothing declared). Pure function ``compute(sections)``
works on the staff-view rendered snapshot, so the score is frozen with it.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

GOOD_AT, FAIR_AT = Decimal("80"), Decimal("60")
SUGGESTED = {"good": "accept", "fair": "accept_with_remark", "major": "return"}
SUGGESTED_LABELS = {"accept": "Accept", "accept_with_remark": "Accept with a remark, or query",
                    "return": "Return to the institution"}


def _dec(value, default=Decimal("0")):
    try:
        return Decimal(str(value)) if value not in (None, "") else default
    except (InvalidOperation, ValueError):
        return default


def band_for(score):
    score = Decimal(str(score))
    return "good" if score >= GOOD_AT else ("fair" if score >= FAIR_AT else "major")


def compute(sections):
    total_w = Decimal("0")
    got = Decimal("0")
    items, scored = [], 0
    for section in sections or []:
        for field in section.get("fields", []):
            if field.get("type") != "catalogue":
                continue
            for row in field.get("rows") or []:
                required = _dec(row.get("required"))
                if required <= 0:
                    continue
                weight = _dec(row.get("weight"), Decimal("1"))
                critical = bool(row.get("critical"))
                w = weight * (2 if critical else 1)
                declared = _dec(row.get("quantity"))
                ratio = min(declared / required, Decimal("1")) if declared > 0 else Decimal("0")
                total_w += w
                got += w * ratio
                scored += 1
                if ratio < 1:
                    items.append({"name": row.get("name", ""), "unit": row.get("unit", ""),
                                  "required": str(required.normalize()), "declared": str(declared.normalize()),
                                  "missing": declared <= 0, "critical": critical, "section": section.get("title", ""),
                                  "ratio": float(ratio), "weight": str(w.normalize())})
    if not total_w:
        return None
    score = (Decimal("100") * got / total_w).quantize(Decimal("0.1"))
    band = band_for(score)
    items.sort(key=lambda i: (not i["critical"], i["ratio"], i["name"]))
    return {"score": score, "band": band, "items": items, "items_scored": scored, "suggested": SUGGESTED[band]}


def record(submission):
    """Compute and store the analysis for a submission (idempotent)."""
    from .models import ShortfallAnalysis

    result = compute(submission.rendered_snapshot)
    if result is None:
        return None
    obj, _ = ShortfallAnalysis.unscoped.update_or_create(
        submission=submission, defaults={"ssq": submission.ssq, "score": result["score"], "band": result["band"],
                                         "items": result["items"], "items_scored": result["items_scored"],
                                         "suggested": result["suggested"]})
    return obj


def latest_for(ssq):
    from .models import ShortfallAnalysis

    return ShortfallAnalysis.unscoped.filter(ssq=ssq).order_by("-submission__version").first()
