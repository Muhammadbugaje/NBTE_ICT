"""Portal door — complaints (raise, read, reply, actions, files, no internal
notes) and resource persons (engagements, findings, history, profile)."""
import pytest
from rest_framework.test import APIClient

from qa_core.tests.phase2_fixtures import pdf_upload

from .conftest import BASE, bearer, err

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Complaints
# ---------------------------------------------------------------------------
def test_complaint_raise_read_reply(portal_api, portal_host, p3, bill, inst_user):
    from complaints.models import ComplaintLayer

    api = bearer(portal_api, inst_user)
    form = api.get(BASE + f"complaints/new/?about=payment:{bill.pk}").json()
    layer = ComplaintLayer.objects.get(code="payment-issues")
    assert form["suggested_layer"] == layer.pk and form["related_label"]
    r = api.post(BASE + "complaints/", {"layer": layer.pk, "subject": "Hi", "body": "Short"}, format="json")
    assert r.status_code == 400 and "subject" in err(r)["fields"]
    r = api.post(BASE + "complaints/", {"layer": 999999, "subject": "Payment not confirmed",
                                        "body": "We paid a week ago and nothing moved."}, format="json")
    assert r.status_code == 400 and "layer" in err(r)["fields"]
    r = api.post(BASE + "complaints/", {"layer": layer.pk, "subject": "Payment not confirmed",
                                        "body": "We paid a week ago and nothing moved.", "about": f"payment:{bill.pk}",
                                        "files": [pdf_upload("teller.pdf")]}, format="multipart")
    assert r.status_code == 201, r.json()
    c = r.json()
    assert c["reference"] and c["is_open"] and c["related_label"]
    assert c["messages"] and c["messages"][0]["attachments"]
    file_url = c["messages"][0]["attachments"][0]["download_url"]
    assert api.get(file_url).status_code == 200
    # an internal note never reaches the app
    from complaints.models import ComplaintMessage

    ComplaintMessage.objects.create(complaint_id=c["id"], author=p3.p2.officer, visibility="internal",
                                    body="INTERNAL: check the bank statement")
    body = api.get(BASE + f"complaints/{c['id']}/").json()
    assert not any("INTERNAL" in m["body"] for m in body["messages"])
    r = api.post(BASE + f"complaints/{c['id']}/reply/", {"body": "Any update?"}, format="json")
    assert r.status_code == 201 and r.json()["messages"][-1]["mine"] is True
    assert api.get(BASE + "complaints/").json()["count"] == 1
    r = api.post(BASE + f"complaints/{c['id']}/actions/delete/", {}, format="json")
    assert r.status_code == 403
    # other institution and resource persons cannot see it
    other = bearer(APIClient(HTTP_HOST=portal_host), p3.admin)
    assert other.get(BASE + f"complaints/{c['id']}/").status_code == 404
    assert other.get(file_url).status_code == 404
    assert other.post(BASE + f"complaints/{c['id']}/reply/", {"body": "Hello there"},
                      format="json").status_code == 404


def test_complaint_withdraw(portal_api, p3):
    from complaints.models import ComplaintLayer

    api = bearer(portal_api, p3.admin)
    layer = ComplaintLayer.objects.get(code="payment-issues")
    c = api.post(BASE + "complaints/", {"layer": layer.pk, "subject": "Payment not confirmed",
                                        "body": "We paid a week ago and nothing moved."}, format="json").json()
    codes = [a["code"] for a in c["actions"]]
    if "withdraw" not in codes:
        pytest.skip("withdraw is not offered at this stage by the seeded flow")
    r = api.post(BASE + f"complaints/{c['id']}/actions/withdraw/", {"remark": "Sorted"}, format="json")
    assert r.status_code == 200 and r.json()["is_open"] is False


def test_suggest_and_help(portal_api, p3):
    api = bearer(portal_api, p3.admin)
    assert "results" in api.get(BASE + "complaints/suggest/?subject=payment+not+confirmed").json()
    articles = api.get(BASE + "help/").json()["results"]
    if articles:
        slug = articles[0]["slug"]
        assert api.get(BASE + f"help/{slug}/").json()["body"]
        assert api.post(BASE + f"help/{slug}/vote/", {"helpful": True}, format="json").status_code == 204
    assert api.get(BASE + "help/no-such-article/").status_code == 404


# ---------------------------------------------------------------------------
# Resource persons
# ---------------------------------------------------------------------------
def _rp_user(p5_visit):
    m = p5_visit.mappings[0]
    user = m.resource_person.user
    user.must_change_password = False
    user.save(update_fields=["must_change_password"])
    return m, user


def test_engagements_and_findings(portal_api, portal_host, p5_visit):
    m, user = _rp_user(p5_visit)
    api = bearer(portal_api, user)
    listing = api.get(BASE + "engagements/").json()
    assert listing["count"] >= 1
    assert any(row["id"] == m.pk for row in listing["results"])
    detail = api.get(BASE + f"engagements/{m.pk}/").json()
    assert detail["institution"] == m.case.institution.name and detail["role"]
    assert detail["visit"] is not None and detail["visit"]["reference"]
    assert api.get(BASE + "engagements/?status=released").json()["count"] == 0
    if detail["can_write_findings"]:
        r = api.post(BASE + f"engagements/{m.pk}/findings/", {"findings": "Too short"}, format="json")
        assert r.status_code == 400 and "findings" in err(r)["fields"]
        text = "Workshops are adequately equipped; library needs more titles."
        r = api.post(BASE + f"engagements/{m.pk}/findings/", {"findings": text}, format="json")
        assert r.status_code == 200 and r.json()["findings"] == text
    else:
        r = api.post(BASE + f"engagements/{m.pk}/findings/", {"findings": "x" * 30}, format="json")
        assert r.status_code == 400
    # someone else's engagement
    others = [x for x in p5_visit.mappings if x.resource_person_id != m.resource_person_id]
    if others:
        assert api.get(BASE + f"engagements/{others[0].pk}/").status_code == 404
        assert api.post(BASE + f"engagements/{others[0].pk}/findings/", {"findings": "x" * 30},
                        format="json").status_code == 404


def test_service_history_and_profile(portal_api, p5_visit):
    m, user = _rp_user(p5_visit)
    api = bearer(portal_api, user)
    hist = api.get(BASE + "service-history/").json()
    assert set(hist) == {"results", "next", "previous", "count"}
    summary = api.get(BASE + "service-history/summary/").json()
    assert {"visits", "ssqs", "years", "duties"} <= set(summary)
    profile = api.get(BASE + "profile/").json()
    assert profile["full_name"] == m.resource_person.full_name and profile["editable_fields"] == []
    r = api.post(BASE + "profile/files/", {"kind": "cv"}, format="multipart")
    assert r.status_code == 400 and "file" in err(r)["fields"]
    r = api.post(BASE + "profile/files/", {"kind": "cv", "file": pdf_upload("cv.pdf")}, format="multipart")
    assert r.status_code == 201 and r.json()["files"][0]["kind"] == "cv"
    # resource persons see no institution data
    assert api.get(BASE + "payments/").status_code == 404
    assert api.get(BASE + "documents/").status_code == 404


def test_institution_inspections(portal_api, p5_visit):
    from accounts.models import User

    inst = p5_visit.case.institution
    user = User.objects.filter(institution=inst, user_type="institution", is_active=True).first()
    if user is None:
        pytest.skip("demo institution has no portal account")
    user.must_change_password = False
    user.save(update_fields=["must_change_password"])
    body = bearer(portal_api, user).get(BASE + "inspections/").json()
    assert body["count"] >= 1 and body["results"][0]["reference"]
