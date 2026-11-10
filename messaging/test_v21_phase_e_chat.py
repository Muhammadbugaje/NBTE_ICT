"""v2.1 Phase E chat: sticky back (L1), group roles/settings/members (L3),
invite links (L4), in-chat pins (L6), reminders (L7), cheap polls (L9) and
subject-linked groups (X41)."""
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from messaging import chat_services as cs
from messaging import group_services as gs
from messaging import subject_groups
from messaging.models import Message, MessageReminder, Thread, ThreadInvite, ThreadParticipant, start_thread
from notifications.models import Notification

pytestmark = pytest.mark.django_db
PW = "Pass-word-123"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def staff(email, n=[0]):
    from staff.models import Staff

    n[0] += 1
    u = User.objects.create_user(email, PW, must_change_password=False,
                                 first_name=email.split("@")[0].split(".")[0].title(), last_name="Test")
    Staff.objects.get_or_create(user=u, defaults={"employee_no": f"E{u.pk:05d}{n[0]}",
                                                   "first_name": u.first_name, "last_name": u.last_name})
    return u


@pytest.fixture
def team():
    owner, admin, member, other, outsider = (staff(f"{n}@e.local") for n in
                                             ("owner", "admin", "member", "other", "outsider"))
    group = start_thread(creator=owner, participants=[admin, member, other], subject="EXCO",
                         initial_body="Hello", thread_type=Thread.GROUP)
    gs.set_role(group, owner, admin.pk, gs.ADMIN)
    return {"owner": owner, "admin": admin, "member": member, "other": other, "outsider": outsider,
            "group": group}


def client(user):
    c = Client()
    c.force_login(user)
    return c


XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest", "HTTP_ACCEPT": "application/json"}


# ---------------------------------------------------------------------------
# L1 - the back button is inside the sticky header
# ---------------------------------------------------------------------------
def test_l1_back_button_inside_sticky_header(team):
    html = client(team["member"]).get(reverse("messaging:thread", args=[team["group"].pk])).content.decode()
    head_start = html.index('class="msg-chat-head"')
    head_end = html.index("</header>", head_start)
    back = html.index("data-chat-back")
    assert head_start < back < head_end
    assert reverse("messaging:inbox") in html[back - 400:back]
    assert "4 members, 2 admins" in html


# ---------------------------------------------------------------------------
# L3 - roles, settings, members
# ---------------------------------------------------------------------------
def test_l3_group_roles_and_permissions(team):
    g, owner, admin, member, other, outsider = (team[k] for k in
                                                ("group", "owner", "admin", "member", "other", "outsider"))
    roles = dict(g.participants.values_list("user_id", "role"))
    assert roles[owner.pk] == "owner" and roles[admin.pk] == "admin" and roles[member.pk] == "member"

    # Members cannot add, remove, promote, change settings or (by default) edit info.
    with pytest.raises(PermissionDenied):
        gs.add_members(g, member, [outsider.pk])
    with pytest.raises(PermissionDenied):
        gs.remove_member(g, member, other.pk)
    with pytest.raises(PermissionDenied):
        gs.set_role(g, member, other.pk, gs.ADMIN)
    with pytest.raises(PermissionDenied):
        gs.update_settings(g, member, send_policy=Thread.ADMINS)
    with pytest.raises(PermissionDenied):
        gs.update_info(g, member, title="Hijack")

    # Admins can; every change posts a system message.
    before = g.messages.filter(is_system=True).count()
    assert gs.add_members(g, admin, [outsider.pk]) == [outsider]
    gs.update_info(g, admin, title="EXCO Secretariat", description="Pack and minutes")
    gs.set_role(g, admin, member.pk, gs.ADMIN)
    gs.set_role(g, admin, member.pk, gs.MEMBER)
    gs.remove_member(g, admin, other.pk)
    assert g.messages.filter(is_system=True).count() == before + 6
    assert not g.participants.filter(user=other).exists()
    g.refresh_from_db()
    assert g.subject == "EXCO Secretariat"

    # Nobody removes or demotes the owner.
    with pytest.raises(PermissionDenied):
        gs.remove_member(g, admin, owner.pk)
    with pytest.raises(PermissionDenied):
        gs.set_role(g, admin, owner.pk, gs.MEMBER)

    # "Only admins can send": members are refused everywhere (text, poll, forward).
    gs.update_settings(g, owner, send_policy=Thread.ADMINS, edit_info_policy=Thread.ALL)
    with pytest.raises(ValidationError):
        cs.send_message(g, member, body="hi")
    with pytest.raises(ValidationError):
        cs.create_poll(g, member, question="Q?", options=["a", "b"])
    direct = start_thread(creator=member, participants=[owner], initial_body="psst")
    with pytest.raises(ValidationError):
        cs.forward_message(direct.messages.first(), member, [g.pk])
    assert cs.send_message(g, admin, body="admins only now").pk
    # edit_info_policy=all lets members rename.
    gs.update_info(g, member, title="EXCO (renamed)")

    # Owner leaves: the longest-serving admin takes over.
    gs.leave_group(g, owner)
    assert g.participants.get(user=admin).role == "owner"
    assert not g.participants.filter(user=owner).exists()


def test_l3_web_and_api_enforce_admin_only(team):
    from rest_framework.test import APIClient

    from api.tests.conftest import bearer

    g, member, admin, outsider = team["group"], team["member"], team["admin"], team["outsider"]
    url = reverse("messaging:members_add", args=[g.pk])
    r = client(member).post(url, {"users": [outsider.pk]}, **XHR)
    assert r.status_code == 403 and not r.json()["ok"]
    r = client(admin).post(url, {"users": [outsider.pk]}, **XHR)
    assert r.status_code == 200 and r.json()["ok"] and "Outsider" in r.json()["html"]
    # Non-participants cannot see the group at all.
    assert client(staff("stranger@e.local")).get(reverse("messaging:group_info", args=[g.pk]), **XHR).status_code == 404

    api_member = bearer(APIClient(HTTP_HOST="localhost"), member)
    base = f"/api/v1/chats/threads/{g.pk}/"
    r = api_member.post(base + f"members/{team['other'].pk}/remove/")
    assert r.status_code == 403
    r = api_member.patch(base + "group/", {"send_policy": "admins"}, format="json")
    assert r.status_code == 403
    api_admin = bearer(APIClient(HTTP_HOST="localhost"), admin)
    r = api_admin.patch(base + "group/", {"send_policy": "admins", "description": "Only admins post"},
                        format="json")
    assert r.status_code == 200, r.content
    assert r.json()["send_policy"] == "admins" and r.json()["permissions"]["is_admin"] is True
    r = api_member.get(base + "group/")
    assert r.status_code == 200 and r.json()["permissions"]["can_send"] is False
    assert {m["role"] for m in r.json()["members"]} == {"owner", "admin", "member"}
    r = api_member.post(base + "messages/", {"body": "may I?"}, format="json")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# L4 - invite links
# ---------------------------------------------------------------------------
def test_l4_invite_token_expiry(team):
    g, owner, member, outsider = team["group"], team["owner"], team["member"], team["outsider"]
    with pytest.raises(PermissionDenied):
        gs.create_invite(g, member)                       # add_members_policy = admins
    invite = gs.create_invite(g, owner, days=2, max_uses=2)
    assert len(invite.token) >= 30 and invite.expires_at > timezone.now() + timedelta(days=1)

    # A staff member joins through the web page.
    c = client(outsider)
    page = c.get(reverse("messaging:join", args=[invite.token]))
    assert page.status_code == 200 and b"Join group" in page.content
    r = c.post(reverse("messaging:join", args=[invite.token]))
    assert r.status_code == 302 and g.participants.filter(user=outsider).exists()
    invite.refresh_from_db()
    assert invite.use_count == 1
    assert gs.join_by_invite(invite.token, outsider) == g     # already a member: no second use
    invite.refresh_from_db()
    assert invite.use_count == 1

    # Expired, revoked and used-up links refuse new people.
    late = staff("late@e.local")
    ThreadInvite.objects.filter(pk=invite.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
    with pytest.raises(ValidationError):
        gs.join_by_invite(invite.token, late)
    assert client(late).get(reverse("messaging:join", args=[invite.token])).status_code == 410
    fresh = gs.create_invite(g, owner, days=1)
    gs.revoke_invite(g, owner, fresh.pk)
    with pytest.raises(ValidationError):
        gs.join_by_invite(fresh.token, late)
    once = gs.create_invite(g, owner, days=1, max_uses=1)
    gs.join_by_invite(once.token, late)
    with pytest.raises(ValidationError):
        gs.join_by_invite(once.token, staff("third@e.local"))

    # Staff only: an institution / resource-person account cannot join.
    rp = User.objects.create_user("rp@e.local", PW, user_type="resource_person")
    ok = gs.create_invite(g, owner, days=1)
    with pytest.raises(PermissionDenied):
        gs.join_by_invite(ok.token, rp)
    with pytest.raises(Exception):
        gs.join_by_invite("not-a-token", late)


# ---------------------------------------------------------------------------
# L6 - pins
# ---------------------------------------------------------------------------
def test_l6_pin_limit(team):
    g, admin, member = team["group"], team["admin"], team["member"]
    msgs = [cs.send_message(g, member, body=f"note {i}") for i in range(4)]
    with pytest.raises(PermissionDenied):
        gs.pin_message(msgs[0], member)                   # groups: admins pin
    for m in msgs[:3]:
        gs.pin_message(m, admin)
    with pytest.raises(ValidationError):
        gs.pin_message(msgs[3], admin)                     # max 3 per chat
    assert [m.pk for m in gs.pinned_messages(g, member)] == [msgs[2].pk, msgs[1].pk, msgs[0].pk]
    gs.pin_message(msgs[0], admin, pinned=False)
    gs.pin_message(msgs[3], admin)
    assert len(gs.pinned_messages(g, member)) == 3

    # The pinned bar is in the sticky header and the payload says pinned.
    html = client(member).get(reverse("messaging:thread", args=[g.pk])).content.decode()
    head = html[html.index('class="msg-chat-head"'):html.index("data-thread>")]
    assert "data-pinned-bar" in head and "note 3" in head
    assert cs.message_payload(Message.objects.get(pk=msgs[3].pk), member)["pinned_at"]

    # Direct chats: both people may pin.
    d = start_thread(creator=member, participants=[admin], initial_body="direct")
    gs.pin_message(d.messages.first(), member)
    # The setting changes the limit.
    from config.models import ConfigEntry
    from config.registry import invalidate

    ConfigEntry.objects.update_or_create(key="messaging.pins_per_chat", scope="global",
                                         defaults={"value": "4", "value_type": "int"})
    invalidate("messaging.pins_per_chat")
    assert gs.pins_per_chat() == 4


# ---------------------------------------------------------------------------
# L7 - reminders
# ---------------------------------------------------------------------------
def test_l7_reminder_fires(team):
    from messaging.tasks import send_due_reminders

    g, member, admin = team["group"], team["member"], team["admin"]
    msg = cs.send_message(g, admin, body="Send the EXCO pack by Tuesday")
    with pytest.raises(ValidationError):
        gs.set_reminder(msg, member, remind_at=timezone.now() - timedelta(minutes=5))
    r = gs.set_reminder(msg, member, preset="1h", note="Pack!")
    assert r.remind_at > timezone.now() + timedelta(minutes=55)
    assert gs.set_reminder(msg, member, preset="3h").pk == r.pk          # one pending per message
    # The bubble shows the chip for its owner only.
    fresh = cs.get_visible_message(g, member, msg.pk)
    cs.prepare_message(fresh, member, cs.thread_participants(g))
    assert fresh.my_reminder is not None
    other = cs.get_visible_message(g, admin, msg.pk)
    cs.prepare_message(other, admin, cs.thread_participants(g))
    assert other.my_reminder is None

    assert send_due_reminders() == 0                       # not due yet
    MessageReminder.objects.filter(pk=r.pk).update(remind_at=timezone.now() - timedelta(seconds=1))
    assert send_due_reminders() == 1
    n = Notification.objects.get(recipient=member, title__startswith="Reminder:")
    assert "EXCO" in n.title and f"#m{msg.pk}" in n.url and n.category == Notification.MESSAGE
    assert send_due_reminders() == 0                       # fires once
    r.refresh_from_db()
    assert r.sent_at is not None

    # Web endpoint (custom time) and cancel.
    c = client(member)
    at = (timezone.now() + timedelta(days=2)).isoformat()
    resp = c.post(reverse("messaging:remind", args=[g.pk, msg.pk]), {"remind_at": at}, **XHR)
    assert resp.status_code == 200 and "Reminder set for" in resp.json()["html"]
    resp = c.post(reverse("messaging:remind", args=[g.pk, msg.pk]), {"cancel": "1"}, **XHR)
    assert resp.json()["ok"] and not MessageReminder.objects.filter(user=member, sent_at__isnull=True).exists()
    # A reminder for a chat you left is closed silently.
    gs.set_reminder(msg, member, preset="1h")
    gs.leave_group(g, member)
    MessageReminder.objects.filter(user=member).update(remind_at=timezone.now() - timedelta(seconds=1))
    assert send_due_reminders() == 0


# ---------------------------------------------------------------------------
# L9 - polls answer "unchanged" without database work
# ---------------------------------------------------------------------------
def test_l9_poll_unchanged_shortcut_and_invalidation(team):
    g, member, admin = team["group"], team["member"], team["admin"]
    first = cs.thread_updates(g, member, after_id=0, since=timezone.now() - timedelta(minutes=5))
    v = first["v"]
    first = cs.thread_updates(g, member, after_id=first["last_id"], since=first["server_time"], version=v)
    v = first["v"]
    with CaptureQueriesContext(connection) as q:
        again = cs.thread_updates(g, member, after_id=first["last_id"], since=first["server_time"].isoformat(),
                                  version=v)
    assert again["unchanged"] is True
    assert len(q) <= 3, [x["sql"] for x in q]      # participant + typing roster only; no write
    # A new message changes the token: the next poll is a full one and sees it.
    m = cs.send_message(g, admin, body="new!")
    full = cs.thread_updates(g, member, after_id=first["last_id"], since=first["server_time"], version=v)
    assert not full.get("unchanged") and [x.pk for x in full["new"]] == [m.pk]

    # Inbox: cached for unchanged state, refreshed by a write, and "unchanged" for a current v.
    a = cs.inbox_updates(member)
    assert cs.inbox_updates(member, version=a["v"])["unchanged"] is True
    with CaptureQueriesContext(connection) as q:
        cs.inbox_updates(member)
    assert len(q) == 0
    cs.send_message(g, admin, body="another")
    b = cs.inbox_updates(member)
    assert b["v"] != a["v"]
    row = next(t for t in b["threads"] if t["id"] == g.pk)
    assert row["preview"].endswith("another") and row["unread"] >= 1

    # Web endpoint shape.
    c = client(member)
    url = reverse("messaging:thread_updates", args=[g.pk])
    d = c.get(url, {"after": 0}, **XHR).json()
    d2 = c.get(url, {"after": d["last_id"], "since": d["server_time"], "v": d["v"]}, **XHR).json()
    d3 = c.get(url, {"after": d2.get("last_id", d["last_id"]), "since": d2.get("server_time", d["server_time"]),
                     "v": d2["v"]}, **XHR).json()
    assert d3["unchanged"] is True and "new" not in d3


def test_l9_notification_badge_cache_drops_on_change(team):
    from notifications.services import mark_all_read, notify

    c = client(team["member"])
    mark_all_read(team["member"])          # the group's first message notified them
    assert c.get("/notifications/stream/", HTTP_ACCEPT="application/json").json()["unread"] == 0
    notify(team["member"], title="Hello")
    assert c.get("/notifications/stream/", HTTP_ACCEPT="application/json").json()["unread"] == 1
    mark_all_read(team["member"])
    assert c.get("/notifications/stream/", HTTP_ACCEPT="application/json").json()["unread"] == 0


# ---------------------------------------------------------------------------
# X41 - subject-linked groups
# ---------------------------------------------------------------------------
def test_x41_group_membership_sync(team):
    from institutions.models import Institution
    from staff.models import Department

    dept = Department.objects.create(code="x41", name="X41 Dept")
    inst = Institution.unscoped.create(code="X41P", name="X41 Polytechnic", handling_department=dept,
                                       status="approved")
    inst_user = User.objects.create_user("inst@e.local", PW, user_type="institution", institution=inst)
    officer, rp_user, extra = team["owner"], team["member"], team["other"]
    subject = dept      # any model row works as the subject

    g = subject_groups.ensure_group(subject, purpose=Thread.PURPOSE_SSQ_TEAM, title="SSQ team: X41",
                                    members=[officer, rp_user, inst_user], owner=officer)
    assert g.auto_managed and g.purpose == "ssq_team" and g.subject_object == subject
    ids = set(g.participants.values_list("user_id", flat=True))
    assert ids == {officer.pk, rp_user.pk}                   # never the institution user
    assert g.participants.get(user=officer).role == "owner"
    assert g.messages.filter(is_system=True, body__startswith="Group for SSQ team: X41").exists()

    # Same subject + purpose -> the same group; the team changes -> members sync.
    again = subject_groups.ensure_group(subject, purpose="ssq_team", title="SSQ team: X41",
                                        members=[officer, extra], owner=officer)
    assert again.pk == g.pk
    assert set(g.participants.values_list("user_id", flat=True)) == {officer.pk, extra.pk}
    bodies = list(g.messages.filter(is_system=True).values_list("body", flat=True))
    assert any("joined the team" in b for b in bodies) and any("left the team" in b for b in bodies)
    # Another purpose on the same subject is a separate group.
    panel = subject_groups.ensure_group(subject, purpose="visit_panel", title="Visit panel", members=[officer])
    assert panel.pk != g.pk and subject_groups.groups_for(subject).count() == 2

    # People cannot change an auto-managed group by hand.
    with pytest.raises(ValidationError):
        gs.add_members(g, officer, [rp_user.pk])
    with pytest.raises(ValidationError):
        gs.remove_member(g, officer, extra.pk)
    with pytest.raises(ValidationError):
        gs.leave_group(g, extra)
    with pytest.raises(PermissionDenied):
        gs.create_invite(g, officer)

    # The subject closes: read-only.
    subject_groups.archive_group(subject, purpose="ssq_team")
    g.refresh_from_db()
    assert g.archived_at is not None
    with pytest.raises(ValidationError):
        cs.send_message(g, extra, body="still here?")
    perms = gs.permissions_for(g, g.participants.get(user=extra))
    assert perms["read_only"] and not perms["can_send"] and not perms["can_pin"]
    # ... and comes back if the subject reopens.
    subject_groups.ensure_group(subject, purpose="ssq_team", title="SSQ team: X41", members=[officer, extra],
                                owner=officer)
    g.refresh_from_db()
    assert g.archived_at is None
    assert cs.send_message(g, extra, body="back").pk


def test_l3_group_photo_upload_and_private_serving(team, settings, tmp_path):
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.PROTECTED_MEDIA_ROOT = str(tmp_path)
    g, admin, outsider = team["group"], team["admin"], team["outsider"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = client(admin).post(reverse("messaging:group_edit", args=[g.pk]),
                           {"title": "EXCO", "description": "", "photo": SimpleUploadedFile("g.png", png, "image/png")},
                           **XHR)
    assert r.status_code == 200 and r.json()["ok"]
    g.refresh_from_db()
    assert g.photo
    photo_url = reverse("messaging:group_photo", args=[g.pk])
    assert client(team["member"]).get(photo_url).status_code == 200
    assert client(outsider).get(photo_url).status_code == 404          # participants only
    bad = SimpleUploadedFile("x.png", b"not an image", "image/png")
    r = client(admin).post(reverse("messaging:group_edit", args=[g.pk]), {"photo": bad}, **XHR)
    assert r.status_code == 400
