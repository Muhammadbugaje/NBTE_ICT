"""v2.1 Phase E chats API: group admin, invite links, pins, reminders and the
versioned poll (the same rules as the website; messaging.group_services)."""
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from messaging import group_services as gs
from messaging.models import MessageReminder, Thread, start_thread

from .conftest import BASE, bearer

pytestmark = pytest.mark.django_db
C = BASE + "chats/"


@pytest.fixture(autouse=True)
def _cache():
    cache.clear()


def with_staff(user, n=[0]):
    from staff.models import Staff

    n[0] += 1
    Staff.objects.get_or_create(user=user, defaults={"employee_no": f"PE{user.pk:05d}{n[0]}",
                                                      "first_name": user.first_name, "last_name": user.last_name})
    return user


def api(user):
    return bearer(APIClient(HTTP_HOST="localhost"), user)


@pytest.fixture
def g(make_user):
    owner = with_staff(make_user("owner.e@t.local"))
    member = with_staff(make_user("member.e@t.local"))
    newbie = with_staff(make_user("newbie.e@t.local"))
    thread = start_thread(creator=owner, participants=[member], subject="Team", initial_body="hi",
                          thread_type=Thread.GROUP)
    return {"owner": owner, "member": member, "newbie": newbie, "t": thread}


def test_l3_l4_api_members_and_invites(g, make_user):
    t, owner, member, newbie = g["t"], g["owner"], g["member"], g["newbie"]
    r = api(member).post(C + f"threads/{t.pk}/members/", {"users": [newbie.pk]}, format="json")
    assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"
    r = api(owner).post(C + f"threads/{t.pk}/members/", {"users": [newbie.pk]}, format="json")
    assert r.status_code == 200 and r.json()["member_count"] == 3
    r = api(owner).post(C + f"threads/{t.pk}/members/{member.pk}/role/", {"role": "admin"}, format="json")
    assert {m["id"]: m["role"] for m in r.json()["members"]}[member.pk] == "admin"
    r = api(member).post(C + f"threads/{t.pk}/members/{newbie.pk}/remove/")
    assert r.status_code == 200 and r.json()["member_count"] == 2

    assert api(newbie).get(C + f"threads/{t.pk}/group/").status_code == 404   # removed: invisible
    r = api(member).post(C + f"threads/{t.pk}/invites/", {"days": 1, "max_uses": 5}, format="json")
    assert r.status_code == 201
    token = r.json()["token"]
    assert api(newbie).get(C + f"join/{token}/").json()["state"] == "ok"
    r = api(newbie).post(C + f"join/{token}/")
    assert r.status_code == 200 and r.json()["my_role"] == "member"
    assert api(newbie).get(C + f"join/{token}/").json()["state"] == "member"
    invite_id = r.json() and api(owner).get(C + f"threads/{t.pk}/invites/").json()[0]["id"]
    assert api(newbie).get(C + f"threads/{t.pk}/invites/").status_code == 403
    r = api(owner).post(C + f"threads/{t.pk}/invites/{invite_id}/revoke/")
    assert r.json()["revoked"] is True and r.json()["usable"] is False
    late = with_staff(make_user("late.e@t.local"))
    r = api(late).post(C + f"join/{token}/")
    assert r.status_code == 400
    assert api(newbie).post(C + f"threads/{t.pk}/leave/").status_code == 204


def test_l6_l7_api_pins_and_reminders(g):
    t, owner, member = g["t"], g["owner"], g["member"]
    msg = t.messages.first()
    assert api(member).post(C + f"messages/{msg.pk}/pin/", {"pinned": True}, format="json").status_code == 403
    r = api(owner).post(C + f"messages/{msg.pk}/pin/", {"pinned": True}, format="json")
    assert r.status_code == 200 and r.json()["pinned_at"]
    assert api(member).get(C + f"threads/{t.pk}/group/").json()["pinned"][0]["id"] == msg.pk

    r = api(member).post(C + f"messages/{msg.pk}/reminder/", {"preset": "3h", "note": "follow up"}, format="json")
    assert r.status_code == 200 and r.json()["note"] == "follow up"
    assert len(api(member).get(C + "reminders/").json()) == 1
    assert api(member).get(C + f"messages/{msg.pk}/").json()["my_reminder_at"]
    past = (timezone.now() - timedelta(hours=1)).isoformat()
    assert api(member).post(C + f"messages/{msg.pk}/reminder/", {"remind_at": past}, format="json").status_code == 400
    assert api(member).delete(C + f"messages/{msg.pk}/reminder/").status_code == 204
    assert not MessageReminder.objects.filter(user=member).exists()


def test_l9_api_updates_unchanged(g):
    t, member = g["t"], g["member"]
    c = api(member)
    first = c.get(C + f"threads/{t.pk}/updates/", {"after": 0}).json()
    second = c.get(C + f"threads/{t.pk}/updates/", {"after": first["last_id"], "since": first["server_time"],
                                                     "v": first["v"]}).json()
    third = c.get(C + f"threads/{t.pk}/updates/", {"after": second["last_id"], "since": second["server_time"],
                                                    "v": second["v"]}).json()
    assert third["unchanged"] is True and third["new"] == [] and third["last_id"] == second["last_id"]
    gs.post_system_message(t, "something happened")
    fourth = c.get(C + f"threads/{t.pk}/updates/", {"after": third["last_id"], "since": third["server_time"],
                                                     "v": third["v"]}).json()
    assert fourth["unchanged"] is False and fourth["new"][0]["body"] == "something happened"
