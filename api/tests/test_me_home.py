"""me/ and home/ shapes for the three user types."""
import pytest

from .conftest import BASE, bearer, err

pytestmark = pytest.mark.django_db

ME_KEYS = {"id", "email", "full_name", "first_name", "last_name", "initials", "photo_url", "user_type", "door",
           "designation", "department", "institution", "must_change_password", "capabilities", "unread",
           "profile_completeness"}
CAPS = {"qa_workspace", "approve_leave", "post_announcements", "finance_queue", "insights"}
UNREAD = {"chats", "alerts", "announcements", "memos"}


def _me(client, user):
    r = bearer(client, user).get(BASE + "me/")
    assert r.status_code == 200, r.content
    return r.json()


def test_me_staff(staff_api, qa_seed, make_user):
    from notifications.models import Notification

    user = make_user("dir.fin@t.local", role="director", dept="dpap")
    Notification.objects.create(recipient=user, title="Hi", body="x")
    me = _me(staff_api, user)
    assert set(me) == ME_KEYS
    assert set(me["capabilities"]) == CAPS and set(me["unread"]) == UNREAD
    assert me["door"] == "staff" and me["institution"] is None
    assert me["department"]["id"] == user.department_id and me["department"]["name"]
    assert me["initials"] == "DT"  # Dir Test
    assert me["unread"]["alerts"] == 1
    assert isinstance(me["profile_completeness"], int)
    assert me["capabilities"]["post_announcements"] is False  # no post_announcement permission


def test_me_staff_capabilities_follow_permissions(staff_api, qa_seed, make_user):
    officer = make_user("qa.off@t.local", role="officer", dept="dpap", groups=["QA Settings Admin"])
    assert _me(staff_api, officer)["capabilities"]["qa_workspace"] is True
    plain = make_user("plain@t.local")
    caps = _me(staff_api, plain)["capabilities"]
    assert caps == {k: False for k in CAPS}
    boss = make_user("root@t.local", superuser=True)
    assert _me(staff_api, boss)["capabilities"]["insights"] is True


def test_me_institution(portal_api, institution_user):
    me = _me(portal_api, institution_user)
    assert set(me) == ME_KEYS
    assert me["institution"] == {"id": institution_user.institution_id, "code": "APX",
                                 "name": "APX Polytechnic", "status": "approved"}
    assert me["department"] is None and me["door"] == "portal"
    assert me["profile_completeness"] is None
    assert not any(me["capabilities"].values())


def test_me_resource_person(portal_api, rp_user):
    me = _me(portal_api, rp_user)
    assert set(me) == ME_KEYS
    assert me["user_type"] == "resource_person" and me["institution"] is None and me["department"] is None


def test_unread_chats_one_query_matches_the_inbox(staff_user, make_user):
    from messaging.models import Thread, start_thread

    from api.services.me import unread_chat_count

    other = make_user("colleague@t.local")
    thread = start_thread(creator=other, participants=[staff_user], subject="Hello", initial_body="first")
    from messaging.models import Message

    Message.objects.create(thread=thread, sender=other, body="second")
    expected = sum(t.unread_count_for(staff_user) for t in Thread.objects.filter(participants__user=staff_user))
    assert expected == 2
    assert unread_chat_count(staff_user) == expected
    assert unread_chat_count(other) == 0


def test_patch_me_staff(staff_api, staff_user):
    bearer(staff_api, staff_user)
    r = staff_api.patch(BASE + "me/", {"phone": "08030000000", "first_name": "Fatima"}, format="json")
    assert r.status_code == 200, r.content
    assert r.json()["first_name"] == "Fatima"
    staff_user.refresh_from_db()
    assert staff_user.staff_profile.phone == "08030000000"
    assert staff_user.email == "fatima.staff@t.local"  # untouched


def test_patch_me_portal_only_names(portal_api, institution_user):
    bearer(portal_api, institution_user)
    r = portal_api.patch(BASE + "me/", {"phone": "0803"}, format="json")
    assert r.status_code == 400 and "phone" in err(r)["fields"]
    r = portal_api.patch(BASE + "me/", {"last_name": "Bello"}, format="json")
    assert r.status_code == 200 and r.json()["last_name"] == "Bello"


def test_account_endpoints_are_live(staff_api, staff_user):
    """The former 501 stubs are implemented (details: test_me_account.py)."""
    bearer(staff_api, staff_user)
    for path in ("me/preferences/", "me/devices/", "me/deletion-request/", "me/staff-id/"):
        assert staff_api.get(BASE + path).status_code == 200, path


def test_staff_id_is_staff_door_only(portal_api, institution_user):
    r = bearer(portal_api, institution_user).get(BASE + "me/staff-id/")
    assert r.status_code == 404 and err(r)["code"] == "not_found"


# ---------------------------------------------------------------------------
# home/
# ---------------------------------------------------------------------------
STAFF_HOME = {"greeting", "date_label", "attendance", "counts", "pinned_chats", "up_next", "announcement"}


def test_home_staff_empty(staff_api, staff_user):
    r = bearer(staff_api, staff_user).get(BASE + "home/")
    assert r.status_code == 200, r.content
    home = r.json()
    assert set(home) == STAFF_HOME
    assert set(home["attendance"]) == {"checked_in", "checked_in_at", "location_name", "can_check_in"}
    assert home["counts"] == {"tasks_open": 0, "tasks_due_today": 0, "approvals_waiting": 0, "unread_chats": 0}
    assert home["pinned_chats"] == [] and home["up_next"] == [] and home["announcement"] is None
    assert home["greeting"].startswith("Good ")


def test_home_staff_tasks_and_attendance(staff_api, qa_seed, make_user):
    from django.utils import timezone

    from attendance.models import AttendanceRecord
    from tasks.models import Task, TaskAssignee

    user = make_user("worker@t.local", role="officer", dept="dpap")
    today = timezone.localdate()
    task = Task.objects.create(reference="T-API-1", title="File the report", due_date=today, created_by=user,
                               state=Task.OPEN)
    TaskAssignee.objects.create(task=task, user=user)
    AttendanceRecord.objects.create(user=user, date=today, status=AttendanceRecord.PRESENT,
                                    check_in_at=timezone.now())
    home = bearer(staff_api, user).get(BASE + "home/").json()
    assert home["counts"]["tasks_open"] == 1 and home["counts"]["tasks_due_today"] == 1
    assert home["up_next"][0] == {"kind": "task", "id": task.pk, "title": "File the report",
                                  "subtitle": task.reference, "due_label": "Today", "route": f"/tasks/{task.pk}"}
    assert home["attendance"]["checked_in"] is True and home["attendance"]["can_check_in"] is False


def test_home_institution_empty(portal_api, institution_user):
    home = bearer(portal_api, institution_user).get(BASE + "home/").json()
    assert set(home) == {"institution", "application", "bills_due", "programmes_expiring", "payments_by_month",
                         "action_items", "recent_activity", "programme_board", "programme_count", "sections"}
    assert home["institution"]["code"] == "APX"
    assert home["application"] is None and home["bills_due"] == [] and home["programmes_expiring"] == []
    assert len(home["payments_by_month"]) == 12
    assert all(set(m) == {"month", "count"} and m["count"] == 0 for m in home["payments_by_month"])


def test_home_institution_with_application_and_bill(portal_api, inst_user, fee_obligation):
    home = bearer(portal_api, inst_user).get(BASE + "home/").json()
    app = home["application"]
    assert app["stages"] == 7 and 1 <= app["stage"] <= 7 and app["headline"] and app["route"] == "/application"
    bill = home["bills_due"][0]
    assert set(bill) == {"id", "title", "amount", "amount_display", "due_date", "status"}
    assert bill["id"] == fee_obligation.pk
    assert bill["amount_display"].startswith("₦") and "." in bill["amount"]


def test_home_resource_person_empty(portal_api, rp_user):
    home = bearer(portal_api, rp_user).get(BASE + "home/").json()
    sections = home.pop("sections")
    assert home == {"next_visit": None, "todo": [], "history": []}
    assert [s["key"] for s in sections] == ["engagements", "service", "profile", "complaints"]


def test_home_resource_person_with_visit(portal_api, p5_visit):
    rp = p5_visit.mappings[0].resource_person
    assert rp.user_id, "demo resource person has a portal account"
    rp.user.must_change_password = False  # demo accounts start with a temporary password
    rp.user.save(update_fields=["must_change_password"])
    r = bearer(portal_api, rp.user).get(BASE + "home/")
    assert r.status_code == 200, r.content
    home = r.json()
    visit = home["next_visit"]
    assert visit is not None
    assert set(visit) == {"id", "institution", "date", "role", "programmes", "route"}
    assert visit["programmes"] >= 1 and visit["route"] == f"/visits/{visit['id']}"
    assert isinstance(home["todo"], list) and isinstance(home["history"], list)
