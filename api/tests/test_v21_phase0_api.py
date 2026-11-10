"""v2.1 Phase 0 through the REST API: the same rules as the website.

* X12 — only the Chief Executive submits (application, programmes, QA requests, SSQs);
* Q1/Q2 — programme submission and the staff programme-clearance endpoints;
* X35 — the portal SSQ payload carries no required quantities;
* O10 — staff clearance decide refuses officers.
"""
import pytest
from rest_framework.test import APIClient

from qa_core.tests.phase2_fixtures import clear_all, pdf_upload, upload_all_documents
from qa_core.tests.phase3_fixtures import complete_ssq, make_request

from .conftest import BASE, STAFF_HOST, bearer, err

pytestmark = pytest.mark.django_db


@pytest.fixture
def ict_head(make_user, proposed_inst):
    return make_user("ict.head@zit.edu.ng", role="inst-admin", user_type="institution", institution=proposed_inst)


def _at_programme_selection(app, p2, user, capture):
    from django.contrib.contenttypes.models import ContentType

    from accreditation.models import Application
    from payments import services as pay
    from payments.models import PaymentObligation, PaymentVerification

    with capture(execute=True):
        clear_all(app, p2)
    ob = PaymentObligation.unscoped.get(source_type=ContentType.objects.get_for_model(Application),
                                        source_id=str(app.pk))
    with capture(execute=True):
        pay.submit_proof(ob, user=user, upload=pdf_upload("t.pdf"), amount_claimed=ob.amount, bank_name="CBN",
                         teller_number="T-1", paid_on=None)
    item = PaymentVerification.unscoped.get(obligation=ob, is_open=True)
    with capture(execute=True):
        pay.final_decision(item, user=p2.fin_director, decision="confirmed")
    app.refresh_from_db()
    return app


def test_x12_ict_head_cannot_submit_application_api(portal_api, portal_host, proposed_inst, inst_user, ict_head):
    from accreditation import services

    app = services.start_application(proposed_inst, user=inst_user)
    upload_all_documents(proposed_inst, inst_user, app)
    api = bearer(portal_api, ict_head)
    body = api.get(BASE + "application/").json()
    assert body["can_submit"] is False and body["is_chief_executive"] is False
    r = api.post(BASE + "application/submit/", {"declaration": True}, format="json")
    assert r.status_code == 403 and "Chief Executive" in err(r)["message"]
    ce = bearer(APIClient(HTTP_HOST=portal_host), inst_user)
    assert ce.get(BASE + "application/").json()["can_submit"] is True
    r = ce.post(BASE + "application/submit/", {"declaration": True}, format="json")
    assert r.status_code == 200, r.json()


def test_q1_programmes_submit_api_ce_only(portal_api, portal_host, submitted_app, p2, inst_user, ict_head,
                                          django_capture_on_commit_callbacks):
    app = _at_programme_selection(submitted_app, p2, inst_user, django_capture_on_commit_callbacks)
    ict = bearer(portal_api, ict_head)
    r = ict.post(BASE + "application/programmes/", {"name": "Computer Science", "level": _nd(), "streams": 2},
                 format="json")
    assert r.status_code == 201, r.json()
    sub = r.json()["programme_submission"]
    assert sub["can_submit"] is False and sub["chief_executive_only"] is True and sub["count"] == 1
    assert ict.post(BASE + "application/programmes/submit/").status_code == 403
    ce = bearer(APIClient(HTTP_HOST=portal_host), inst_user)
    assert ce.get(BASE + "application/").json()["programme_submission"]["can_submit"] is True
    r = ce.post(BASE + "application/programmes/submit/")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["application"]["status"] == "programme_clearance"
    assert [p["state"] for p in body["programme_submission"]["programmes"]] == ["pending"]
    # Staff API: an NTIRD officer cannot decide; the NTIRD director clears → programme fee.
    officer = bearer(APIClient(HTTP_HOST=STAFF_HOST), p2.ntird_officer)
    listing = officer.get(BASE + "qa/applications/programmes/")
    review_id = None
    if listing.status_code == 200 and listing.json()["results"]:
        review_id = listing.json()["results"][0]["programmes"][0]["id"]
    director = bearer(APIClient(HTTP_HOST=STAFF_HOST), p2.ntird_director)
    listing = director.get(BASE + "qa/applications/programmes/").json()
    assert listing["count"] == 1 and listing["results"][0]["can_clear"] is True
    review_id = review_id or listing["results"][0]["programmes"][0]["id"]
    r = officer.post(BASE + f"qa/applications/programmes/{review_id}/decide/", {"decision": "clear"}, format="json")
    assert r.status_code in (403, 404)
    r = director.post(BASE + f"qa/applications/programmes/{review_id}/decide/", {"decision": "return"},
                      format="json")
    assert r.status_code == 400  # remark required
    with django_capture_on_commit_callbacks(execute=True):
        r = director.post(BASE + f"qa/applications/programmes/{review_id}/decide/", {"decision": "clear"},
                          format="json")
    assert r.status_code == 200, r.json()
    assert r.json()["application"]["status"] == "programme_fee_due"
    fees = ce.get(BASE + "application/").json()["fees"]
    assert len(fees) == 2


def _nd():
    from institutions.models import ProgrammeLevel

    return ProgrammeLevel.objects.get(code="nd").pk


def test_x12_ict_head_cannot_submit_qa_request_api(portal_api, portal_host, p3):
    req = make_request(p3)
    admin = bearer(portal_api, p3.admin)
    body = admin.get(BASE + f"qa-requests/{req.pk}/").json()
    assert body["can_submit"] is False
    r = admin.post(BASE + f"qa-requests/{req.pk}/submit/", {"declaration": True}, format="json")
    assert r.status_code in (403, 404)
    ce = bearer(APIClient(HTTP_HOST=portal_host), p3.ceo)
    r = ce.post(BASE + f"qa-requests/{req.pk}/submit/", {"declaration": True}, format="json")
    assert r.status_code == 200, r.json()


def test_x12_ict_head_cannot_submit_ssq_api_and_x35_no_required(portal_api, portal_host, p3, paid_request):
    from accreditation import qa_services as svc

    ssq = complete_ssq(svc.request_ssqs(paid_request)[0], p3.admin)
    admin = bearer(portal_api, p3.admin)
    url = BASE + f"qa-requests/{paid_request.pk}/ssq/{ssq.pk}/"
    body = admin.get(url).json()
    assert body["can_submit"] is False
    assert "required_quantity" not in str(body)
    r = admin.post(url + "submit/", {"declaration": True}, format="json")
    assert r.status_code == 403
    ce = bearer(APIClient(HTTP_HOST=portal_host), p3.ceo)
    r = ce.post(url + "submit/", {"declaration": True}, format="json")
    assert r.status_code == 200, r.json()
    body = ce.get(url).json()
    snapshot = body["submission"]["snapshot"]
    rows = [row for s in snapshot for f in s["fields"] if f["type"] == "catalogue" for row in f["rows"]]
    assert rows and not any("required" in row or "short" in row for row in rows)


def test_o10_staff_api_refuses_officer_clearance(staff_api, submitted_app, make_user):
    from accounts.models import User
    from accreditation import services
    from qa_core.departments import qa_department

    officer = make_user("plain.legal.api@t.local", role="officer")
    officer.department = qa_department("legal")
    officer.save(update_fields=["department"])
    officer = User.objects.get(pk=officer.pk)
    legal = next(c for c in services.clearances_for_round(submitted_app) if "legal" in c.branch_code)
    api = bearer(staff_api, officer)
    r = api.post(BASE + f"qa/applications/clearance/{legal.pk}/decide/", {"decision": "clear"}, format="json")
    assert r.status_code in (403, 404)
    legal.refresh_from_db()
    assert legal.status == "pending"
