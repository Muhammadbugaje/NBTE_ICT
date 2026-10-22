"""Portal door — institution profile and users, documents, application,
timeline, protected files, home additions, and the security matrix for
every portal endpoint (401 / wrong door / password change / wrong user type)."""
import pytest
from rest_framework.test import APIClient

from qa_core.tests.phase2_fixtures import pdf_upload

from .conftest import BASE, bearer, err

pytestmark = pytest.mark.django_db

INSTITUTION_GETS = ["institution/", "institution/options/", "institution/users/", "timeline/", "inspections/",
                    "documents/", "documents/summary/", "documents/history/", "application/", "qa-requests/",
                    "qa-requests/new/", "queries/", "programmes/", "programmes/board/", "letters/", "payments/",
                    "payments/summary/"]
RP_GETS = ["engagements/", "service-history/", "service-history/summary/", "profile/"]
SHARED_GETS = ["complaints/", "complaints/new/", "complaints/suggest/?subject=payment", "help/"]
ALL_GETS = INSTITUTION_GETS + RP_GETS + SHARED_GETS


def _api(portal_api, user):
    return bearer(portal_api, user)


# ---------------------------------------------------------------------------
# Security matrix
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ALL_GETS)
def test_unauthenticated_401(portal_api, path):
    r = portal_api.get(BASE + path)
    assert r.status_code == 401 and err(r)["code"] == "not_authenticated"


@pytest.mark.parametrize("path", ALL_GETS)
def test_staff_token_wrong_door(portal_api, staff_user, path):
    r = _api(portal_api, staff_user).get(BASE + path)
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"


def test_portal_routes_do_not_exist_on_staff_door(staff_api, staff_user):
    r = bearer(staff_api, staff_user).get(BASE + "institution/")
    assert r.status_code == 404 and err(r)["code"] == "not_found"


@pytest.mark.parametrize("path", ["institution/", "payments/", "engagements/", "complaints/"])
def test_password_change_required(portal_api, make_user, institution, path):
    user = make_user("temp@apx.local", user_type="institution", institution=institution, must_change=True)
    r = _api(portal_api, user).get(BASE + path)
    assert r.status_code == 403 and err(r)["code"] == "password_change_required"


@pytest.mark.parametrize("path", INSTITUTION_GETS)
def test_resource_person_gets_404_on_institution_pages(portal_api, rp_user, path):
    r = _api(portal_api, rp_user).get(BASE + path)
    assert r.status_code == 404 and err(r)["code"] == "not_found"


@pytest.mark.parametrize("path", RP_GETS)
def test_institution_gets_404_on_resource_person_pages(portal_api, institution_user, path):
    r = _api(portal_api, institution_user).get(BASE + path)
    assert r.status_code == 404


@pytest.mark.parametrize("path", INSTITUTION_GETS + SHARED_GETS)
def test_institution_reads_every_page(portal_api, institution_user, path):
    r = _api(portal_api, institution_user).get(BASE + path)
    assert r.status_code == 200, (path, r.content[:300])


def test_portal_switched_off_is_account_inactive(portal_api, institution_user, institution):
    institution.portal_enabled = False
    institution.save(update_fields=["portal_enabled"])
    r = _api(portal_api, institution_user).get(BASE + "institution/")
    assert r.status_code == 403 and err(r)["code"] == "account_inactive"


def test_paginated_shape(portal_api, institution_user):
    body = _api(portal_api, institution_user).get(BASE + "timeline/?page_size=5").json()
    assert set(body) == {"results", "next", "previous", "count"}


# ---------------------------------------------------------------------------
# Institution profile
# ---------------------------------------------------------------------------
def test_approved_profile_is_read_only(portal_api, p3):
    api = _api(portal_api, p3.admin)
    body = api.get(BASE + "institution/").json()
    assert body["code"] == "AAA" and body["editable"] is False and body["edit"] is None
    assert body["handling_department"]["name"]
    r = api.patch(BASE + "institution/", {"phone": "0800"}, format="json")
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"


def test_proposed_profile_edit(portal_api, proposed_inst, inst_user):
    api = _api(portal_api, inst_user)
    body = api.get(BASE + "institution/").json()
    assert body["editable"] is True and body["edit"]["official_email"] == "info@zit.edu.ng"
    assert body["can_manage_users"] is True
    r = api.patch(BASE + "institution/", {"phone": "08099990000", "website": "https://zit.edu.ng"}, format="json")
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["website"] == "https://zit.edu.ng"
    assert any(c["kind"] == "phone" and c["value"] == "08099990000" for c in body["contacts"])
    r = api.patch(BASE + "institution/", {"official_email": "not-an-email"}, format="json")
    assert r.status_code == 400 and "official_email" in err(r)["fields"]


def test_profile_options(portal_api, proposed_inst, inst_user, lookups):
    body = _api(portal_api, inst_user).get(BASE + f"institution/options/?state={lookups.kaduna.pk}").json()
    assert body["ownerships"] and body["states"] and body["lgas"]
    r = _api(portal_api, inst_user).get(BASE + "institution/options/?state=abc")
    assert r.status_code == 400 and "state" in err(r)["fields"]


def test_users_list_and_invite(portal_api, proposed_inst, inst_user, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    api = _api(portal_api, inst_user)
    body = api.get(BASE + "institution/users/").json()
    assert body["can_manage"] is True and body["results"][0]["is_primary_contact"] is True
    r = api.post(BASE + "institution/users/", {"first_name": "Ada"}, format="json")
    assert r.status_code == 400 and "email" in err(r)["fields"]
    r = api.post(BASE + "institution/users/", {"first_name": "Ada", "last_name": "Obi", "email": "ada@zit.edu.ng",
                                               "role": "inst-academic-dept"}, format="json")
    assert r.status_code == 201, r.json()
    assert any(m["full_name"] == "Ada Obi" and m["invited"] for m in r.json()["results"])


def test_invite_needs_manager(portal_api, p3):
    r = _api(portal_api, p3.dept).post(BASE + "institution/users/", {"email": "x@aaa.local"}, format="json")
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
def test_documents_upload_download_and_isolation(portal_api, portal_host, proposed_inst, inst_user, p3):
    api = _api(portal_api, inst_user)
    body = api.get(BASE + "documents/").json()
    assert body["count"] >= 1
    row = body["results"][0]
    assert row["state"] == "missing" and row["can_upload"] is True and row["document"] is None
    kind_id = row["kind"]["id"]
    r = api.post(BASE + f"documents/upload/{kind_id}/", {}, format="multipart")
    assert r.status_code == 400 and "file" in err(r)["fields"]
    from django.core.files.uploadedfile import SimpleUploadedFile

    bad = SimpleUploadedFile("evil.exe", b"MZ....", content_type="application/octet-stream")
    r = api.post(BASE + f"documents/upload/{kind_id}/", {"file": bad}, format="multipart")
    assert r.status_code == 400 and err(r)["code"] == "validation_error"
    r = api.post(BASE + f"documents/upload/{kind_id}/", {"file": pdf_upload("cac.pdf")}, format="multipart")
    assert r.status_code == 201, r.json()
    doc = r.json()["document"]
    assert r.json()["state"] == "submitted" and doc["version"] == 1
    url = doc["file"]["download_url"]
    assert url == f"/api/v1/documents/{doc['id']}/download/"
    r = api.get(url)
    assert r.status_code == 200 and b"".join(r.streaming_content).startswith(b"%PDF")
    assert "attachment" in r["Content-Disposition"]
    # the same file through the generic files endpoint
    assert api.get(BASE + f"files/institutions.institutiondocument/{doc['id']}/").status_code == 200
    # another institution: 404, never the file
    other = _api(APIClient(HTTP_HOST=portal_host), p3.admin)
    assert other.get(url).status_code == 404
    assert other.get(BASE + f"files/institutions.institutiondocument/{doc['id']}/").status_code == 404
    assert api.get(BASE + "files/accounts.user/1/").status_code == 404
    assert api.post(BASE + "documents/upload/999999/", {"file": pdf_upload()}, format="multipart").status_code == 404
    summary = api.get(BASE + "documents/summary/").json()
    assert summary["ready"] == 1 and summary["mandatory"] >= 1
    hist = api.get(BASE + "documents/history/").json()
    assert hist["results"][0]["version"] == 1


def test_documents_filter(portal_api, proposed_inst, inst_user):
    body = _api(portal_api, inst_user).get(BASE + "documents/?state=accepted").json()
    assert body["count"] == 0


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
def test_application_start_and_submit(portal_api, proposed_inst, inst_user):
    from qa_core.tests.phase2_fixtures import upload_all_documents

    api = _api(portal_api, inst_user)
    body = api.get(BASE + "application/").json()
    assert body["application"] is None and body["can_start"] is True
    body = api.post(BASE + "application/start/").json()
    app = body["application"]
    assert app["status"] == "draft" and body["stages"][0]["state"] == "current"
    assert body["blockers"] and body["can_submit"] is False
    r = api.post(BASE + "application/submit/", {"declaration": True}, format="json")
    assert r.status_code == 400 and err(r)["code"] == "validation_error"
    from accreditation.models import Application

    upload_all_documents(proposed_inst, inst_user, Application.unscoped.get(pk=app["id"]))
    body = api.get(BASE + "application/").json()
    assert body["blockers"] == [] and body["can_submit"] is True and body["documents"]["complete"] is True
    r = api.post(BASE + "application/submit/", {}, format="json")
    assert r.status_code == 400  # declaration missing
    r = api.post(BASE + "application/submit/", {"declaration": True}, format="json")
    assert r.status_code == 200, r.json()
    assert r.json()["application"]["status"] != "draft" and r.json()["clearances"]
    # programme selection is not open yet
    r = api.post(BASE + "application/programmes/", {"name": "Computer Science"}, format="json")
    assert r.status_code == 403


def test_application_programme_selection(portal_api, fee_obligation, inst_user, submitted_app):
    from accreditation.models import Application

    Application.unscoped.filter(pk=submitted_app.pk).update(status="programme_selection")
    api = _api(portal_api, inst_user)
    body = api.get(BASE + "application/").json()
    assert body["selecting_programmes"] is True and body["programme_options"]["levels"]
    level = body["programme_options"]["levels"][0]["id"]
    r = api.post(BASE + "application/programmes/", {"name": "", "level": level, "streams": 1}, format="json")
    assert r.status_code == 400 and "name" in err(r)["fields"]
    r = api.post(BASE + "application/programmes/", {"name": "Computer Science", "level": level, "streams": 2},
                 format="json")
    assert r.status_code == 201, r.json()
    prog = next(p for p in r.json()["programmes"] if p["name"] == "Computer Science")
    assert prog["requested_streams"] == 2 and r.json()["estimate"] is not None
    r = api.post(BASE + "application/programmes/", {"name": "computer science", "level": level, "streams": 1},
                 format="json")
    assert r.status_code == 400 and "name" in err(r)["fields"]
    r = api.patch(BASE + f"application/programmes/{prog['id']}/", {"streams": "0"}, format="json")
    assert r.status_code == 400 and "streams" in err(r)["fields"]
    r = api.patch(BASE + f"application/programmes/{prog['id']}/", {"streams": "3"}, format="json")
    assert r.status_code == 200
    r = api.delete(BASE + f"application/programmes/{prog['id']}/")
    assert r.status_code == 200 and not any(p["id"] == prog["id"] for p in r.json()["programmes"])


def test_other_institution_programme_404(portal_api, p3, proposed_inst, inst_user):
    r = _api(portal_api, inst_user).delete(BASE + f"application/programmes/{p3.cs.pk}/")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Timeline and home
# ---------------------------------------------------------------------------
def test_timeline(portal_api, proposed_inst, inst_user):
    api = _api(portal_api, inst_user)
    api.post(BASE + "application/start/")
    body = api.get(BASE + "timeline/?kind=application").json()
    assert body["count"] >= 1 and "Onboarding application" in body["results"][0]["text"]
    assert api.get(BASE + "timeline/?kind=payment").json()["count"] == 0


def test_home_institution_has_dashboard_parts(portal_api, bill, inst_user):
    body = _api(portal_api, inst_user).get(BASE + "home/").json()
    assert {"action_items", "recent_activity", "programme_board", "programme_count", "sections"} <= set(body)
    assert any(a["route"] == f"/payments/{bill.pk}" for a in body["action_items"])
    keys = [s["key"] for s in body["sections"]]
    assert "application" in keys and "payments" in keys and "qa_requests" not in keys


def test_home_resource_person_sections(portal_api, rp_user):
    body = _api(portal_api, rp_user).get(BASE + "home/").json()
    assert [s["key"] for s in body["sections"]][:3] == ["engagements", "service", "profile"]
