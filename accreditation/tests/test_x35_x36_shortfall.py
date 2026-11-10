"""v2.1 R.1 + R.2 (X35, X36): catalogue weights / critical items hidden from
institutions; shortfall analysis at SSQ submission; reviewer card; a major
band needs a remark to accept."""
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from accreditation.shortfall import band_for, compute
from qa_core.tests.phase3_fixtures import complete_ssq


def _sections(*rows):
    return [{"key": "eq", "title": "Equipment", "fields": [
        {"key": "equipment", "type": "catalogue", "label": "Equipment", "rows": [
            {"name": n, "unit": "no.", "required": str(req), "quantity": str(q) if q is not None else "",
             "weight": str(w), "critical": c} for n, req, q, w, c in rows]}]}]


@pytest.mark.parametrize("rows,score,band", [
    # design 28 example: 72.0 / 100
    ([("Desktop computers", 20, 12, 30, False), ("Network switch", 2, 2, 10, False), ("Projector", 2, 1, 5, False),
      ("UPS", 1, 1, 10, False), ("Server rack", 1, 0, 10, False), ("Software seats", 20, 20, 15, False),
      ("Air conditioners", 4, 3, 5, False), ("Textbooks", 40, 34, 15, False)], Decimal("72.0"), "fair"),
    ([("A", 10, 10, 1, False), ("B", 5, 50, 1, False)], Decimal("100.0"), "good"),    # surplus capped at 1
    ([("A", 10, None, 1, False), ("B", 10, 10, 1, False)], Decimal("50.0"), "major"),  # missing = 0
    ([("A", 10, 0, 1, True), ("B", 10, 10, 1, False)], Decimal("33.3"), "major"),      # critical counts double
    ([("A", 10, 10, 1, True), ("B", 10, 0, 1, False)], Decimal("66.7"), "fair"),
])
def test_x36_formula_cases(rows, score, band):
    result = compute(_sections(*rows))
    assert result["score"] == score and result["band"] == band
    assert result["items_scored"] == len(rows)


def test_x36_bands_and_missing_list():
    assert (band_for(80), band_for("79.9"), band_for(60), band_for("59.9")) == ("good", "fair", "fair", "major")
    result = compute(_sections(("Rack", 1, 0, 10, False), ("PC", 20, 12, 30, True), ("Ok", 1, 1, 1, False),
                               ("Zero", 0, 0, 5, False)))
    names = [i["name"] for i in result["items"]]
    assert names == ["PC", "Rack"]           # critical first, then worst ratio; required 0 is not scored
    assert result["items"][1]["missing"] and not result["items"][0]["missing"]
    assert compute(_sections()) is None


def test_x35_portal_never_shows_required():
    from accreditation.ssq import for_audience

    sections = _sections(("PC", 20, 12, 30, True))
    sections[0]["fields"][0]["rows"][0]["short"] = True
    out = for_audience(sections, "institution")
    row = out[0]["fields"][0]["rows"][0]
    assert not {"required", "short", "weight", "critical"} & set(row)
    assert sections[0]["fields"][0]["rows"][0]["weight"] == "30"   # staff copy untouched


@pytest.mark.django_db
def test_x36_shortfall_recorded_at_submission_and_review_card(p3, paid_request, client_for, department_mode):
    from accreditation import qa_services as svc
    from accreditation.models import CatalogueItem, ShortfallAnalysis
    from qa_core.tests.conftest import hx

    ssq = svc.request_ssqs(paid_request)[0]   # Computer Science
    item = CatalogueItem.objects.get(programme_name="Computer Science", name="Desktop computer")
    item.is_critical = True
    item.weight = Decimal("50")
    item.save()
    complete_ssq(ssq, p3.admin)
    svc.save_fields(ssq, {"equipment": {str(item.pk): "0"}}, user=p3.admin)
    svc.submit_ssq(ssq, user=p3.ceo, declaration=True)
    sa = ShortfallAnalysis.unscoped.get(ssq=ssq)
    assert sa.band == "major" and sa.items[0]["name"] == "Desktop computer" and sa.items[0]["critical"]
    r = client_for(p3.p2.officer).get(f"/qa/requests/ssq/{ssq.pk}/", **hx("qa-panel"))
    assert r.status_code == 200 and b"Resource shortfall" in r.content and b"Major shortfall" in r.content
    # the institution's SSQ page never shows the score
    with pytest.raises(ValidationError, match="remark"):
        svc.accept_ssq(ssq, user=p3.p2.officer)
    svc.accept_ssq(ssq, user=p3.p2.officer, remark="Visit panel to confirm; computers on order.")
    ssq.refresh_from_db()
    assert ssq.status == "accepted"
