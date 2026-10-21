"""SOP-01 onboarding: draft → submit → the parallel clearance gate (Legal,
Finance, Academic Planning, Physical Planning) on the Phase 1 flow engine →
institutional fee → Finance confirmation → programme selection."""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from qa_core import flow
from qa_core.tests.phase2_fixtures import clear_all, pdf_upload, upload_all_documents

pytestmark = pytest.mark.django_db


def test_cannot_submit_until_documents_and_declaration(proposed_inst, inst_user):
    from accreditation import services

    app = services.start_application(proposed_inst, user=inst_user)
    assert app.status == "draft"
    assert services.submission_blockers(app)
    with pytest.raises(ValidationError):
        services.submit(app, user=inst_user, declaration=True)
    upload_all_documents(proposed_inst, inst_user, app)
    assert services.submission_blockers(app) == []
    with pytest.raises(ValidationError):
        services.submit(app, user=inst_user, declaration=False)
    services.submit(app, user=inst_user, declaration=True)
    app.refresh_from_db()
    assert app.status == "clearance"


def test_submit_fans_out_to_four_parallel_branches(submitted_app):
    from accreditation import services

    instance = services.instance_of(submitted_app)
    assert set(instance.current_stages) == {"legal_review", "finance_review", "academic_planning_review",
                                            "physical_planning_review"}
    clearances = services.clearances_for_round(submitted_app)
    assert len(clearances) == 4 and {c.status for c in clearances} == {"pending"}
    assert {c.department.code for c in clearances} == {"legal", "finance", "aspd", "ppd"} or len(
        {c.department_id for c in clearances}) == 4


def test_gate_waits_for_all_four(submitted_app, p2):
    from payments.models import PaymentObligation

    clear_all(submitted_app, p2, skip=("physical",))
    assert submitted_app.status == "clearance"
    assert not PaymentObligation.unscoped.filter(institution=submitted_app.institution).exists()
    clear_all(submitted_app, p2)
    assert submitted_app.status == "institutional_fee_due"
    ob = PaymentObligation.unscoped.get(institution=submitted_app.institution)
    assert ob.status == "awaiting_payment"


def test_any_one_return_sends_back_to_draft_and_closes_siblings(submitted_app, p2):
    from accreditation import services

    legal = next(c for c in services.clearances_for_round(submitted_app) if "legal" in c.branch_code)
    finance = next(c for c in services.clearances_for_round(submitted_app) if "finance" in c.branch_code)
    services.decide(finance, user=p2.finance_clr, decision="clear", remark="ok")
    with pytest.raises(ValidationError):
        services.decide(legal, user=p2.legal, decision="return", remark="")  # remark required
    services.decide(legal, user=p2.legal, decision="return", remark="CAC name does not match the proprietor")
    submitted_app.refresh_from_db()
    assert submitted_app.status == "draft"
    statuses = {c.branch_code: c.status for c in services.clearances_for_round(submitted_app, 1)}
    assert statuses["legal_review"] == "returned"
    assert set(statuses.values()) - {"returned", "cleared"} <= {"closed"}
    assert flow.branch_states(services.instance_of(submitted_app)) == {}


def test_resubmission_opens_round_two(submitted_app, p2, inst_user):
    from accreditation import services

    legal = next(c for c in services.clearances_for_round(submitted_app) if "legal" in c.branch_code)
    services.decide(legal, user=p2.legal, decision="return", remark="Re-upload the CAC certificate")
    submitted_app.refresh_from_db()
    services.submit(submitted_app, user=inst_user, declaration=True)
    submitted_app.refresh_from_db()
    assert submitted_app.round == 2 and submitted_app.status == "clearance"
    assert len(services.clearances_for_round(submitted_app)) == 4


def test_only_the_branch_department_can_decide(submitted_app, p2):
    from accreditation import services

    legal = next(c for c in services.clearances_for_round(submitted_app) if "legal" in c.branch_code)
    with pytest.raises(PermissionDenied):
        services.decide(legal, user=p2.finance_clr, decision="clear", remark="")
    with pytest.raises(PermissionDenied):
        services.decide(legal, user=p2.officer, decision="clear", remark="")


def test_documents_locked_after_submission(submitted_app, inst_user, client_for, portal_host):
    from institutions.models import DocumentKind

    kind = DocumentKind.objects.get(code="cac")
    c = client_for(inst_user, portal_host)
    response = c.post(f"/documents/upload/{kind.pk}/", {"file": pdf_upload("new.pdf", b"v2")})
    assert response.status_code == 200
    assert b"locked" in response.content
    from institutions.models import InstitutionDocument

    assert InstitutionDocument.unscoped.filter(institution=submitted_app.institution, kind=kind).count() == 1


@pytest.mark.parametrize("ownership,required,absent", [
    ("private", {"cac", "bank-guarantee"}, {"enabling-law", "funding-commitment"}),
    ("federal", {"enabling-law", "funding-commitment"}, {"cac", "bank-guarantee"}),
    ("state", {"enabling-law", "funding-commitment"}, {"cac", "bank-guarantee"}),
])
def test_document_requirements_differ_by_ownership(p2, add_tvet_payload, lookups, ownership, required, absent):
    from institutions import services
    from institutions.models import Institution, InstitutionOwnership
    from qa_core import actions

    own = InstitutionOwnership.objects.get(code=ownership)
    pa = actions.initiate("institutions.add_tvet", actor=p2.officer, reason="x",
                          payload=add_tvet_payload(ownership=own, email=f"{ownership}@t.edu.ng"))
    actions.confirm(pa, p2.director)
    inst = Institution.unscoped.get(name="Zaria Institute of Technology")
    codes = {r["kind"].code for r in services.checklist(inst) if r["mandatory"]}
    assert required <= codes
    assert not (absent & codes)
    assert {"c-of-o", "academic-master-plan", "need-survey", "physical-master-plan"} <= codes


def test_institutional_fee_snapshot_is_frozen(fee_obligation):
    from decimal import Decimal

    from django.core.cache import cache

    from config.models import ConfigEntry
    from payments.models import PaymentObligation

    assert fee_obligation.amount == Decimal("4000000.00")
    assert Decimal(str(fee_obligation.formula_snapshot["result"])) == Decimal("4000000")
    assert fee_obligation.amount_in_words == "Four million naira only"
    # Changing the fee setting later never changes a bill already issued.
    key = fee_obligation.fee_schedule.formula
    ConfigEntry.objects.filter(key=key).update(value="7000000")
    cache.clear()
    fresh = PaymentObligation.unscoped.get(pk=fee_obligation.pk)
    assert fresh.amount == Decimal("4000000.00") and fresh.formula_snapshot == fee_obligation.formula_snapshot


def test_confirmed_fee_moves_application_to_programme_selection(fee_obligation, submitted_app, p2, inst_user,
                                                                django_capture_on_commit_callbacks):
    from payments import services as pay
    from payments.models import PaymentVerification

    # The application listens to payment signals sent on commit.
    with django_capture_on_commit_callbacks(execute=True):
        pay.submit_proof(fee_obligation, user=inst_user, upload=pdf_upload("teller.pdf"), amount_claimed=fee_obligation.amount,
                         bank_name="CBN", teller_number="T-1", paid_on=None)
    submitted_app.refresh_from_db()
    assert submitted_app.status == "payment_pending_confirmation"
    item = PaymentVerification.unscoped.get(obligation=fee_obligation, is_open=True)
    pay.first_pass(item, user=p2.fin_officer, decision="approved", amount=fee_obligation.amount)
    submitted_app.refresh_from_db()
    assert submitted_app.status == "payment_pending_confirmation"  # first pass alone moves nothing
    with django_capture_on_commit_callbacks(execute=True):
        pay.final_decision(item, user=p2.fin_director, decision="confirmed")
    submitted_app.refresh_from_db()
    assert submitted_app.status == "programme_selection"
    from institutions.models import Institution

    assert Institution.unscoped.get(pk=submitted_app.institution_id).status in ("proposed", "under_review")


def test_clearance_queue_lists_only_own_department(submitted_app, p2, client_for):
    c = client_for(p2.legal)
    page = c.get("/qa/applications/clearance/")
    assert page.status_code == 200 and submitted_app.reference.encode() in page.content
    c = client_for(p2.dpap_monitor)
    assert c.get(f"/qa/applications/{submitted_app.pk}/").status_code == 404
