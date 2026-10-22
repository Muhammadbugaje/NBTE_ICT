"""tickets/ (staff door)."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from .conftest import BASE, bearer, err
from .work_fixtures import PDF, api_as, boss, outsider, work_seed, worker  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def category(work_seed):
    from staff.models import Department
    from tickets.models import TicketCategory

    return TicketCategory.objects.create(code="hw-api", name="Hardware", minimum_priority="normal",
                                         handling_department=Department.objects.get(code="ict"))


def raise_ticket(client, category, **extra):
    data = {"category": category.code, "subject": "Printer jammed",
            "body": "The printer on floor 2 keeps jamming.", **extra}
    return client.post(BASE + "tickets/", data, format="multipart" if "photo" in extra else "json")


def test_auth_and_door(staff_api, rp_user, category):
    assert staff_api.get(BASE + "tickets/").status_code == 401
    r = bearer(staff_api, rp_user).get(BASE + "tickets/")
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"


def test_categories_and_create_with_photo(outsider, category, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    c = api_as(outsider)
    cats = c.get(BASE + "tickets/categories/").json()["results"]
    assert cats[0]["code"] == "hw-api" and cats[0]["priorities"] == ["normal", "high", "urgent"]
    photo = SimpleUploadedFile("jam.pdf", PDF, content_type="application/pdf")
    r = raise_ticket(c, category, priority="low", photo=photo)
    assert r.status_code == 400 and "priority" in err(r)["fields"]  # below the category floor
    photo = SimpleUploadedFile("jam.pdf", PDF, content_type="application/pdf")
    r = raise_ticket(c, category, photo=photo, current_location="Floor 2")
    assert r.status_code == 201, r.content
    t = r.json()
    assert t["state"] in ("new", "assigned") and t["priority"] == "normal" and t["current_location"] == "Floor 2"
    assert t["attachments"][0]["name"] == "jam.pdf" and t["actions"]["is_raiser"]
    r = c.get(BASE + f"tickets/{t['id']}/attachments/{t['attachments'][0]['id']}/")
    assert r.status_code == 200
    mine = c.get(BASE + "tickets/?scope=my").json()
    assert mine["count"] == 1 and set(mine) == {"results", "next", "previous", "count"}


def test_create_validation(outsider, category):
    r = raise_ticket(api_as(outsider), category, subject="ab", body="short")
    assert r.status_code == 400 and {"subject", "body"} <= set(err(r)["fields"])
    r = api_as(outsider).post(BASE + "tickets/", {"category": "nope", "subject": "Hello there",
                                                  "body": "Long enough body text"}, format="json")
    assert r.status_code == 400 and "category" in err(r)["fields"]


def test_visibility_and_lifecycle(boss, worker, outsider, work_seed, make_user, category):
    stranger = make_user("stranger@t.local", role="officer", dept="hr")
    t = raise_ticket(api_as(outsider), category).json()
    assert api_as(stranger).get(BASE + f"tickets/{t['id']}/").status_code == 404
    # The handling department's director sees it (department lead) and assigns.
    b = api_as(boss)
    assert b.get(BASE + "tickets/?scope=all").json()["count"] == 1
    people = b.get(BASE + f"tickets/{t['id']}/assignees/").json()["results"]
    assert worker.pk in [p["id"] for p in people] and outsider.pk not in [p["id"] for p in people]
    r = api_as(stranger).get(BASE + f"tickets/{t['id']}/assignees/")
    assert r.status_code == 404
    r = b.post(BASE + f"tickets/{t['id']}/assign/", {"assignee": outsider.pk}, format="json")
    assert r.status_code == 400 and "assignee" in err(r)["fields"]  # not in the handling department
    r = b.post(BASE + f"tickets/{t['id']}/assign/", {"assignee": worker.pk}, format="json")
    assert r.status_code == 200 and r.json()["assigned_to"]["id"] == worker.pk
    w = api_as(worker)
    assert w.get(BASE + "tickets/?scope=assigned").json()["count"] == 1
    r = api_as(outsider).post(BASE + f"tickets/{t['id']}/start/", {}, format="json")
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"
    assert w.post(BASE + f"tickets/{t['id']}/start/", {}, format="json").json()["state"] == "in_progress"
    # internal note hidden from the raiser
    r = w.post(BASE + f"tickets/{t['id']}/comments/", {"body": "Toner is fake", "is_internal": True},
               format="json")
    assert r.status_code == 201 and r.json()["comments"][-1]["is_internal"]
    raiser_view = api_as(outsider).get(BASE + f"tickets/{t['id']}/").json()
    assert all(not c["is_internal"] for c in raiser_view["comments"])
    assert all(a["kind"] != "internal_note" for a in raiser_view["activity"])
    r = w.post(BASE + f"tickets/{t['id']}/resolve/", {"resolution_notes": "ok"}, format="json")
    assert r.status_code == 400 and "resolution_notes" in err(r)["fields"]
    r = w.post(BASE + f"tickets/{t['id']}/resolve/", {"resolution_notes": "Replaced the roller."},
               format="json")
    assert r.json()["state"] == "resolved"
    o = api_as(outsider)
    assert o.get(BASE + f"tickets/{t['id']}/").json()["actions"]["can_close"]
    r = o.post(BASE + f"tickets/{t['id']}/close/", {"rating": 9}, format="json")
    assert r.status_code == 400 and "rating" in err(r)["fields"]
    r = o.post(BASE + f"tickets/{t['id']}/close/", {"rating": 5, "comment": "Thanks"}, format="json")
    assert r.json()["state"] == "closed" and r.json()["satisfaction_rating"] == 5
    r = o.post(BASE + f"tickets/{t['id']}/reopen/", {"reason": "Jamming again today"}, format="json")
    assert r.json()["state"] == "reopened"


def test_cancel_by_raiser_only(boss, outsider, category):
    t = raise_ticket(api_as(outsider), category).json()
    r = api_as(boss).post(BASE + f"tickets/{t['id']}/cancel/", {}, format="json")
    assert r.status_code == 403
    r = api_as(outsider).post(BASE + f"tickets/{t['id']}/cancel/", {"reason": "Fixed itself"}, format="json")
    assert r.status_code == 200 and r.json()["state"] == "cancelled"
