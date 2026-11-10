"""v2.2: fees by institution category (Settings → Fees by institution category).

Polytechnics and colleges of nursing/health pay different registration,
processing and programme fees, edited on one screen by Finance."""
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

pytestmark = pytest.mark.django_db


@pytest.fixture
def table(lookups):
    from institutions.models import InstitutionType
    from payments.fee_table_seed import seed_type_fees
    from payments.models import InstitutionTypeFee

    seed_type_fees(InstitutionType, InstitutionTypeFee, activate=True)
    return InstitutionTypeFee


def test_seed_gives_sop_annex_a_figures_per_category(table):
    poly = table.objects.get(institution_type__code="polytechnic")
    nursing = table.objects.get(institution_type__code="college-nursing")
    assert poly.registration_fee == Decimal("4000000") and poly.programme_fee == Decimal("2000000")
    assert nursing.registration_fee == Decimal("2000000") and nursing.programme_fee == Decimal("1000000")
    assert poly.is_active and nursing.is_active
    # A category with no figure yet stays off (older formula keeps working).
    assert not table.objects.get(institution_type__code="vei").is_active


def test_registration_bill_uses_the_category_row(table, p2, proposed_inst):
    from payments import services

    row = table.objects.get(institution_type__code="polytechnic")
    row.registration_fee, row.registration_processing_fee = Decimal("3500000"), Decimal("250000")
    row.save()
    ob = services.issue_obligation(proposed_inst, "institutional_processing", actor=p2.fin_officer)
    assert ob.amount == Decimal("3750000.00")
    codes = [c["code"] for c in ob.formula_snapshot["components"]]
    assert codes == ["registration", "processing"]
    assert ob.formula_snapshot["source"] == "institution_type_fee"


def test_programme_bill_per_programme_and_processing_switch(table, proposed_inst):
    from payments import billing
    from payments.models import FeeSchedule

    row = table.objects.get(institution_type__code="polytechnic")
    row.programme_fee, row.programme_processing_fee = Decimal("100"), Decimal("10")
    row.save()
    sched = FeeSchedule.objects.get(code="programme_processing")
    _s, lines = billing.compute(sched, proposed_inst, {"programmes": 3, "streams": 3})
    assert sum(line["amount"] for line in lines) == Decimal("310.00")
    row.programme_processing_per_programme = True
    row.save()
    _s, lines = billing.compute(sched, proposed_inst, {"programmes": 3, "streams": 3})
    assert sum(line["amount"] for line in lines) == Decimal("330.00")


def test_category_without_any_fee_blocks_with_a_clear_message(table, proposed_inst):
    from payments import billing
    from payments.models import FeeSchedule

    row = table.objects.get(institution_type__code="polytechnic")
    row.registration_fee = row.registration_processing_fee = Decimal("0")
    row.save()
    with pytest.raises(ValidationError, match="Fees by institution category"):
        billing.compute(FeeSchedule.objects.get(code="institutional_processing"), proposed_inst, {})


def test_inactive_row_falls_back_to_the_formula(table, proposed_inst):
    from payments import billing
    from payments.models import FeeSchedule

    table.objects.filter(institution_type__code="polytechnic").update(is_active=False)
    from payments.services import fee_variables

    snap, _lines = billing.compute(FeeSchedule.objects.get(code="institutional_processing"), proposed_inst,
                                   fee_variables(proposed_inst))
    assert snap.get("source") != "institution_type_fee"


def test_only_finance_es_or_admin_can_edit(table, p2, client):
    from django.test import override_settings

    row = table.objects.get(institution_type__code="college-nursing")
    url = f"/qa/settings/fees/categories/{row.pk}/"
    data = {"registration_fee": "1,800,000", "programme_fee": "900000", "is_active": "on"}
    client.force_login(p2.officer)
    assert client.post(url, data).status_code in (403, 404)
    client.force_login(p2.fin_director)
    resp = client.post(url, data, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert resp.status_code == 200, resp.content[:400]
    row.refresh_from_db()
    assert row.registration_fee == Decimal("1800000") and row.updated_by == p2.fin_director
    page = client.get("/qa/settings/fees/categories/")
    assert page.status_code == 200 and b"College of Nursing" in page.content
