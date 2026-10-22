"""tasks/ (staff door): lists, detail, create, edit, transitions, comments,
attachments, timers, people picker - and the security checklist."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from .conftest import BASE, bearer, err
from .work_fixtures import PDF, api_as, boss, outsider, work_seed, worker  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def category(work_seed):
    from staff.models import Department
    from tasks.models import TaskCategory

    return TaskCategory.objects.create(code="gen-api", name="General API", default_priority="high",
                                       default_due_days=5, handling_department=Department.objects.get(code="ict"))


def make_task(creator, assignees=(), title="Fix the router", **kw):
    from tasks import services

    task = services.create_task(created_by=creator, title=title, assignees=list(assignees), **kw)
    if assignees:
        services.open_task(task, creator)
    task.refresh_from_db()
    return task


def test_requires_auth_and_staff_door(staff_api, portal_api, worker, rp_user):
    assert staff_api.get(BASE + "tasks/").status_code == 401
    r = bearer(portal_api, rp_user).get(BASE + "tasks/")
    assert r.status_code == 404  # staff-only route does not exist on the portal host
    r = bearer(staff_api, rp_user).get(BASE + "tasks/")
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"


def test_password_change_required(staff_api, work_seed, make_user):
    u = make_user("temp.pw@t.local", role="officer", dept="ict", must_change=True)
    r = api_as(u).get(BASE + "tasks/")
    assert r.status_code == 403 and err(r)["code"] == "password_change_required"


def test_list_scopes_and_pagination(staff_api, boss, worker, outsider):
    t1 = make_task(boss, [worker], title="Assigned one")
    make_task(boss, [], title="Boss draft")
    make_task(outsider, [], title="Not mine")
    c = api_as(worker)
    r = c.get(BASE + "tasks/?scope=assigned")
    body = r.json()
    assert r.status_code == 200 and set(body) == {"results", "next", "previous", "count"}
    assert [x["id"] for x in body["results"]] == [t1.pk]
    item = body["results"][0]
    assert item["my_state"] == "assigned" and item["created_by"]["id"] == boss.pk
    assert item["state"] == "open" and item["category"] is None
    created = api_as(boss).get(BASE + "tasks/?scope=created").json()
    assert {x["title"] for x in created["results"]} == {"Assigned one", "Boss draft"}
    # all = visible to me; worker cannot see the outsider's task
    titles = {x["title"] for x in c.get(BASE + "tasks/?scope=all&state=all").json()["results"]}
    assert "Not mine" not in titles and "Assigned one" in titles
    assert c.get(BASE + "tasks/?q=router").json()["count"] == 0
    assert c.get(BASE + "tasks/?q=Assigned").json()["count"] == 1
    r = c.get(BASE + "tasks/?scope=nope")
    assert r.status_code == 400 and "scope" in err(r)["fields"]


def test_detail_and_other_users_task_is_404(staff_api, boss, worker, outsider):
    t = make_task(boss, [worker])
    r = api_as(worker).get(BASE + f"tasks/{t.pk}/")
    assert r.status_code == 200
    d = r.json()
    assert d["reference"] == t.reference and d["assignees"][0]["user"]["id"] == worker.pk
    assert d["actions"]["can_accept"] and d["actions"]["can_start"] and not d["actions"]["can_edit"]
    assert d["activity"] and d["running_timer"] is None
    r = api_as(outsider).get(BASE + f"tasks/{t.pk}/")
    assert r.status_code == 404 and err(r)["code"] == "not_found"


def test_create_with_category_defaults_and_open_now(staff_api, boss, worker, category):
    c = api_as(boss)
    r = c.post(BASE + "tasks/", {"title": "Replace UPS", "category": category.pk, "assignees": [worker.pk],
                                 "open_now": True, "tags": ["power", "urgent"]}, format="json")
    assert r.status_code == 201, r.content
    body = r.json()
    task = body["task"]
    assert body["warnings"] == []
    assert task["state"] == "open" and task["priority"] == "high" and task["due_date"]
    assert task["tags"] == ["power", "urgent"] and task["owner_department"]["name"]
    assert task["recurrence_type"] == "none"  # the website form's initial value
    assert task["assignees"][0]["user"]["id"] == worker.pk


def test_create_validation_uses_website_form(staff_api, boss):
    c = api_as(boss)
    r = c.post(BASE + "tasks/", {"title": "ab", "start_date": "2026-10-10", "due_date": "2026-10-01"},
               format="json")
    assert r.status_code == 400
    fields = err(r)["fields"]
    assert "title" in fields and "due_date" in fields


def test_create_category_rank_is_enforced(staff_api, worker):
    from tasks.models import TaskCategory

    cat = TaskCategory.objects.create(code="high-only", name="High only", min_rank_to_assign=100)
    r = api_as(worker).post(BASE + "tasks/", {"title": "Try it", "category": cat.pk}, format="json")
    assert r.status_code == 400 and "category" in err(r)["fields"]
    cats = api_as(worker).get(BASE + "tasks/categories/").json()["results"]
    assert cat.pk not in [x["id"] for x in cats]


def test_create_links_chat_message(staff_api, boss, monkeypatch):
    calls = []
    monkeypatch.setattr("messaging.chat_services.link_message_to_task",
                        lambda mid, task, user: calls.append((mid, task.pk, user.pk)))
    r = api_as(boss).post(BASE + "tasks/", {"title": "From chat", "from_message": 77}, format="json")
    assert r.status_code == 201
    assert calls == [(77, r.json()["task"]["id"], boss.pk)]


def test_edit_permissions(staff_api, boss, worker):
    t = make_task(boss, [worker])
    r = api_as(worker).patch(BASE + f"tasks/{t.pk}/", {"title": "Hacked"}, format="json")
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"
    r = api_as(boss).patch(BASE + f"tasks/{t.pk}/", {"title": "Renamed task", "priority": "low"},
                                      format="json")
    assert r.status_code == 200 and r.json()["title"] == "Renamed task" and r.json()["priority"] == "low"
    assert any(a["kind"] == "edited" for a in r.json()["activity"])


def test_full_lifecycle_with_review(staff_api, boss, worker):
    t = make_task(boss, [worker])
    w = api_as(worker)
    assert w.post(BASE + f"tasks/{t.pk}/accept/", {}, format="json").json()["my_state"] == "accepted"
    r = w.post(BASE + f"tasks/{t.pk}/start/", {}, format="json")
    assert r.status_code == 200 and r.json()["state"] == "in_progress"
    r = w.post(BASE + f"tasks/{t.pk}/timer/start/")
    assert r.json()["running_timer"] is not None and r.json()["actions"]["can_stop_timer"]
    r = w.post(BASE + f"tasks/{t.pk}/timer/stop/")
    assert r.json()["running_timer"] is None and len(r.json()["time_entries"]) == 1
    r = w.post(BASE + f"tasks/{t.pk}/mark-done/", {"note": "Done it"}, format="json")
    assert r.json()["state"] == "awaiting_review"
    # the assignee cannot review
    r = w.post(BASE + f"tasks/{t.pk}/review/", {"approve": True}, format="json")
    assert r.status_code == 403
    b = api_as(boss)
    review_list = b.get(BASE + "tasks/?scope=review").json()["results"]
    assert [x["id"] for x in review_list] == [t.pk]
    r = b.post(BASE + f"tasks/{t.pk}/review/", {"approve": False}, format="json")
    assert r.status_code == 400 and "notes" in err(r)["fields"]
    r = b.post(BASE + f"tasks/{t.pk}/review/", {"approve": True, "notes": "Good"}, format="json")
    assert r.status_code == 200 and r.json()["state"] == "completed"
    r = b.post(BASE + f"tasks/{t.pk}/reopen/", {"reason": "no"}, format="json")
    assert r.status_code == 400 and "reason" in err(r)["fields"]
    r = b.post(BASE + f"tasks/{t.pk}/reopen/", {"reason": "Still flaky"}, format="json")
    assert r.status_code == 200 and r.json()["state"] != "completed"


def test_ask_return_resend_cancel(staff_api, boss, worker):
    t = make_task(boss, [worker])
    w = api_as(worker)
    r = w.post(BASE + f"tasks/{t.pk}/ask/", {"note": ""}, format="json")
    assert r.status_code == 400 and "note" in err(r)["fields"]
    assert w.post(BASE + f"tasks/{t.pk}/ask/", {"note": "Which router?"}, format="json").json()["state"] == \
        "awaiting_response"
    assert w.post(BASE + f"tasks/{t.pk}/resume/", {}, format="json").json()["state"] == "in_progress"
    r = w.post(BASE + f"tasks/{t.pk}/return/", {"reason": "Not my area of work"}, format="json")
    assert r.json()["state"] == "returned"
    r = w.post(BASE + f"tasks/{t.pk}/resend/", {}, format="json")
    assert r.status_code == 403
    b = api_as(boss)
    assert b.post(BASE + f"tasks/{t.pk}/resend/", {}, format="json").json()["state"] == "open"
    r = w.post(BASE + f"tasks/{t.pk}/cancel/", {}, format="json")
    assert r.status_code == 403
    assert b.post(BASE + f"tasks/{t.pk}/cancel/", {"reason": "Moot"}, format="json").json()["state"] == "cancelled"


def test_delete_and_open(staff_api, boss, worker):
    t = make_task(boss, [])
    b = api_as(boss)
    r = b.post(BASE + f"tasks/{t.pk}/open/")
    assert r.status_code == 400 and "assignee" in err(r)["message"].lower()
    r = b.post(BASE + f"tasks/{t.pk}/assignees/", {"user_ids": [worker.pk, 999999]}, format="json")
    assert r.status_code == 200 and r.json()["added"] == [worker.pk] and r.json()["skipped"]
    assert r.json()["task"]["state"] == "open"  # adding an assignee opens a draft (website rule)
    r = b.post(BASE + f"tasks/{t.pk}/delete/", {"reason": "x"}, format="json")
    assert r.status_code == 400
    r = b.post(BASE + f"tasks/{t.pk}/delete/", {"reason": "Duplicate task"}, format="json")
    assert r.status_code == 204
    assert b.get(BASE + f"tasks/{t.pk}/").status_code == 404


def test_comments_and_attachments(staff_api, boss, worker, outsider, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    t = make_task(boss, [worker])
    w = api_as(worker)
    r = w.post(BASE + f"tasks/{t.pk}/comments/", {"body": "  "}, format="json")
    assert r.status_code == 400 and "body" in err(r)["fields"]
    r = w.post(BASE + f"tasks/{t.pk}/comments/", {"body": "On it"}, format="json")
    assert r.status_code == 201 and r.json()["comments"][-1]["body"] == "On it"
    upload = SimpleUploadedFile("notes.pdf", PDF, content_type="application/pdf")
    r = w.post(BASE + f"tasks/{t.pk}/attachments/", {"file": upload, "description": "Log"}, format="multipart")
    assert r.status_code == 201, r.content
    att = r.json()["attachments"][0]
    assert att["name"] == "notes.pdf" and att["download_url"].endswith(f"tasks/{t.pk}/attachments/{att['id']}/")
    r = w.get(BASE + f"tasks/{t.pk}/attachments/{att['id']}/")
    assert r.status_code == 200 and b"".join(r.streaming_content) == PDF
    r = api_as(outsider).get(BASE + f"tasks/{t.pk}/attachments/{att['id']}/")
    assert r.status_code == 404
    bad = SimpleUploadedFile("evil.exe", b"MZ", content_type="application/octet-stream")
    r = w.post(BASE + f"tasks/{t.pk}/attachments/", {"file": bad}, format="multipart")
    assert r.status_code == 400 and "file" in err(r)["fields"]
    r = api_as(outsider).post(BASE + f"tasks/{t.pk}/comments/", {"body": "hi"}, format="json")
    assert r.status_code == 404


def test_people_picker_follows_rank(staff_api, boss, worker):
    r = api_as(boss).get(BASE + "tasks/people/?q=worker")
    assert r.status_code == 200
    people = r.json()["results"]
    assert [p["id"] for p in people] == [worker.pk]
    assert {"used", "max", "remaining", "at_capacity"} <= set(people[0])
    # an officer cannot assign to a director
    assert api_as(worker).get(BASE + "tasks/people/?q=boss").json()["count"] == 0


def test_schema_still_validates():
    import io

    from django.core.management import call_command

    call_command("spectacular", "--validate", "--fail-on-warn", "--urlconf", "nbte_ict.urls",
                 stdout=io.StringIO())


