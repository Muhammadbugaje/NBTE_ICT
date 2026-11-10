"""Portal door — QA requests, the SSQ (offline partial save, evidence,
submit / revise), queries, programmes and outcome letters."""
import pytest

from qa_core.tests.phase2_fixtures import pdf_upload
from qa_core.tests.phase3_fixtures import complete_ssq, make_request

from .conftest import BASE, bearer, err

pytestmark = pytest.mark.django_db


def _api(portal_api, user):
    return bearer(portal_api, user)


# ---------------------------------------------------------------------------
# QA requests
# ---------------------------------------------------------------------------
def test_new_request_choices(portal_api, p3):
    api = _api(portal_api, p3.admin)
    body = api.get(BASE + "qa-requests/new/").json()
    assert body["can_create"] is True and body["programmes"] == []
    assert any(t["code"] == "reaccreditation" for t in body["types"])
    body = api.get(BASE + "qa-requests/new/?type=reaccreditation").json()
    by_name = {p["name"]: p for p in body["programmes"]}
    assert by_name["Computer Science"]["eligible"] is True
    assert by_name["Mechanical Engineering Technology"]["eligible"] is False
    assert "Too early" in by_name["Mechanical Engineering Technology"]["reason"]
    # department accounts cannot start requests (the website answers 404)
    assert _api(portal_api, p3.dept).get(BASE + "qa-requests/new/").status_code == 404


def test_create_list_detail_submit(portal_api, p3):
    api = _api(portal_api, p3.admin)
    r = api.post(BASE + "qa-requests/", {"type": "nope", "programmes": [p3.cs.pk]}, format="json")
    assert r.status_code == 400 and "type" in err(r)["fields"]
    r = api.post(BASE + "qa-requests/", {"type": "reaccreditation", "programmes": [p3.far.pk]}, format="json")
    assert r.status_code == 400 and err(r)["code"] == "validation_error"
    r = api.post(BASE + "qa-requests/", {"type": "reaccreditation", "programmes": [p3.cs.pk, p3.eee.pk]},
                 format="json")
    assert r.status_code == 201, r.json()
    req = r.json()
    # v2.1 X12: the administrator (Head of ICT) prepares; only the Chief Executive submits.
    assert req["status"] == "draft" and req["can_submit"] is False and len(req["programmes"]) == 2
    assert req["estimate"]["total"] is not None and req["next_step"]
    listing = api.get(BASE + "qa-requests/").json()
    assert listing["count"] == 1 and listing["results"][0]["reference"] == req["reference"]
    assert api.get(BASE + "qa-requests/?show=closed").json()["count"] == 0
    assert api.post(BASE + f"qa-requests/{req['id']}/submit/", {"declaration": True},
                    format="json").status_code == 403
    from rest_framework.test import APIClient

    api = bearer(APIClient(HTTP_HOST=portal_api.defaults["HTTP_HOST"]), p3.ceo)
    r = api.post(BASE + f"qa-requests/{req['id']}/submit/", {}, format="json")
    assert r.status_code == 400
    r = api.post(BASE + f"qa-requests/{req['id']}/submit/", {"declaration": True}, format="json")
    assert r.status_code == 200, r.json()
    assert r.json()["status"] != "draft" and r.json()["fees"]


def test_request_isolation_and_department(portal_api, portal_host, p3):
    from rest_framework.test import APIClient

    req = make_request(p3)
    other = bearer(APIClient(HTTP_HOST=portal_host), p3.other_admin)
    assert other.get(BASE + f"qa-requests/{req.pk}/").status_code == 404
    assert other.post(BASE + f"qa-requests/{req.pk}/cancel/", {"reason": "x"}, format="json").status_code == 404
    dept = bearer(APIClient(HTTP_HOST=portal_host), p3.dept)
    assert dept.get(BASE + f"qa-requests/{req.pk}/").status_code == 200
    assert dept.post(BASE + f"qa-requests/{req.pk}/submit/", {"declaration": True},
                     format="json").status_code == 404


def test_programme_changes_on_draft(portal_api, p3):
    api = _api(portal_api, p3.admin)
    req = make_request(p3, [p3.cs])
    body = api.get(BASE + f"qa-requests/{req.pk}/").json()
    assert body["can_add"] is True and any(c["id"] == p3.eee.pk for c in body["candidates"])
    r = api.post(BASE + f"qa-requests/{req.pk}/programmes/", {"programme": p3.bravo_prog.pk}, format="json")
    assert r.status_code == 400 and "programme" in err(r)["fields"]
    r = api.post(BASE + f"qa-requests/{req.pk}/programmes/", {"programme": p3.eee.pk}, format="json")
    assert r.status_code == 200 and len([p for p in r.json()["programmes"] if p["status"] == "active"]) == 2
    row = next(p for p in r.json()["programmes"] if p["programme"]["id"] == p3.eee.pk)["row_id"]
    r = api.post(BASE + f"qa-requests/{req.pk}/programmes/{row}/drop/", {"reason": "Not ready"}, format="json")
    assert r.status_code == 200
    assert next(p for p in r.json()["programmes"] if p["row_id"] == row)["status"] != "active"
    assert api.post(BASE + f"qa-requests/{req.pk}/programmes/999999/drop/", {}, format="json").status_code == 404
    r = api.post(BASE + f"qa-requests/{req.pk}/cancel/", {"reason": "Changed our minds"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"


def test_estimate(portal_api, p3):
    body = _api(portal_api, p3.admin).get(
        BASE + f"qa-requests/estimate/?type=reaccreditation&programmes={p3.cs.pk},{p3.eee.pk}").json()
    assert body["count"] == 2 and body["estimate"]["total"] is not None


# ---------------------------------------------------------------------------
# SSQ
# ---------------------------------------------------------------------------
def _first_ssq(req):
    from accreditation.models import SSQ

    return SSQ.unscoped.filter(qa_request_programme__qa_request=req, is_current=True).order_by("pk").first()


def test_ssq_read_partial_save_and_submit(portal_api, portal_host, p3, paid_request):
    from rest_framework.test import APIClient

    ssq = _first_ssq(paid_request)
    api = _api(portal_api, p3.admin)
    url = BASE + f"qa-requests/{paid_request.pk}/ssq/{ssq.pk}/"
    body = api.get(url).json()
    assert body["status"] == "draft" and body["sections"] and body["problems"]
    keys = {f["key"]: f for s in body["sections"] for f in s["fields"]}
    assert "enrolment" in keys and keys["enrolment"]["type"] == "table" and keys["enrolment"]["columns"]
    # v2.1 X12: the administrator edits; only the Chief Executive submits.
    assert body["can_submit"] is False and body["can_send_department"] is True
    # partial save: one good value, one bad, an unknown key ignored
    r = api.patch(url, {"answers": {"head_of_department": "Dr. A. Musa", "running_streams": "abc",
                                    "no_such_field": "x"}}, format="json")
    assert r.status_code == 200, r.json()
    res = r.json()
    assert res["saved"] == ["head_of_department"] and "running_streams" in res["errors"]
    ssq.refresh_from_db()
    assert ssq.data["head_of_department"] == "Dr. A. Musa"
    r = api.patch(url, {"answers": "nope"}, format="json")
    assert r.status_code == 400 and "answers" in err(r)["fields"]
    # submit needs every required answer, then the declaration (Chief Executive)
    api = bearer(APIClient(HTTP_HOST=portal_host), p3.ceo)
    r = api.post(url + "submit/", {"declaration": True}, format="json")
    assert r.status_code == 400
    complete_ssq(ssq, p3.admin)
    r = api.post(url + "submit/", {}, format="json")
    assert r.status_code == 400
    r = api.post(url + "submit/", {"declaration": True}, format="json")
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "submitted" and r.json()["can_submit"] is False
    # read-only now: saving is refused with the service's message
    r = api.patch(url, {"answers": {"head_of_department": "Changed"}}, format="json")
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"
    # another institution
    other = bearer(APIClient(HTTP_HOST=portal_host), p3.other_admin)
    assert other.get(url).status_code == 404


def test_ssq_department_cannot_edit_until_sent(portal_api, portal_host, p3, paid_request):
    from rest_framework.test import APIClient

    ssq = _first_ssq(paid_request)
    url = BASE + f"qa-requests/{paid_request.pk}/ssq/{ssq.pk}/"
    dept = bearer(APIClient(HTTP_HOST=portal_host), p3.dept)
    r = dept.patch(url, {"answers": {"head_of_department": "X"}}, format="json")
    assert r.status_code == 403
    admin = _api(portal_api, p3.admin)
    section = admin.get(url).json()["sections"][0]["key"]
    r = admin.post(url + "department/", {"department_user": p3.dept.pk, "sections": [section], "remark": "Pls"},
                   format="json")
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "returned_to_department" and r.json()["can_recall"] is True
    body = dept.get(url).json()
    assert body["editable_sections"] == [section] and body["can_department_submit"] is True
    r = admin.post(url + "recall/", format="json")
    assert r.status_code == 200 and r.json()["status"] == "draft"


def test_ssq_evidence_upload_and_remove(portal_api, p3, paid_request):
    ssq = _first_ssq(paid_request)
    api = _api(portal_api, p3.admin)
    url = BASE + f"qa-requests/{paid_request.pk}/ssq/{ssq.pk}/"
    body = api.get(url).json()
    file_field = next((f for s in body["sections"] for f in s["fields"] if f["type"] == "file"), None)
    if file_field is None:
        pytest.skip("The seeded template has no file field.")
    r = api.post(url + f"evidence/{file_field['key']}/", {}, format="multipart")
    assert r.status_code == 400 and "file" in err(r)["fields"]
    r = api.post(url + f"evidence/{file_field['key']}/", {"file": pdf_upload("ev.pdf")}, format="multipart")
    assert r.status_code == 201, r.json()
    field = next(f for s in r.json()["sections"] for f in s["fields"] if f["key"] == file_field["key"])
    ev = field["evidence"][0]
    assert ev["file"]["download_url"].startswith("/api/v1/files/accreditation.evidence/")
    assert api.get(ev["file"]["download_url"]).status_code == 200
    r = api.delete(url + f"evidence/{ev['id']}/")
    assert r.status_code == 200
    assert api.delete(url + f"evidence/{ev['id']}/").status_code == 404


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def test_query_reply(portal_api, portal_host, p3, paid_request):
    from rest_framework.test import APIClient

    from accreditation import qa_services as svc

    ssq = _first_ssq(paid_request)
    query = svc.raise_query(ssq, user=p3.p2.officer, title="Staff list", body="Send the staff payroll.")
    api = _api(portal_api, p3.admin)
    listing = api.get(BASE + "queries/").json()
    assert listing["count"] == 1 and listing["results"][0]["status"] == "open"
    body = api.get(BASE + f"queries/{query.pk}/").json()
    assert body["messages"][0]["from_nbte"] is True and body["can_respond"] is True
    r = api.post(BASE + f"queries/{query.pk}/reply/", {"body": "x"}, format="multipart")
    assert r.status_code == 400
    r = api.post(BASE + f"queries/{query.pk}/reply/", {"body": "Attached, as asked.", "file": pdf_upload("p.pdf")},
                 format="multipart")
    assert r.status_code == 201, r.json()
    msg = r.json()["messages"][-1]
    assert msg["from_nbte"] is False and msg["attachment"]["download_url"]
    assert api.get(msg["attachment"]["download_url"]).status_code == 200
    assert r.json()["status"] == "answered"
    other = bearer(APIClient(HTTP_HOST=portal_host), p3.other_admin)
    assert other.get(BASE + f"queries/{query.pk}/").status_code == 404
    assert other.post(BASE + f"queries/{query.pk}/reply/", {"body": "Hello there"}).status_code == 404


# ---------------------------------------------------------------------------
# Programmes and letters
# ---------------------------------------------------------------------------
def test_programme_board_list_detail(portal_api, portal_host, p3):
    from rest_framework.test import APIClient

    api = _api(portal_api, p3.admin)
    board = api.get(BASE + "programmes/board/").json()
    cards = {c["name"]: c for c in board["cards"]}
    assert cards["Computer Science"]["urgency"] == "ok" or cards["Computer Science"]["urgency"] == "soon"
    assert cards["Statistics"]["urgency"] == "expired"
    assert board["renewal_type"]["code"] == "reaccreditation"
    listing = api.get(BASE + "programmes/?q=computer").json()
    assert listing["count"] == 1 and listing["results"][0]["id"] == p3.cs.pk
    detail = api.get(BASE + f"programmes/{p3.cs.pk}/").json()
    assert detail["name"] == "Computer Science" and detail["requests"] == [] and "history" in detail
    assert api.get(BASE + f"programmes/{p3.bravo_prog.pk}/").status_code == 404
    other = bearer(APIClient(HTTP_HOST=portal_host), p3.other_admin)
    assert other.get(BASE + f"programmes/{p3.cs.pk}/").status_code == 404
    assert api.get(BASE + "letters/").json()["count"] == 0
    assert api.get(BASE + "letters/999999/download/").status_code == 404
