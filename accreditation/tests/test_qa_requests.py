"""Phase 3 — SOP-02 QA requests (PHASES.md Phase 3; Master Plan §5.4, §5.7).

Requirement 4 (six-month window), R-25 (cancel / drop / replace only in draft,
submitted, fee due and payment pending confirmation), the fee, programme
clearance for new programmes, the Phase 2 actions that go live (Add Programme
to QA, Cancel Request, Wipe), the QA case and the Staff Portal Dashboard.
"""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client

from qa_core.tests.phase3_fixtures import bill_of, complete_ssq, make_request, set_config, waive_fee

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Requirement 4: the reassessment window
# ---------------------------------------------------------------------------
def test_six_month_window_requirement_4(p3):
    from accreditation import qa_services as svc
    from accreditation.models import QAType

    reacc = QAType.objects.get(code="reaccreditation")
    ok, _ = svc.programme_eligibility(p3.cs, reacc)          # expires in 120 days
    assert ok
    ok, why = svc.programme_eligibility(p3.far, reacc)       # expires in 3 years
    assert not ok and "Too early" in why and "Requirement 4" in why
    ok, _ = svc.programme_eligibility(p3.expired, reacc)     # expired: always eligible
    assert ok
    ok, why = svc.programme_eligibility(p3.proposed, reacc)  # wrong status
    assert not ok
    with pytest.raises(ValidationError):
        make_request(p3, [p3.far])


def test_window_follows_the_setting(p3):
    from accreditation import qa_services as svc
    from accreditation.models import QAType

    set_config("qa.programme.reassessment_window_months", "1")
    ok, why = svc.programme_eligibility(p3.cs, QAType.objects.get(code="reaccreditation"))
    assert not ok and "1 months" in why


def test_programme_cannot_be_in_two_open_requests(p3):
    make_request(p3, [p3.cs])
    with pytest.raises(ValidationError):
        make_request(p3, [p3.cs], type_code="institutional_administration")


# ---------------------------------------------------------------------------
# Request → fee → SSQs → QA case (DoD path)
# ---------------------------------------------------------------------------
def test_submit_issues_the_qa_fee_for_the_programme_count(p3):
    from accreditation import qa_services as svc

    req = make_request(p3)
    assert req.status == "draft" and req.reference.startswith("NBTE/QAR/")
    assert req.institution_request is not None and req.institution_request.request_type.code == "qa"
    svc.drop_programme(req, req.active_programmes()[1], user=p3.admin, reason="Not ready this year")
    req = svc.submit(req, user=p3.ceo, declaration=True)
    assert req.status == "fee_due"
    bill = bill_of(req)
    assert bill.amount == 200000  # 50,000 + 1 × 150,000
    assert bill.source_reference == req.reference


def test_unconfirmed_fee_waits_at_fee_due_and_can_be_retried(p3):
    from accreditation import qa_services as svc

    set_config("qa.fee.qa_request_processing.confirmed", "false")
    req = svc.submit(make_request(p3), user=p3.ceo, declaration=True)
    assert req.status == "fee_due" and req.fee_note and bill_of(req) is None
    with pytest.raises(ValidationError):
        svc.retry_fee(req, user=p3.p2.officer)
    set_config("qa.fee.qa_request_processing.confirmed", "true")
    svc.retry_fee(req, user=p3.p2.officer)
    req.refresh_from_db()
    assert bill_of(req) is not None and req.fee_note == ""


def test_declaration_is_required(p3):
    from accreditation import qa_services as svc

    with pytest.raises(ValidationError):
        svc.submit(make_request(p3), user=p3.ceo, declaration=False)


def test_confirmed_fee_opens_one_ssq_per_programme(paid_request):
    from accreditation import qa_services as svc

    ssqs = svc.request_ssqs(paid_request)
    assert len(ssqs) == 2 and all(s.version == 1 and s.status == "draft" for s in ssqs)


def test_qa_case_created_when_every_ssq_is_submitted(p3, paid_request, django_capture_on_commit_callbacks):
    from accreditation import qa_services as svc
    from accreditation.models import QACase

    first, second = svc.request_ssqs(paid_request)
    for ssq in (first, second):
        complete_ssq(ssq, p3.admin)
    with django_capture_on_commit_callbacks(execute=True):
        svc.submit_ssq(first, user=p3.ceo, declaration=True)
    paid_request.refresh_from_db()
    assert paid_request.status == "ssq_in_progress" and not QACase.unscoped.exists()
    with django_capture_on_commit_callbacks(execute=True):
        svc.submit_ssq(second, user=p3.ceo, declaration=True)
    paid_request.refresh_from_db()
    assert paid_request.status == "in_workflow"
    case = QACase.unscoped.get(qa_request=paid_request)
    # Phase 4: the case now starts SOP-03 at once (stage "case opened"; was "awaiting_sop03" in Phase 3).
    assert case.status == "case_opened" and case.programme_count == 2
    assert case.owning_department == p3.inst.handling_department
    assert case.reference.startswith("NBTE/QAC/")


# ---------------------------------------------------------------------------
# R-25: cancel / drop / replace only in the four early stages
# ---------------------------------------------------------------------------
def _to_stage(p3, stage):
    from accreditation import qa_services as svc
    from qa_core import flow

    req = make_request(p3)
    if stage == "draft":
        return req
    svc.submit(req, user=p3.ceo, declaration=True)
    req.refresh_from_db()
    if stage == "fee_due":
        return req
    if stage == "payment_pending_confirmation":
        flow.transition(svc.request_instance(req), "payment_claimed", None)
        req.refresh_from_db()
        return req
    if stage == "ssq_in_progress":
        return waive_fee(p3, req)
    raise AssertionError(stage)


@pytest.mark.parametrize("stage", ["draft", "fee_due", "payment_pending_confirmation"])
def test_cancel_allowed_in_r25_stages(p3, stage):
    from accreditation import qa_services as svc

    req = _to_stage(p3, stage)
    assert req.status == stage and svc.is_cancellable(req)
    bill = bill_of(req)
    req = svc.cancel(req, user=p3.admin, reason="We are not ready")
    assert req.status == "cancelled" and req.cancel_reason == "We are not ready"
    req.institution_request.refresh_from_db()
    assert req.institution_request.status == "cancelled"
    if bill is not None and stage == "fee_due":
        bill.refresh_from_db()
        assert bill.status == "cancelled"


def test_cancel_allowed_when_submitted(p3):
    """`submitted` is passed through automatically by submit(); the flow
    still offers cancel from it (checked on the flow data)."""
    from accreditation import qa_services as svc

    labels = svc.cancellable_stage_labels()
    assert set(labels) == {"Draft", "Submitted", "QA fee due", "Payment awaiting Finance confirmation"}


def test_cancel_refused_after_the_fee_is_confirmed(p3):
    from accreditation import qa_services as svc

    req = _to_stage(p3, "ssq_in_progress")
    assert req.status == "ssq_in_progress" and not svc.is_cancellable(req)
    with pytest.raises(ValidationError, match="R-25"):
        svc.cancel(req, user=p3.admin, reason="Too late")
    with pytest.raises(ValidationError, match="R-25"):
        svc.drop_programme(req, req.active_programmes()[0], user=p3.admin, reason="Too late")
    with pytest.raises(ValidationError, match="R-25"):
        svc.replace_programme(req, req.active_programmes()[0], p3.expired, user=p3.admin, reason="Too late")


@pytest.mark.parametrize("stage", ["draft", "fee_due", "payment_pending_confirmation"])
def test_drop_and_replace_in_r25_stages(p3, stage):
    from accreditation import qa_services as svc

    req = _to_stage(p3, stage)
    rows = req.active_programmes()
    new_row = svc.replace_programme(req, rows[0], p3.expired, user=p3.admin, reason="Swap for Statistics")
    rows[0].refresh_from_db()
    assert rows[0].status == "replaced" and rows[0].replaced_by == new_row
    svc.drop_programme(req, rows[1], user=p3.admin, reason="Not ready")
    assert [r.programme for r in req.active_programmes()] == [p3.expired]
    with pytest.raises(ValidationError, match="last programme"):
        svc.drop_programme(req, req.active_programmes()[0], user=p3.admin, reason="x y z")


def test_drop_at_fee_due_reissues_the_unpaid_bill(p3):
    from accreditation import qa_services as svc

    req = _to_stage(p3, "fee_due")
    first = bill_of(req)
    assert first.amount == 350000
    svc.drop_programme(req, req.active_programmes()[1], user=p3.admin, reason="Not ready")
    first.refresh_from_db()
    assert first.status == "cancelled"
    assert bill_of(req).amount == 200000


def test_department_account_cannot_manage_requests(p3):
    with pytest.raises(PermissionDenied):
        make_request(p3, user=p3.dept)


# ---------------------------------------------------------------------------
# New programmes: programme clearance (and its return path)
# ---------------------------------------------------------------------------
@pytest.fixture
def resource_type(p3):
    """Resource Inspection gets a published template so it can be requested."""
    from accreditation import builder
    from accreditation.models import QAType, SSQTemplate

    qa_type = QAType.objects.get(code="resource_inspection")
    base = QAType.objects.get(code="reaccreditation").ssq_template
    tpl = SSQTemplate.objects.create(qa_type=qa_type, version=1, name="RI", schema=base.schema)
    builder.publish(tpl, user=None, system=True)
    return qa_type


def test_new_programme_goes_through_clearance(p3, resource_type):
    from accreditation import qa_services as svc

    req = make_request(p3, [p3.proposed], type_code="resource_inspection")
    req = svc.submit(req, user=p3.ceo, declaration=True)
    assert req.status == "programme_clearance"
    ok, why = svc.can_clear(req, p3.p2.officer)   # DPAP officer: no clear permission
    assert not ok
    ok, why = svc.can_clear(req, p3.p2.director)  # DPAP director, but the stage belongs to NTIRD
    assert not ok and "another department" in why
    svc.return_programmes(req, user=p3.p2.ntird_director, remark="Add the curriculum first")
    req.refresh_from_db()
    assert req.status == "draft" and req.active_programmes()[0].clearance_status == "returned"
    req = svc.submit(req, user=p3.ceo, declaration=True)
    svc.clear_programmes(req, user=p3.p2.ntird_director, remark="Fine")
    req.refresh_from_db()
    assert req.status == "fee_due" and req.active_programmes()[0].clearance_status == "cleared"
    assert bill_of(req) is not None


# ---------------------------------------------------------------------------
# Phase 2 actions now live
# ---------------------------------------------------------------------------
def test_add_programme_to_qa_action_is_live(p3, department_mode):
    from accreditation.models import QARequestProgramme
    from qa_core import actions
    from qa_core.models import ActionPolicy

    assert ActionPolicy.objects.get(action_code="institutions.add_programme_to_qa").is_enabled
    req = make_request(p3, [p3.cs])
    pa = actions.initiate("institutions.add_programme_to_qa", actor=p3.p2.officer, target=p3.inst,
                          payload={"qa_request_id": req.pk, "programme_id": p3.eee.pk, "reason": "Asked by phone"},
                          reason="Asked by phone")
    assert not QARequestProgramme.unscoped.filter(qa_request=req, programme=p3.eee).exists()  # pending
    actions.confirm(pa, p3.p2.director)
    assert QARequestProgramme.unscoped.filter(qa_request=req, programme=p3.eee, status="active").exists()


def test_add_programme_action_refused_past_fee_due(p3, department_mode):
    from qa_core import actions

    req = _to_stage(p3, "ssq_in_progress")
    with pytest.raises(ValidationError):
        actions.initiate("institutions.add_programme_to_qa", actor=p3.p2.officer, target=p3.inst,
                         payload={"qa_request_id": req.pk, "programme_id": p3.expired.pk}, reason="x")


def test_cancel_request_action_uses_r25(p3, department_mode):
    from qa_core import actions

    early = _to_stage(p3, "draft")
    pa = actions.initiate("institutions.cancel_institution_request", actor=p3.p2.officer, target=p3.inst,
                          payload={"request_id": early.institution_request_id, "reason": "Duplicate"},
                          reason="Duplicate")
    actions.confirm(pa, p3.p2.director)
    early.refresh_from_db()
    assert early.status == "cancelled"

    late = make_request(p3, [p3.expired])
    from accreditation import qa_services as svc

    svc.submit(late, user=p3.ceo, declaration=True)
    waive_fee(p3, late)
    late.refresh_from_db()
    with pytest.raises(ValidationError, match="R-25"):
        actions.initiate("institutions.cancel_institution_request", actor=p3.p2.officer, target=p3.inst,
                         payload={"request_id": late.institution_request_id, "reason": "x"}, reason="x")


def test_drop_programmes_action_takes_the_programme_off_open_requests(p3, department_mode):
    from qa_core import actions

    # v2.1 X47: Drop Programmes is a TVET institution change now held by the
    # TVET Support groups, not the officer/director roles.
    req = make_request(p3)
    pa = actions.initiate("institutions.drop_programmes", actor=p3.p2.tvet_initiator, target=p3.inst,
                          payload={"programme_ids": [p3.eee.pk], "reason": "Closed by the institution"},
                          reason="Closed by the institution")
    actions.confirm(pa, p3.p2.tvet_confirmer)
    assert [r.programme for r in req.active_programmes()] == [p3.cs]


# ---------------------------------------------------------------------------
# Portal: the three dashboards and tenant isolation
# ---------------------------------------------------------------------------
def test_staff_portal_dashboard(p3, paid_request, portal_host):
    c = Client(HTTP_HOST=portal_host)
    c.force_login(p3.p2.officer)
    r = c.get("/")
    body = r.content.decode()
    assert r.status_code == 200 and "Go to Internal System" in body
    assert "Staff" in body and "Institution dashboard" not in body
    # No institution data on the portal: no names, no programme names.
    assert "Alpha Polytechnic" not in body and "Computer Science" not in body
    # Institution pages are closed to staff (they go back to their dashboard).
    for path in ("/qa-requests/", f"/qa-requests/{paid_request.pk}/", "/documents/", "/payments/", "/queries/",
                 "/institution/", "/timeline/"):
        r = c.get(path)
        assert r.status_code == 302 and r["Location"] == "/", path


def test_staff_dashboard_lists_work_with_internal_links(p3, paid_request, portal_host, department_mode):
    from accreditation import qa_services as svc

    ssq = svc.request_ssqs(paid_request)[0]
    complete_ssq(ssq, p3.admin)
    svc.submit_ssq(ssq, user=p3.ceo, declaration=True)
    # SSQs are on a strict allow-list (assignments.access.ssq_accessible_ids): an
    # officer sees one only once the department director assigns it and they accept.
    import org
    from accounts.models import User
    from accreditation import ssq_assign
    from assignments import work

    director = User.objects.get(pk=org.director_of(p3.inst.handling_department).pk)
    work.accept(ssq_assign.assign(ssq, by=director, officer=p3.p2.officer), User.objects.get(pk=p3.p2.officer.pk))
    c = Client(HTTP_HOST=portal_host)
    c.force_login(p3.p2.officer)
    body = c.get("/").content.decode()
    assert paid_request.reference in body and f"/qa/requests/ssq/{ssq.pk}/" in body and "Review SSQ" in body


def test_institution_users_never_get_the_staff_dashboard(p3, portal_host):
    c = Client(HTTP_HOST=portal_host)
    c.force_login(p3.admin)
    body = c.get("/").content.decode()
    assert "Institution dashboard" in body and "Go to Internal System" not in body


def test_other_institution_gets_404_on_the_portal(p3, paid_request, portal_host):
    from accreditation import qa_services as svc

    ssq = svc.request_ssqs(paid_request)[0]
    c = Client(HTTP_HOST=portal_host)
    c.force_login(p3.other_admin)
    for path in (f"/qa-requests/{paid_request.pk}/", f"/qa-requests/{paid_request.pk}/ssq/{ssq.pk}/"):
        assert c.get(path).status_code == 404
    assert c.post(f"/qa-requests/{paid_request.pk}/cancel/", {"reason": "hostile"}).status_code == 404
    paid_request.refresh_from_db()
    assert paid_request.status == "ssq_in_progress"


def test_portal_new_request_and_submit_screens(p3, portal_host):
    from accreditation.models import QARequest

    # v2.1 X13 runs the profile gate on top; this test checks the request
    # screens themselves (the gate has its own tests in test_v21_onboarding).
    set_config("qa.onboarding.profile_gate_enabled", "false")

    c = Client(HTTP_HOST=portal_host)
    c.force_login(p3.admin)
    r = c.get("/qa-requests/new/?type=reaccreditation")
    assert r.status_code == 200 and b"Too early" in r.content  # the far programme explains itself
    r = c.post("/qa-requests/new/", {"type": "reaccreditation", "programmes": [p3.cs.pk]},
               HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-main")
    assert r.status_code == 204 and "HX-Location" in r
    req = QARequest.unscoped.get(institution=p3.inst)
    # v2.1 X12: the administrator cannot submit; the Chief Executive can.
    r = c.post(f"/qa-requests/{req.pk}/submit/", {"declaration": "on"}, HTTP_HX_REQUEST="true",
               HTTP_HX_TARGET="qa-main")
    assert r.status_code == 422 and b"Chief Executive" in r.content
    c.force_login(p3.ceo)
    r = c.post(f"/qa-requests/{req.pk}/submit/", {"declaration": "on"}, HTTP_HX_REQUEST="true",
               HTTP_HX_TARGET="qa-main")
    assert r.status_code == 200 and b"QA processing fee due" in r.content


def test_internal_screens_are_scoped(p3, paid_request):
    """A VTSD officer gets 404 for DPAP's request, SSQ and query routes."""
    from accreditation import qa_services as svc

    ssq = svc.request_ssqs(paid_request)[0]
    from accounts.models import User
    from staff.models import Department

    outsider = User.objects.get(pk=p3.p2.officer.pk)
    outsider.department = Department.objects.get(code="vtsd")
    outsider.save(update_fields=["department"])
    outsider = User.objects.get(pk=outsider.pk)
    c = Client(HTTP_HOST="localhost")
    c.force_login(outsider)
    for path in (f"/qa/requests/{paid_request.pk}/", f"/qa/requests/ssq/{ssq.pk}/",
                 f"/qa/requests/query/ssq/{ssq.pk}/"):
        assert c.get(path).status_code == 404, path
    assert c.post(f"/qa/requests/ssq/{ssq.pk}/decide/", {"decision": "accept"}).status_code == 404
