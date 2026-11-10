"""v2.1 R.7 (X46, X48): rebuilt Add TVET form and inline programme Edit/Drop
through the pending-action flow."""
import pytest
from types import SimpleNamespace

from qa_core.tests.conftest import hx
from qa_core.tests.phase2_fixtures import pdf_upload

pytestmark = pytest.mark.django_db


@pytest.fixture
def tvet_ntird(make_user):
    """v2.1 X47: Add/Edit/Drop TVET are held by the TVET Support groups, not
    the officer/director roles. These holders sit in NTIRD, the department a
    proposed institution is handled by (Master Plan §5.2)."""
    return SimpleNamespace(
        initiator=make_user("x46.tvet.init@t.local", role="officer", dept="ntird",
                            groups=["TVET Support – Initiators"]),
        confirmer=make_user("x46.tvet.confirm@t.local", role="director", dept="ntird",
                            groups=["TVET Support – Confirmers"]),
    )


def _form_data(lookups, **extra):
    from institutions import services

    data = {
        "name": "Kafanchan Demo Institute", "status_at_add": "proposed", "ownership": lookups.private.pk,
        "category": lookups.poly_cat.pk, "cac_rc_number": "RC 12345", "state": lookups.kaduna.pk,
        "lga": services.lgas_for(lookups.kaduna)[0], "address_line1": "Km 3 Kafanchan Road",
        "postal_address": "PMB 1001, Kafanchan", "official_email": "info@kdi.demo-poly.local", "phone": "0803",
        "head_name": "Dr. Demo Rector", "ce_email": "rector@kdi.demo-poly.local", "ce_phone": "0804",
        "ict_name": "Demo ICT Head", "ict_email": "ict@kdi.demo-poly.local", "admin_first_name": "Demo",
        "admin_last_name": "Admin", "admin_email": "admin@kdi.demo-poly.local", "reason": "New institution",
    }
    data.update(extra)
    return data


def test_x46_form_rules(lookups):
    from institutions.forms import AddTvetForm

    f = AddTvetForm(_form_data(lookups))
    assert f.is_valid(), f.errors
    p = f.payload()
    assert p["code"] == "" and p["institution_type_id"] == lookups.poly.pk  # type derived from the merged field
    assert "code" not in f.fields and "institution_type" not in f.fields
    # private needs RC/CAC; public drops it
    f = AddTvetForm(_form_data(lookups, cac_rc_number=""))
    assert not f.is_valid() and "cac_rc_number" in f.errors
    f = AddTvetForm(_form_data(lookups, cac_rc_number="RC9", ownership=lookups.federal.pk))
    assert f.is_valid(), f.errors and f.cleaned_data["cac_rc_number"] == ""
    # Approved needs the letter
    f = AddTvetForm(_form_data(lookups, status_at_add="approved"))
    assert not f.is_valid() and "approval_letter" in f.errors
    f = AddTvetForm(_form_data(lookups, status_at_add="approved"), {"approval_letter": pdf_upload("letter.pdf")})
    assert f.is_valid(), f.errors
    # LGA is a dropdown of the chosen state
    assert f.fields["lga"].widget.__class__.__name__ == "Select"
    f = AddTvetForm(_form_data(lookups, lga="Not An LGA"))
    assert not f.is_valid() and "lga" in f.errors


def test_x46_approved_with_letter_end_to_end(p2, tvet_ntird, lookups, client_for):
    from institutions.models import Institution, InstitutionAddress, InstitutionContact, InstitutionDocument
    from qa_core import actions
    from qa_core.models import PendingAction

    # v2.1 X47: the Add TVET screen is carried by the TVET Support – Initiators.
    c = client_for(tvet_ntird.initiator)
    r = c.post("/qa/institutions/add/", {**_form_data(lookups, status_at_add="approved"),
                                         "approval_letter": pdf_upload("letter.pdf")})
    assert r.status_code in (200, 302), r.content[:500]
    pa = PendingAction.objects.get(policy__action_code="institutions.add_tvet", status="pending")
    assert pa.payload["approval_letter_path"] and pa.payload["code"] == ""
    actions.confirm(pa, tvet_ntird.confirmer)
    inst = Institution.unscoped.get(name="Kafanchan Demo Institute")
    assert inst.status == Institution.APPROVED and inst.code
    assert InstitutionDocument.unscoped.filter(institution=inst, kind__code="approval_letter").exists()
    assert InstitutionAddress.unscoped.filter(institution=inst, kind="postal").exists()
    assert InstitutionContact.unscoped.filter(institution=inst, kind="head_of_ict", name="Demo ICT Head").exists()


def test_x46_lga_select_options(p2, lookups, client_for):
    r = client_for(p2.officer).get(f"/qa/institutions/lgas/?select=1&state={lookups.kaduna.pk}", **hx("id_lga"))
    assert r.status_code == 200 and b"Choose the LGA" in r.content and b"<option value=" in r.content


def test_x48_inline_edit_and_drop(p2, tvet_ntird, department_mode, proposed_inst, client_for):
    """v2.1 X47 + X1: the holders are TVET Support staff in NTIRD; the screens
    resolve the institution through the viewer's scope, so the test runs in
    department (deployed) privacy mode."""
    from institutions.models import Programme, ProgrammeLevel
    from qa_core import actions
    from qa_core.models import PendingAction

    nd = ProgrammeLevel.objects.get(code="nd")
    p = Programme.unscoped.create(institution=proposed_inst, name="Computer Sciense", level=nd)
    c = client_for(tvet_ntird.initiator)
    url = f"/qa/institutions/{proposed_inst.code}/actions/institutions.update_programme/"
    r = c.get(url + f"?programme={p.pk}")
    assert r.status_code == 200 and b"Computer Sciense" in r.content
    r = c.post(url, {"programme": p.pk, "name": "Computer Science", "level": nd.pk, "reason": "Spelling fix"})
    assert r.status_code in (200, 302), r.content[:400]
    pa = PendingAction.objects.get(policy__action_code="institutions.update_programme", status="pending")
    actions.confirm(pa, tvet_ntird.confirmer)
    p.refresh_from_db()
    assert p.name == "Computer Science"
    # Drop pre-selects the row's programme
    r = c.get(f"/qa/institutions/{proposed_inst.code}/actions/institutions.drop_programmes/?programmes={p.pk}")
    assert r.status_code == 200 and b"checked" in r.content
