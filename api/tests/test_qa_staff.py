"""QA workspace for NBTE staff (qa/… on the staff door).

Each area: happy path through the website's own services, plus the security
checklist — no token 401, wrong door 403, temporary password 403, section
closed 403, out-of-scope object 404, refused action 403 with the reason,
validation 400 with ``fields``."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from qa_core.tests.phase2_fixtures import PDF, pdf_upload
from qa_core.tests.phase4_fixtures import advance_to, holder

from .conftest import BASE, bearer, err

pytestmark = pytest.mark.django_db
QA = BASE + "qa/"


def _get(client, user, path, **params):
    return bearer(client, user).get(QA + path, params)


def _post(client, user, path, data=None, fmt="json"):
    return bearer(client, user).post(QA + path, data or {}, format=fmt)


# ===========================================================================
# Doors, auth and sections
# ===========================================================================
ROUTES = ["summary/", "cases/", "cases/my-stages/", "cases/1/", "confirmations/", "finance/", "institutions/",
          "institutions/KDP/", "applications/clearance/", "requests/", "requests/ssq/", "complaints/",
          "inspections/", "files/payments.proofofpayment/1/"]


@pytest.mark.parametrize("path", ROUTES)
def test_no_token_is_401(staff_api, qa_seed, path):
    r = staff_api.get(QA + path)
    assert r.status_code == 401 and err(r)["code"] == "not_authenticated"


def test_portal_user_gets_wrong_door(portal_api, institution_user):
    r = bearer(portal_api, institution_user).get(QA + "summary/")
    # The qa/ routes exist on the staff door only: the portal host answers 404 …
    assert r.status_code == 404
    # … and a portal token used on the staff host is refused with wrong_door.
    from rest_framework.test import APIClient

    staff_host = bearer(APIClient(HTTP_HOST="localhost"), institution_user)
    r = staff_host.get(QA + "summary/")
    assert r.status_code == 403 and err(r)["code"] == "wrong_door" and err(r)["correct_door"] == "portal"


def test_temporary_password_blocks_qa(staff_api, qa_seed, make_user):
    user = make_user("tmp.qa@t.local", role="officer", dept="dpap", must_change=True)
    r = _get(staff_api, user, "summary/")
    assert r.status_code == 403 and err(r)["code"] == "password_change_required"


@pytest.mark.parametrize("path", ["cases/", "confirmations/", "finance/", "institutions/", "applications/clearance/",
                                  "requests/", "complaints/", "inspections/"])
def test_sections_closed_to_plain_staff(staff_api, qa_seed, make_user, path):
    plain = make_user("plain.qa@t.local")
    r = _get(staff_api, plain, path)
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"


def test_summary_counts_are_null_for_closed_sections(staff_api, qa_seed, make_user):
    plain = make_user("plain.sum@t.local")
    data = _get(staff_api, plain, "summary/").json()
    assert not any(data["sections"].values())
    assert all(v is None for v in data["counts"].values())


# ===========================================================================
# Cases
# ===========================================================================
def test_case_lists_and_detail(staff_api, p4):
    case, director = p4.case, p4.people.director
    summary = _get(staff_api, director, "summary/").json()
    assert summary["sections"]["cases"] is True and summary["counts"]["my_stages"] == 1

    mine = _get(staff_api, director, "cases/my-stages/").json()
    assert set(mine) == {"results", "next", "previous", "count"} and mine["count"] == 1
    row = mine["results"][0]
    assert row["case"]["id"] == case.pk and row["stage_code"] == "case_opened" and row["task"]["reference"]

    board = _get(staff_api, director, "cases/", stage="setup", mine="1").json()
    assert [c["id"] for c in board["results"]] == [case.pk]
    assert board["results"][0]["holders"][0]["id"] == director.pk
    assert _get(staff_api, director, "cases/", stage="visit").json()["count"] == 0
    assert _get(staff_api, director, "cases/", institution=case.institution.code).json()["count"] == 1

    detail = _get(staff_api, director, f"cases/{case.pk}/").json()
    assert detail["reference"] == case.reference and detail["institution"]["code"] == case.institution.code
    stage = detail["current_stages"][0]
    assert stage["code"] == "case_opened" and stage["can_act"] is True and stage["can_return"] is False
    assert stage["problems"]  # the desk officer is not chosen yet
    assert any(r["state"] == "current" for r in detail["progress"])
    assert len(detail["programmes"]) == case.programme_count
    assert detail["timeline"]

    # Someone else who can see the case cannot act on it.
    other = _get(staff_api, p4.people.desk_officer, f"cases/{case.pk}/").json()["current_stages"][0]
    assert other["can_act"] is False and "Bello" in other["why_not"]


def test_case_bad_query_is_400(staff_api, p4):
    r = _get(staff_api, p4.people.director, "cases/my-stages/", kind="nope")
    assert r.status_code == 400 and "kind" in err(r)["fields"]


def test_case_act_rules(staff_api, p4):
    from accreditation import case_services as cs

    case, people = p4.case, p4.people
    url = f"cases/{case.pk}/act/"
    r = _post(staff_api, people.director, url, {"stage": "case_opened", "action": "approve"})
    assert r.status_code == 400 and "desk officer" in err(r)["message"]
    r = _post(staff_api, people.director, url, {"stage": "case_opened", "action": "return"})
    assert r.status_code == 400 and "remark" in err(r)["fields"]
    r = _post(staff_api, people.director, url, {"stage": "case_opened", "action": "jump"})
    assert r.status_code == 400 and "action" in err(r)["fields"]
    r = _post(staff_api, people.desk_officer, url, {"stage": "case_opened", "action": "approve"})
    assert r.status_code == 403 and err(r)["code"] == "permission_denied" and "Bello" in err(r)["message"]
    r = _post(staff_api, people.ict_monitor, url, {"stage": "case_opened", "action": "approve"})
    assert r.status_code == 403 and "R-13" in err(r)["message"]

    cs.set_desk_officer(case, user=people.director, officer=people.desk_officer)
    r = _post(staff_api, people.director, url, {"stage": "case_opened", "action": "approve", "remark": "Go"})
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["message"].startswith("Done.")
    assert {s["code"] for s in body["case"]["current_stages"]} == {
        "assign_audit_officer", "assign_finance_officer", "assign_ict_officer"}
    assert holder(case, "assign_audit_officer") == people.audit_director


def test_case_out_of_scope_is_404(staff_api, p4, make_user):
    outsider = make_user("vtsd.api@t.local", role="officer", dept="vtsd")
    assert _get(staff_api, outsider, f"cases/{p4.case.pk}/").status_code == 404
    r = _post(staff_api, outsider, f"cases/{p4.case.pk}/act/", {"stage": "case_opened", "action": "approve"})
    assert r.status_code == 404 and err(r)["code"] == "not_found"
    assert _get(staff_api, outsider, "cases/").json()["count"] == 0


def test_case_attach_and_download(staff_api, p4, make_user):
    case, director = p4.case, p4.people.director
    url = f"cases/{case.pk}/documents/"
    r = _post(staff_api, director, url, {"stage": "case_opened"}, fmt="multipart")
    assert r.status_code == 400 and "file" in err(r)["fields"]
    bad = SimpleUploadedFile("tool.exe", b"MZ\x90\x00", content_type="application/octet-stream")
    r = _post(staff_api, director, url, {"stage": "case_opened", "file": bad}, fmt="multipart")
    assert r.status_code == 400 and "file" in err(r)["fields"]
    r = _post(staff_api, p4.people.desk_officer, url, {"stage": "case_opened", "file": pdf_upload("x.pdf")},
              fmt="multipart")
    assert r.status_code == 403
    r = _post(staff_api, director, url, {"stage": "case_opened", "file": pdf_upload("minutes.pdf"),
                                         "description": "Opening minutes"}, fmt="multipart")
    assert r.status_code == 201, r.content
    doc = r.json()
    assert doc["kind"] == "tasks.taskattachment" and doc["name"] == "minutes.pdf" and doc["stage_code"] == "case_opened"
    docs = _get(staff_api, director, f"cases/{case.pk}/").json()["documents"]
    assert [d["id"] for d in docs] == [doc["id"]]
    download = bearer(staff_api, director).get(doc["download_url"])
    assert download.status_code == 200 and b"".join(download.streaming_content).startswith(PDF[:5])
    outsider = make_user("vtsd.files@t.local", role="officer", dept="vtsd")
    r = bearer(staff_api, outsider).get(doc["download_url"])
    assert r.status_code == 404
    r = _get(staff_api, director, "files/accounts.user/1/")
    assert r.status_code == 404


# ===========================================================================
# Confirmations
# ===========================================================================
def _start_setting_change(user):
    from config.models import ConfigEntry
    from qa_core import actions

    return actions.initiate("config.update_setting", actor=user,
                            target=ConfigEntry.objects.get(key="qa.impersonation.max_minutes", scope="global"),
                            payload={"value": "20"}, reason="Shorter sessions")


def test_confirmation_list_detail_and_approve(staff_api, officer, director):
    pa = _start_setting_change(officer)
    data = _get(staff_api, director, "confirmations/").json()
    assert data["count"] == 1 and data["counts"]["pending"] == 1
    row = data["results"][0]
    assert row["id"] == pa.pk and row["can_decide"] is True and row["initiated_by"]["id"] == officer.pk
    assert _get(staff_api, officer, "confirmations/", tab="started_by_me").json()["count"] == 1
    assert _get(staff_api, director, "confirmations/", tab="started_by_me").json()["count"] == 0
    assert _get(staff_api, director, "confirmations/", tab="nope").status_code == 400

    detail = _get(staff_api, director, f"confirmations/{pa.pk}/").json()
    assert detail["diff"] == [{"label": "Value", "before": "30", "after": "20", "changed": True}]
    assert detail["stale"] is False and detail["can_withdraw"] is False and detail["history"]

    r = _post(staff_api, officer, f"confirmations/{pa.pk}/approve/", {"note": "self"})
    assert r.status_code == 403 and "different officer" in err(r)["message"]
    r = _post(staff_api, director, f"confirmations/{pa.pk}/reject/", {"note": " "})
    assert r.status_code == 400 and "note" in err(r)["fields"]
    r = _post(staff_api, director, f"confirmations/{pa.pk}/approve/", {"note": "Agreed"})
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["confirmation"]["status"] == "applied" and "confirmed and applied" in body["message"]
    assert body["confirmation"]["decision_remark"] == "Agreed"
    r = _post(staff_api, director, f"confirmations/{pa.pk}/approve/")
    assert r.status_code == 403  # already decided
    assert _get(staff_api, director, "confirmations/", tab="applied").json()["count"] == 1


def test_confirmation_reject_withdraw_and_scope(staff_api, officer, director, make_user):
    pa = _start_setting_change(officer)
    r = _post(staff_api, director, f"confirmations/{pa.pk}/reject/", {"note": "Not now"})
    assert r.status_code == 200 and r.json()["confirmation"]["status"] == "rejected"

    pa2 = _start_setting_change(officer)
    r = _post(staff_api, director, f"confirmations/{pa2.pk}/withdraw/")
    assert r.status_code == 403
    r = _post(staff_api, officer, f"confirmations/{pa2.pk}/withdraw/")
    assert r.status_code == 200 and r.json()["confirmation"]["status"] == "withdrawn"

    from django.contrib.auth.models import Permission

    outsider = make_user("vtsd.pending@t.local", role="officer", dept="vtsd")
    outsider.user_permissions.add(Permission.objects.get(codename="view_pending_actions",
                                                         content_type__app_label="qa_core"))
    from accounts.models import User

    outsider = User.objects.get(pk=outsider.pk)
    assert _get(staff_api, outsider, "confirmations/").status_code == 200  # section open, other department
    assert _get(staff_api, outsider, f"confirmations/{pa.pk}/").status_code == 404
    assert _post(staff_api, outsider, f"confirmations/{pa.pk}/approve/").status_code == 404


# ===========================================================================
# Finance
# ===========================================================================
@pytest.fixture
def claimed(bill, inst_user):
    from payments import services
    from payments.models import PaymentObligation

    services.submit_proof(bill, user=inst_user, upload=pdf_upload("teller.pdf"), amount_claimed=bill.amount,
                          bank_name="CBN", teller_number="T-001", paid_on=None)
    return PaymentObligation.unscoped.get(pk=bill.pk)


def test_finance_queue_two_step(staff_api, claimed, p2):
    from payments.models import PaymentVerification

    item = PaymentVerification.unscoped.get(obligation=claimed, is_open=True)
    data = _get(staff_api, p2.fin_officer, "finance/").json()
    assert data["count"] == 1 and data["summary"]["role"] == "officer"
    row = data["results"][0]
    assert row["id"] == item.pk and row["reference"] == claimed.reference and row["decision"] is None
    assert _get(staff_api, p2.fin_officer, "finance/", stage="final").json()["count"] == 0

    detail = _get(staff_api, p2.fin_officer, f"finance/{item.pk}/").json()
    assert detail["rights"]["can_first"] is True and detail["rights"]["can_final"] is False
    proof = detail["proofs"][0]
    assert proof["teller_number"] == "T-001" and proof["file"]["kind"] == "payments.proofofpayment"
    assert bearer(staff_api, p2.fin_officer).get(proof["file"]["download_url"]).status_code == 200

    r = _post(staff_api, p2.fin_officer, f"finance/{item.pk}/first-pass/", {"decision": "approved"})
    assert r.status_code == 400 and "amount" in err(r)["fields"]
    r = _post(staff_api, p2.fin_officer, f"finance/{item.pk}/first-pass/", {"decision": "rejected"})
    assert r.status_code == 400 and "reason" in err(r)["fields"]
    r = _post(staff_api, p2.fin_officer, f"finance/{item.pk}/first-pass/",
              {"decision": "approved", "amount": str(claimed.amount)})
    assert r.status_code == 200, r.content
    assert r.json()["item"]["first_pass"]["decision"] == "approved"

    r = _post(staff_api, p2.fin_officer, f"finance/{item.pk}/decide/", {"decision": "confirmed"})
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"
    r = _post(staff_api, p2.fin_director, f"finance/{item.pk}/decide/", {"decision": "rejected"})
    assert r.status_code == 400 and "reason" in err(r)["fields"]
    r = _post(staff_api, p2.fin_director, f"finance/{item.pk}/decide/", {"decision": "confirmed"})
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["item"]["is_open"] is False and body["item"]["final"]["decision"] == "confirmed"
    assert body["item"]["advices"] and "confirmed" in body["message"]


def test_finance_closed_to_programme_officers(staff_api, claimed, p2):
    from payments.models import PaymentVerification

    item = PaymentVerification.unscoped.get(obligation=claimed, is_open=True)
    r = _get(staff_api, p2.officer, f"finance/{item.pk}/")
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"


# ===========================================================================
# Institutions
# ===========================================================================
def test_institutions_scope(staff_api, qa_seed, make_institution, make_user):
    mine = make_institution("APX")
    make_institution("VTX", dept_code="vtsd")
    officer = make_user("inst.officer@t.local", role="officer", dept="dpap")
    data = _get(staff_api, officer, "institutions/").json()
    assert [r["code"] for r in data["results"]] == ["APX"]
    assert _get(staff_api, officer, "institutions/", q="nothing-like-this").json()["count"] == 0
    assert _get(staff_api, officer, "institutions/", sort="weird").status_code == 400
    detail = _get(staff_api, officer, "institutions/apx/").json()
    assert detail["id"] == mine.pk and detail["handling_department"]["name"]
    assert detail["programmes"] == [] and detail["open_cases"] == [] and isinstance(detail["documents"], list)
    assert _get(staff_api, officer, "institutions/VTX/").status_code == 404


# ===========================================================================
# Applications (departmental clearance)
# ===========================================================================
def test_clearance_queue_and_decision(staff_api, submitted_app, p2, make_user):
    from accreditation.models import DepartmentalClearance

    data = _get(staff_api, p2.legal, "applications/clearance/").json()
    assert data["count"] == 1
    row = data["results"][0]
    assert row["application"]["id"] == submitted_app.pk and row["can_decide"] is True
    clearance = DepartmentalClearance.unscoped.get(pk=row["id"])

    detail = _get(staff_api, p2.legal, f"applications/clearance/{clearance.pk}/").json()
    assert detail["documents"] and all(d["file"] for d in detail["documents"])
    assert len(detail["other_departments"]) == 3

    r = _post(staff_api, p2.aspd, f"applications/clearance/{clearance.pk}/decide/",
              {"decision": "clear"})
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"
    r = _post(staff_api, p2.legal, f"applications/clearance/{clearance.pk}/decide/", {"decision": "return"})
    assert r.status_code == 400 and "remark" in err(r)["fields"]
    r = _post(staff_api, p2.legal, f"applications/clearance/{clearance.pk}/decide/", {"decision": "clear"})
    assert r.status_code == 200, r.content
    assert r.json()["clearance"]["status"] == "cleared"

    stranger = make_user("ict.clear@t.local", role="officer", dept="ict")
    assert _get(staff_api, stranger, f"applications/clearance/{clearance.pk}/").status_code in (403, 404)


# ===========================================================================
# QA requests and SSQs
# ===========================================================================
def test_requests_and_ssq_review(staff_api, p4, make_user):
    req = p4.case.qa_request
    director = p4.people.director
    data = _get(staff_api, director, "requests/").json()
    assert req.pk in [r["id"] for r in data["results"]]
    detail = _get(staff_api, director, f"requests/{req.pk}/").json()
    assert detail["case"]["id"] == p4.case.pk and detail["ssqs"]
    ssq_id = detail["ssqs"][0]["id"]
    ssq = _get(staff_api, director, f"requests/ssq/{ssq_id}/").json()
    assert isinstance(ssq["sections"], list) and ssq["versions"]
    r = _post(staff_api, director, f"requests/ssq/{ssq_id}/decide/", {"decision": "return"})
    assert r.status_code == 400 and "remark" in err(r)["fields"]
    r = _post(staff_api, director, f"requests/{req.pk}/clearance/", {"decision": "clear"})
    assert r.status_code in (400, 403)  # the request is long past programme clearance
    outsider = make_user("vtsd.req@t.local", role="officer", dept="vtsd")
    assert _get(staff_api, outsider, f"requests/{req.pk}/").status_code in (403, 404)
    assert _get(staff_api, outsider, f"requests/ssq/{ssq_id}/").status_code in (403, 404)


# ===========================================================================
# Complaints
# ===========================================================================
@pytest.fixture
def complaint_world(qa_seed, make_institution, make_user):
    from complaints import services
    from complaints.models import ComplaintLayer

    inst = make_institution("CMP")
    raiser = make_user("admin@cmp.local", user_type="institution", institution=inst, role="inst-admin")
    fin = make_user("fo@cmp-fin.local", role="qa-finance-officer", dept="finance")
    dpap = make_user("o@cmp-dpap.local", role="officer", dept="dpap")
    complaint = services.raise_complaint(raiser, layer=ComplaintLayer.objects.get(code="payment-issues"),
                                         subject="Payment not confirmed after a week",
                                         body="We paid and uploaded the teller a week ago.")
    return {"complaint": complaint, "fin": fin, "dpap": dpap, "raiser": raiser}


def test_complaints_queue_reply_and_act(staff_api, complaint_world):
    c, fin = complaint_world["complaint"], complaint_world["fin"]
    data = _get(staff_api, fin, "complaints/", tab="all").json()
    assert [r["id"] for r in data["results"]] == [c.pk] and set(data["counts"]) >= {"mine", "breached"}
    assert data["tab"] == "all"
    detail = _get(staff_api, fin, f"complaints/{c.pk}/").json()
    assert detail["rights"]["can_write_internal"] is True and detail["body"].startswith("We paid")

    r = _post(staff_api, fin, f"complaints/{c.pk}/messages/", {"body": "x"})
    assert r.status_code == 400 and "body" in err(r)["fields"]
    r = _post(staff_api, fin, f"complaints/{c.pk}/messages/", {"body": "Checking the statement.",
                                                               "visibility": "internal"})
    assert r.status_code == 201, r.content
    msgs = r.json()["complaint"]["messages"]
    assert msgs[-1]["visibility"] == "internal" and msgs[-1]["author"]["id"] == fin.pk
    r = _post(staff_api, fin, f"complaints/{c.pk}/messages/", {"body": "We are on it.", "visibility": "public",
                                                               "files": pdf_upload("note.pdf")}, fmt="multipart")
    assert r.status_code == 201, r.content
    att = r.json()["complaint"]["messages"][-1]["attachments"]
    assert att and att[0]["kind"] == "complaints.complaintattachment"

    r = _post(staff_api, fin, f"complaints/{c.pk}/act/", {"action": "teleport"})
    assert r.status_code == 400 and "action" in err(r)["fields"]
    codes = [a["code"] for a in detail["rights"]["actions"]]
    if codes:
        first = detail["rights"]["actions"][0]
        r = _post(staff_api, fin, f"complaints/{c.pk}/act/", {"action": first["code"], "remark": "On it now"})
        assert r.status_code in (200, 400), r.content
    r = _post(staff_api, fin, f"complaints/{c.pk}/escalate/", {"reason": ""})
    assert r.status_code == 400 and "reason" in err(r)["fields"]


def test_complaints_scope(staff_api, complaint_world):
    c, dpap = complaint_world["complaint"], complaint_world["dpap"]
    r = _get(staff_api, dpap, f"complaints/{c.pk}/")
    assert r.status_code in (403, 404)
    if r.status_code == 404:
        assert _post(staff_api, dpap, f"complaints/{c.pk}/messages/", {"body": "hello"}).status_code == 404


# ===========================================================================
# Inspections
# ===========================================================================
def test_inspections_mine(staff_api, p4, make_user):
    from inspection.models import Inspection

    advance_to(p4, 11)
    inspection = Inspection.unscoped.get(case=p4.case)
    lead = inspection.team_lead
    data = _get(staff_api, lead, "inspections/").json()
    assert [r["id"] for r in data["results"]] == [inspection.pk] and data["results"][0]["my_role"] == "Team lead"
    detail = _get(staff_api, lead, f"inspections/{inspection.pk}/").json()
    assert detail["team"] and detail["case"]["id"] == p4.case.pk
    assert _get(staff_api, p4.people.es, "inspections/").json()["count"] == 0  # not on the team
    assert _get(staff_api, p4.people.es, "inspections/", mine="0").json()["count"] == 1
    outsider = make_user("vtsd.insp@t.local", role="officer", dept="vtsd")
    assert _get(staff_api, outsider, f"inspections/{inspection.pk}/").status_code == 404


# ===========================================================================
# Schema
# ===========================================================================
def test_schema_lists_the_qa_routes():
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator(urlconf="nbte_ict.urls").get_schema(request=None, public=True)
    paths = [p for p in schema["paths"] if p.startswith("/api/v1/qa/")]
    assert len(paths) == 35
    portal = SchemaGenerator(urlconf="qa_core.portal_urls").get_schema(request=None, public=True)
    assert not [p for p in portal["paths"] if p.startswith("/api/v1/qa/")]
