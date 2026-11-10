"""Chats API (staff door): scoping, door, permissions and every action."""
import shutil
import subprocess
import tempfile
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from messaging import chat_services as cs
from messaging.models import (
    ChatPoll, Message, MessageAttachment, MessageStar, Thread, ThreadParticipant, VoiceNote, start_thread,
)

from .conftest import BASE, bearer, err

pytestmark = pytest.mark.django_db

C = BASE + "chats/"
THUMBS = "\U0001F44D"
HEART = "❤️"
HAS_FFMPEG = bool(shutil.which("ffmpeg"))


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.PROTECTED_MEDIA_ROOT = str(tmp_path / "protected")
    settings.USE_X_ACCEL_REDIRECT = False
    cache.clear()


def with_staff(user, n=[0]):
    from staff.models import Staff

    n[0] += 1
    Staff.objects.get_or_create(user=user, defaults={"employee_no": f"T{user.pk:05d}{n[0]}",
                                                      "first_name": user.first_name,
                                                      "last_name": user.last_name})
    return user


@pytest.fixture
def people(make_user):
    a = with_staff(make_user("amina.bello@t.local"))
    b = with_staff(make_user("musa.ali@t.local"))
    c = with_staff(make_user("chidi.okeke@t.local"))
    out = with_staff(make_user("outsider.x@t.local"))
    direct = start_thread(creator=a, participants=[b], initial_body="Hello Musa")
    group = start_thread(creator=a, participants=[b, c], subject="ICT Team", initial_body="Hello team")
    return {"a": a, "b": b, "c": c, "out": out, "direct": direct, "group": group}


def api(client, user):
    """A separate client per user (bearer() sets credentials on the client)."""
    from rest_framework.test import APIClient

    return bearer(APIClient(HTTP_HOST=client.defaults.get("HTTP_HOST", "localhost")), user)


def first_msg(thread):
    return thread.messages.order_by("id").first()


# ---------------------------------------------------------------------------
# Door, auth, scoping
# ---------------------------------------------------------------------------
def test_requires_auth(staff_api):
    r = staff_api.get(C + "threads/")
    assert r.status_code == 401 and err(r)["code"] == "not_authenticated"


def test_portal_users_get_wrong_door(staff_api, institution_user, rp_user):
    for user in (institution_user, rp_user):
        r = api(staff_api, user).get(C + "threads/")
        assert r.status_code == 403 and err(r)["code"] == "wrong_door"
        assert err(r)["correct_door"] == "portal"


def test_chats_do_not_exist_on_the_portal_door(portal_api, institution_user):
    r = api(portal_api, institution_user).get(C + "threads/")
    assert r.status_code == 404 and err(r)["code"] == "not_found"


def test_password_change_required(staff_api, make_user):
    user = make_user("temp.pw@t.local", must_change=True)
    r = api(staff_api, user).get(C + "threads/")
    assert r.status_code == 403 and err(r)["code"] == "password_change_required"


def test_outsider_gets_404_everywhere(staff_api, people):
    out, d = people["out"], people["direct"]
    m = first_msg(d)
    client = api(staff_api, out)
    gets = [f"threads/{d.pk}/", f"threads/{d.pk}/messages/", f"threads/{d.pk}/updates/",
            f"messages/{m.pk}/", f"messages/{m.pk}/info/"]
    for path in gets:
        r = client.get(C + path)
        assert r.status_code == 404, path
        assert err(r)["code"] == "not_found"
    posts = [(f"threads/{d.pk}/messages/", {"body": "x"}), (f"threads/{d.pk}/typing/", {}),
             (f"threads/{d.pk}/read/", {}), (f"threads/{d.pk}/pin/", {"pinned": True}),
             (f"threads/{d.pk}/mute/", {"duration": "8h"}), (f"threads/{d.pk}/archive/", {}),
             (f"threads/{d.pk}/unarchive/", {}),
             (f"threads/{d.pk}/polls/", {"question": "q", "options": ["a", "b"]}),
             (f"messages/{m.pk}/react/", {"emoji": THUMBS}), (f"messages/{m.pk}/star/", {}),
             (f"messages/{m.pk}/forward/", {"thread_ids": [d.pk]}), (f"messages/{m.pk}/task/", {}),
             (f"messages/{m.pk}/memo/", {})]
    for path, body in posts:
        r = client.post(C + path, body, format="json")
        assert r.status_code == 404, path
    assert client.patch(C + f"messages/{m.pk}/", {"body": "x"}, format="json").status_code == 404
    assert client.delete(C + f"messages/{m.pk}/?scope=me").status_code == 404
    assert client.delete(C + f"threads/{d.pk}/").status_code == 404
    assert client.get(C + "threads/999999/").status_code == 404


# ---------------------------------------------------------------------------
# Thread list + unread parity with the website
# ---------------------------------------------------------------------------
def test_thread_list_shape_and_filters(staff_api, people):
    b = people["b"]
    client = api(staff_api, b)
    r = client.get(C + "threads/")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"results", "next", "previous", "count"} and body["count"] == 2
    row = next(t for t in body["results"] if t["id"] == people["group"].pk)
    assert set(row) == {"id", "title", "kind", "avatar", "participants", "participant_count", "last_message",
                        "last_message_at", "unread", "pinned", "muted", "muted_until", "archived", "typing"}
    assert row["title"] == "ICT Team" and row["kind"] == "group" and row["unread"] == 1
    assert row["avatar"] == {"initials": "IT", "color": cs.avatar_color(people["group"].pk),
                             "seed": people["group"].pk}
    assert row["participant_count"] == 3 and len(row["participants"]) == 2
    assert row["last_message"]["preview"] == "Hello team" and row["last_message"]["kind"] == "text"
    assert row["last_message"]["sender_name"] == "Amina Test"
    direct_row = next(t for t in body["results"] if t["id"] == people["direct"].pk)
    assert direct_row["kind"] == "direct" and direct_row["avatar"]["initials"] == "AT"

    assert [t["id"] for t in client.get(C + "threads/?filter=groups").json()["results"]] == [people["group"].pk]
    assert [t["id"] for t in client.get(C + "threads/?filter=direct").json()["results"]] == [people["direct"].pk]
    assert client.get(C + "threads/?filter=unread").json()["count"] == 2
    assert [t["id"] for t in client.get(C + "threads/?q=ict").json()["results"]] == [people["group"].pk]
    assert client.get(C + "threads/?q=amina").json()["count"] == 2
    r = client.get(C + "threads/?filter=bogus")
    assert r.status_code == 400 and "filter" in err(r)["fields"]

    client.post(C + f"threads/{people['group'].pk}/archive/")
    assert [t["id"] for t in client.get(C + "threads/?filter=archived").json()["results"]] == [people["group"].pk]
    assert client.get(C + "threads/").json()["count"] == 1


def test_list_previews_for_voice_poll_attachment(staff_api, people):
    a, group = people["a"], people["group"]
    poll = cs.create_poll(group, a, question="Lunch?", options=["Rice", "Beans"])
    row = next(t for t in api(staff_api, people["b"]).get(C + "threads/").json()["results"] if t["id"] == group.pk)
    assert row["last_message"] == {"id": poll.pk, "kind": "poll", "preview": "Poll: Lunch?",
                                   "sender_name": "Amina Test", "sent_at": row["last_message"]["sent_at"]}
    m = cs.send_message(people["direct"], a, uploads=[SimpleUploadedFile("a.pdf", b"%PDF-1.4 x")])
    row = next(t for t in api(staff_api, people["b"]).get(C + "threads/").json()["results"]
               if t["id"] == people["direct"].pk)
    assert row["last_message"]["kind"] == "attachment" and row["last_message"]["preview"] == "Attachment"
    assert m.pk == row["last_message"]["id"]


def test_unread_counts_match_the_website(staff_api, client, people):
    a, b = people["a"], people["b"]
    cs.send_message(people["direct"], a, body="second")
    cs.send_message(people["group"], people["c"], body="from chidi")
    hidden = cs.send_message(people["group"], a, body="hide me")
    cs.delete_message(hidden, b, "me")
    gone = cs.send_message(people["group"], a, body="oops")
    cs.delete_message(gone, a, "everyone")

    api_rows = api(staff_api, b).get(C + "threads/").json()["results"]
    api_total = sum(t["unread"] for t in api_rows)

    client.force_login(b)
    page = client.get("/messages/")
    assert page.status_code == 200
    assert api_total == page.context["total_unread"] == 4
    me = api(staff_api, b).get(BASE + "me/").json()
    assert me["unread"]["chats"] == api_total
    assert {t["id"]: t["unread"] for t in api_rows} == {
        t.pk: t.unread_count_for(b) for t in Thread.objects.filter(participants__user=b)}


# ---------------------------------------------------------------------------
# Create + contacts
# ---------------------------------------------------------------------------
def without_staff(user):
    from staff.models import Staff

    Staff.objects.filter(user=user).update(user=None)
    return user


def test_contacts_follow_the_compose_picker(staff_api, people, make_user):
    no_staff = without_staff(make_user("nostaff.person@t.local"))
    inactive = with_staff(make_user("gone.person@t.local"))
    inactive.is_active = False
    inactive.save()
    r = api(staff_api, people["a"]).get(C + "contacts/")
    assert r.status_code == 200
    ids = {u["id"] for u in r.json()["results"]}
    assert ids == {people["b"].pk, people["c"].pk, people["out"].pk}
    assert no_staff.pk not in ids and inactive.pk not in ids and people["a"].pk not in ids
    row = r.json()["results"][0]
    assert set(row) == {"id", "name", "initials", "color", "email", "department", "designation"}
    r = api(staff_api, people["a"]).get(C + "contacts/?q=chidi")
    assert [u["id"] for u in r.json()["results"]] == [people["c"].pk]


def test_create_thread(staff_api, people, make_user):
    client = api(staff_api, people["a"])
    r = client.post(C + "threads/", {"participants": [people["c"].pk], "subject": "", "body": "Hi Chidi"},
                    format="json")
    assert r.status_code == 201, r.content
    data = r.json()
    assert data["kind"] == "direct" and data["title"] == "Chidi Test"
    assert {p["id"] for p in data["participants"]} == {people["a"].pk, people["c"].pk}
    me = next(p for p in data["participants"] if p["is_me"])
    assert me["role"] == "creator"
    thread = Thread.objects.get(pk=data["id"])
    assert first_msg(thread).body == "Hi Chidi"

    # multipart with an attachment, group
    up = SimpleUploadedFile("note.txt", b"hello", content_type="text/plain")
    r = client.post(C + "threads/", {"participants": [people["b"].pk, people["c"].pk], "subject": "Plan",
                                     "body": "See file", "attachments": [up]}, format="multipart")
    assert r.status_code == 201 and r.json()["kind"] == "group"
    assert MessageAttachment.objects.filter(message__thread_id=r.json()["id"]).count() == 1


def test_create_thread_validation(staff_api, people, make_user):
    client = api(staff_api, people["a"])
    r = client.post(C + "threads/", {"participants": [], "body": "x"}, format="json")
    assert r.status_code == 400 and "participants" in err(r)["fields"]
    r = client.post(C + "threads/", {"participants": [people["b"].pk], "body": ""}, format="json")
    assert r.status_code == 400 and "body" in err(r)["fields"]
    no_staff = without_staff(make_user("nostaff2@t.local"))
    r = client.post(C + "threads/", {"participants": [no_staff.pk], "body": "x"}, format="json")
    assert r.status_code == 400 and err(r)["message"] == "No valid recipients."
    big = SimpleUploadedFile("big.bin", b"0" * (5 * 1024 * 1024 + 1))
    r = client.post(C + "threads/", {"participants": [people["b"].pk], "body": "x", "attachments": [big]},
                    format="multipart")
    assert r.status_code == 400 and "5 MB" in err(r)["message"]


# ---------------------------------------------------------------------------
# Thread detail, messages paging, updates
# ---------------------------------------------------------------------------
def test_thread_detail(staff_api, people):
    g = people["group"]
    r = api(staff_api, people["b"]).get(C + f"threads/{g.pk}/")
    assert r.status_code == 200
    d = r.json()
    assert d["title"] == "ICT Team" and d["can_post"] is True and d["can_create_poll"] is True
    assert d["created_by"]["id"] == people["a"].pk and len(d["participants"]) == 3
    assert d["limits"]["attachments_total_bytes"] == 5 * 1024 * 1024
    assert THUMBS in d["reaction_emojis"]
    assert api(staff_api, people["b"]).get(C + f"threads/{people['direct'].pk}/").json()["can_create_poll"] is False


def test_messages_paging_and_marks_read(staff_api, people):
    a, b, d = people["a"], people["b"], people["direct"]
    ids = [first_msg(d).pk] + [cs.send_message(d, a, body=f"m{i}").pk for i in range(9)]
    client = api(staff_api, b)
    r = client.get(C + f"threads/{d.pk}/messages/?limit=4")
    assert r.status_code == 200
    page = r.json()
    assert [m["id"] for m in page["results"]] == ids[-4:]  # newest page, chronological
    assert page["has_older"] is True and page["has_newer"] is False and page["previous"] is None
    assert f"before={ids[-4]}" in page["next"] and page["last_id"] == ids[-1]
    assert d.unread_count_for(b) == 0  # opening marks read
    r = client.get(C + f"threads/{d.pk}/messages/?before={ids[-4]}&limit=4")
    assert [m["id"] for m in r.json()["results"]] == ids[2:6]
    r = client.get(C + f"threads/{d.pk}/messages/?before={ids[2]}&limit=4").json()
    assert [m["id"] for m in r["results"]] == ids[:2] and r["has_older"] is False and r["next"] is None
    r = client.get(C + f"threads/{d.pk}/messages/?around={ids[5]}&limit=5").json()
    assert ids[5] in [m["id"] for m in r["results"]] and len(r["results"]) == 5
    assert r["has_older"] and r["has_newer"]
    r = client.get(C + f"threads/{d.pk}/messages/?after={ids[6]}&limit=10").json()
    assert [m["id"] for m in r["results"]] == ids[7:]
    r = client.get(C + f"threads/{d.pk}/messages/?before=1&around=2")
    assert r.status_code == 400 and err(r)["code"] == "validation_error"
    r = client.get(C + f"threads/{d.pk}/messages/?limit=500")
    assert r.status_code == 400 and "limit" in err(r)["fields"]
    # a message hidden for me is not a valid jump target
    cs.delete_message(Message.objects.get(pk=ids[3]), b, "me")
    assert client.get(C + f"threads/{d.pk}/messages/?around={ids[3]}").status_code == 404


def test_message_shape(staff_api, people):
    a, b, d = people["a"], people["b"], people["direct"]
    first = first_msg(d)
    reply = cs.send_message(d, b, body="Reply", reply_to_id=first.pk,
                            uploads=[SimpleUploadedFile("r.pdf", b"%PDF-1.4 y")])
    r = api(staff_api, b).get(C + f"messages/{reply.pk}/")
    assert r.status_code == 200
    m = r.json()
    assert set(m) == {"id", "thread_id", "kind", "sender", "is_mine", "is_system", "body", "sent_at", "edited",
                      "edited_at", "changed_at", "deleted", "forwarded", "reply_to", "attachments", "voice_note",
                      "poll", "reactions", "my_reaction", "starred", "receipt", "linked_task", "linked_memo",
                      "can_edit", "can_delete_for_everyone",
                      "pinned_at", "my_reminder_at"}  # v2.1 L6/L7
    assert m["kind"] == "text" and m["is_mine"] and m["can_edit"]
    assert m["reply_to"] == {"id": first.pk, "sender_name": "Amina Test", "preview": "Hello Musa"}
    att = m["attachments"][0]
    assert att["name"] == "r.pdf" and att["content_type"] == "application/pdf" and att["size_bytes"] == 10
    assert att["download_path"] == f"/api/v1/chats/attachments/{att['id']}/"
    assert m["receipt"] == {"state": "sent", "read_by": 0, "delivered_to": 0, "total": 1, "label": "Sent"}
    # a's view: not mine, no receipt
    m2 = api(staff_api, a).get(C + f"messages/{reply.pk}/").json()
    assert m2["receipt"] is None and not m2["is_mine"] and not m2["can_edit"]


def test_updates_new_changed_receipts_typing(staff_api, people):
    a, b, d = people["a"], people["b"], people["direct"]
    ca, cb = api(staff_api, a), api(staff_api, b)
    first = cb.get(C + f"threads/{d.pk}/updates/?after=0").json()
    assert [m["id"] for m in first["new"]] == [first_msg(d).pk]
    assert set(first) == {"server_time", "last_id", "new", "changed", "receipts", "typing",
                          "v", "unchanged"}  # v2.1 L9
    new = cs.send_message(d, a, body="Are you there?")
    ca2 = api(staff_api, a)
    ca2.post(C + f"threads/{d.pk}/typing/", {}, format="json")
    upd = cb.get(C + f"threads/{d.pk}/updates/", {"after": first["last_id"], "since": first["server_time"]}).json()
    assert [m["id"] for m in upd["new"]] == [new.pk] and upd["last_id"] == new.pk
    assert upd["typing"] == [{"id": a.pk, "name": "Amina Test", "first_name": "Amina"}]
    # a now sees "read" receipts on its messages
    ua = ca.get(C + f"threads/{d.pk}/updates/", {"after": new.pk}).json()
    assert ua["receipts"][str(new.pk)]["state"] == "read" and ua["receipts"][str(new.pk)]["label"] == "Read"
    # an edit comes back under "changed"
    cs.edit_message(new, a, "Edited")
    upd2 = cb.get(C + f"threads/{d.pk}/updates/", {"after": upd["last_id"], "since": upd["server_time"]}).json()
    assert upd2["new"] == [] and upd2["changed"][0]["body"] == "Edited" and upd2["changed"][0]["edited"]
    # typing stop clears it
    ca2.post(C + f"threads/{d.pk}/typing/", {"state": "stop"}, format="json")
    assert cb.get(C + f"threads/{d.pk}/updates/", {"after": upd["last_id"]}).json()["typing"] == []
    r = ca2.post(C + f"threads/{d.pk}/typing/", {"state": "dance"}, format="json")
    assert r.status_code == 400 and "state" in err(r)["fields"]


def test_updates_not_visible_only_delivers(staff_api, people):
    a, b, d = people["a"], people["b"], people["direct"]
    cs.send_message(d, a, body="x")
    api(staff_api, b).get(C + f"threads/{d.pk}/updates/?after=0&visible=false")
    assert d.unread_count_for(b) == 2
    p = ThreadParticipant.objects.get(thread=d, user=b)
    assert p.last_delivered_at is not None


# ---------------------------------------------------------------------------
# Sending, editing, deleting
# ---------------------------------------------------------------------------
def test_send_message_and_attachment_download(staff_api, people):
    a, b, d = people["a"], people["b"], people["direct"]
    client = api(staff_api, a)
    up = SimpleUploadedFile("plan.txt", b"the plan", content_type="text/plain")
    r = client.post(C + f"threads/{d.pk}/messages/", {"body": "With file", "reply_to": first_msg(d).pk,
                                                       "attachments": [up]}, format="multipart")
    assert r.status_code == 201, r.content
    m = r.json()
    assert m["body"] == "With file" and m["reply_to"]["id"] == first_msg(d).pk
    path = m["attachments"][0]["download_path"]
    r = api(staff_api, b).get(path)
    assert r.status_code == 200 and b"".join(r.streaming_content) == b"the plan"
    assert "plan.txt" in r["Content-Disposition"]
    assert api(staff_api, people["out"]).get(path).status_code == 404
    # hidden for b -> 404 for b
    cs.delete_message(Message.objects.get(pk=m["id"]), b, "me")
    assert api(staff_api, b).get(path).status_code == 404
    # expired -> 404
    MessageAttachment.objects.filter(pk=m["attachments"][0]["id"]).update(
        expires_at=timezone.now() - timedelta(days=1))
    assert client.get(path).status_code == 404


def test_send_validation(staff_api, people):
    client = api(staff_api, people["a"])
    d = people["direct"]
    r = client.post(C + f"threads/{d.pk}/messages/", {"body": "  "}, format="json")
    assert r.status_code == 400 and err(r)["message"] == "Write a message or attach a file."
    r = client.post(C + f"threads/{d.pk}/messages/", {"body": "x", "reply_to": first_msg(people["group"]).pk},
                    format="json")
    assert r.status_code == 400 and "replying to" in err(r)["message"]
    big = SimpleUploadedFile("big.bin", b"0" * (5 * 1024 * 1024 + 1))
    r = client.post(C + f"threads/{d.pk}/messages/", {"attachments": [big]}, format="multipart")
    assert r.status_code == 400 and "5 MB" in err(r)["message"]


def test_edit_and_delete(staff_api, people):
    a, b, d = people["a"], people["b"], people["direct"]
    m = cs.send_message(d, a, body="typo")
    ca, cb = api(staff_api, a), api(staff_api, b)
    r = ca.patch(C + f"messages/{m.pk}/", {"body": "fixed"}, format="json")
    assert r.status_code == 200 and r.json()["body"] == "fixed" and r.json()["edited"]
    r = cb.patch(C + f"messages/{m.pk}/", {"body": "hack"}, format="json")
    assert r.status_code == 400 and err(r)["code"] == "validation_error"
    r = ca.patch(C + f"messages/{m.pk}/", {"body": ""}, format="json")
    assert r.status_code == 400 and "body" in err(r)["fields"]
    # window passed
    Message.objects.filter(pk=m.pk).update(sent_at=timezone.now() - timedelta(minutes=20))
    assert ca.patch(C + f"messages/{m.pk}/", {"body": "late"}, format="json").status_code == 400
    assert ca.delete(C + f"messages/{m.pk}/?scope=everyone").status_code == 400

    m2 = cs.send_message(d, a, body="secret")
    r = cb.delete(C + f"messages/{m2.pk}/?scope=everyone")
    assert r.status_code == 400  # not the sender
    r = ca.delete(C + f"messages/{m2.pk}/", {"scope": "everyone"}, format="json")
    assert r.status_code == 200 and r.json()["deleted"] and r.json()["kind"] == "deleted"
    assert r.json()["body"] == "This message was deleted."
    r = cb.delete(C + f"messages/{m.pk}/?scope=me")
    assert r.status_code == 204
    assert cb.get(C + f"messages/{m.pk}/").status_code == 404
    assert ca.get(C + f"messages/{m.pk}/").status_code == 200
    r = ca.delete(C + f"messages/{m.pk}/?scope=nobody")
    assert r.status_code == 400 and "scope" in err(r)["fields"]


# ---------------------------------------------------------------------------
# Reactions, stars, forward, info, prefill
# ---------------------------------------------------------------------------
def test_react_toggle(staff_api, people):
    m = first_msg(people["direct"])
    cb = api(staff_api, people["b"])
    r = cb.post(C + f"messages/{m.pk}/react/", {"emoji": THUMBS}, format="json")
    assert r.status_code == 200 and r.json()["my_reaction"] == THUMBS
    assert r.json()["message"]["reactions"] == [{"emoji": THUMBS, "count": 1, "users": ["You"], "mine": True}]
    r = cb.post(C + f"messages/{m.pk}/react/", {"emoji": HEART}, format="json")
    assert r.json()["my_reaction"] == HEART
    r = cb.post(C + f"messages/{m.pk}/react/", {"emoji": HEART}, format="json")
    assert r.json()["my_reaction"] is None and r.json()["message"]["reactions"] == []
    r = cb.post(C + f"messages/{m.pk}/react/", {"emoji": "X"}, format="json")
    assert r.status_code == 400 and err(r)["message"] == "That reaction is not available."
    a_view = api(staff_api, people["a"]).post(C + f"messages/{m.pk}/react/", {"emoji": THUMBS}, format="json")
    assert a_view.json()["message"]["reactions"][0]["users"] == ["You"]


def test_star_and_starred_list(staff_api, people):
    m = first_msg(people["group"])
    cb = api(staff_api, people["b"])
    r = cb.post(C + f"messages/{m.pk}/star/")
    assert r.status_code == 200 and r.json()["starred"] is True and r.json()["message"]["starred"]
    r = cb.get(C + "starred/")
    assert r.status_code == 200 and r.json()["count"] == 1
    row = r.json()["results"][0]
    assert row["thread_id"] == people["group"].pk and row["thread_title"] == "ICT Team"
    assert row["message"]["id"] == m.pk
    assert api(staff_api, people["c"]).get(C + "starred/").json()["count"] == 0
    assert cb.post(C + f"messages/{m.pk}/star/").json()["starred"] is False
    assert not MessageStar.objects.exists()


def test_forward(staff_api, people):
    a, b = people["a"], people["b"]
    m = first_msg(people["direct"])
    cb = api(staff_api, b)
    r = cb.post(C + f"messages/{m.pk}/forward/", {"thread_ids": [people["group"].pk]}, format="json")
    assert r.status_code == 201 and r.json()["count"] == 1
    fwd = Message.objects.get(pk=r.json()["message_ids"][0])
    assert fwd.forwarded and fwd.thread_id == people["group"].pk and fwd.sender_id == b.pk
    other = start_thread(creator=people["c"], participants=[people["out"]], initial_body="private")
    r = cb.post(C + f"messages/{m.pk}/forward/", {"thread_ids": [other.pk]}, format="json")
    assert r.status_code == 400 and "own conversations" in err(r)["message"]
    r = cb.post(C + f"messages/{m.pk}/forward/", {"thread_ids": []}, format="json")
    assert r.status_code == 400 and "thread_ids" in err(r)["fields"]


def test_message_info(staff_api, people):
    a, g = people["a"], people["group"]
    m = cs.send_message(g, a, body="Read me")
    cs.mark_read(g, people["b"])
    r = api(staff_api, a).get(C + f"messages/{m.pk}/info/")
    assert r.status_code == 200
    info = r.json()
    assert info["receipt"] == {"state": "sent", "read_by": 1, "delivered_to": 1, "total": 2}
    assert [row["user_id"] for row in info["read"]] == [people["b"].pk]
    assert [row["user_id"] for row in info["pending"]] == [people["c"].pk]
    assert info["read"][0]["read_at"] and info["delivered"] == []


def test_task_and_memo_prefill(staff_api, people):
    m = first_msg(people["direct"])
    cb = api(staff_api, people["b"])
    r = cb.post(C + f"messages/{m.pk}/task/")
    assert r.status_code == 200
    assert r.json()["title"] == "Hello Musa" and "From a chat message by Amina Test" in r.json()["description"]
    assert r.json()["from_message"] == m.pk
    r = cb.post(C + f"messages/{m.pk}/memo/")
    assert r.status_code == 200 and r.json()["title"] == "Hello Musa"


# ---------------------------------------------------------------------------
# Thread actions
# ---------------------------------------------------------------------------
def test_read_pin_mute_archive(staff_api, people):
    b, d = people["b"], people["direct"]
    cb = api(staff_api, b)
    r = cb.post(C + f"threads/{d.pk}/read/")
    assert r.status_code == 200 and r.json() == {"marked": 1, "unread": 0}

    assert cb.post(C + f"threads/{d.pk}/pin/", {"pinned": True}, format="json").json() == {"pinned": True}
    rows = cb.get(C + "threads/").json()["results"]
    assert rows[0]["id"] == d.pk and rows[0]["pinned"]
    assert cb.post(C + f"threads/{d.pk}/pin/", {"pinned": False}, format="json").json() == {"pinned": False}
    r = cb.post(C + f"threads/{d.pk}/pin/", {}, format="json")
    assert r.status_code == 400 and "pinned" in err(r)["fields"]

    r = cb.post(C + f"threads/{d.pk}/mute/", {"duration": "8h"}, format="json")
    assert r.status_code == 200 and r.json()["muted"] is True and r.json()["muted_until"]
    r = cb.post(C + f"threads/{d.pk}/mute/", {"duration": "always"}, format="json")
    assert r.json()["muted_until"].startswith("2999") or r.json()["muted_until"].startswith("3000")
    assert cb.get(C + f"threads/{d.pk}/").json()["muted"] is True
    assert cb.post(C + f"threads/{d.pk}/mute/", {"duration": "off"}, format="json").json() == \
        {"muted": False, "muted_until": None}
    r = cb.post(C + f"threads/{d.pk}/mute/", {"duration": "2y"}, format="json")
    assert r.status_code == 400 and "duration" in err(r)["fields"]

    assert cb.post(C + f"threads/{d.pk}/archive/").json() == {"archived": True}
    assert cb.get(C + f"threads/{d.pk}/").json()["archived"] is True
    assert cb.post(C + f"threads/{d.pk}/unarchive/").json() == {"archived": False}


def test_pin_limit(staff_api, people):
    a = people["a"]
    threads = [start_thread(creator=a, participants=[people["b"]], subject=f"T{i}", initial_body="x")
               for i in range(6)]
    ca = api(staff_api, a)
    for t in threads[:5]:
        assert ca.post(C + f"threads/{t.pk}/pin/", {"pinned": True}, format="json").status_code == 200
    r = ca.post(C + f"threads/{threads[5].pk}/pin/", {"pinned": True}, format="json")
    assert r.status_code == 400 and "up to 5" in err(r)["message"]


def test_delete_chat_for_me_then_erased(staff_api, people):
    a, b, d = people["a"], people["b"], people["direct"]
    assert api(staff_api, b).delete(C + f"threads/{d.pk}/").status_code == 204
    assert api(staff_api, b).get(C + f"threads/{d.pk}/").status_code == 404
    assert Thread.objects.filter(pk=d.pk).exists()
    assert api(staff_api, a).delete(C + f"threads/{d.pk}/").status_code == 204
    assert not Thread.objects.filter(pk=d.pk).exists()


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------
def test_polls(staff_api, people):
    g = people["group"]
    ca, cb = api(staff_api, people["a"]), api(staff_api, people["b"])
    r = ca.post(C + f"threads/{g.pk}/polls/", {"question": "Meeting day?", "options": ["Mon", "Tue", "mon"],
                                               "allow_multiple": False}, format="json")
    assert r.status_code == 201, r.content
    msg = r.json()
    assert msg["kind"] == "poll" and [o["text"] for o in msg["poll"]["options"]] == ["Mon", "Tue"]
    poll_id = msg["poll"]["id"]
    opt = msg["poll"]["options"][1]["id"]
    r = cb.post(C + f"polls/{poll_id}/vote/", {"option_ids": [opt]}, format="json")
    assert r.status_code == 200
    p = r.json()["poll"]
    assert p["voted"] and p["voter_count"] == 1 and p["options"][1]["mine"] and p["options"][1]["percent"] == 100
    r = cb.post(C + f"polls/{poll_id}/vote/", {"option_ids": [msg["poll"]["options"][0]["id"], opt]},
                format="json")
    assert r.status_code == 400 and "one answer" in err(r)["message"]
    r = cb.post(C + f"polls/{poll_id}/vote/", {"option_ids": [999999]}, format="json")
    assert r.status_code == 400
    r = cb.post(C + f"polls/{poll_id}/vote/", {"option_ids": [opt]}, format="json")  # same again withdraws
    assert r.json()["poll"]["voted"] is False
    assert api(staff_api, people["out"]).post(C + f"polls/{poll_id}/vote/", {"option_ids": [opt]},
                                              format="json").status_code == 404
    r = ca.post(C + f"threads/{people['direct'].pk}/polls/", {"question": "q", "options": ["a", "b"]},
                format="json")
    assert r.status_code == 400 and "group chats" in err(r)["message"]
    r = ca.post(C + f"threads/{g.pk}/polls/", {"question": "q", "options": ["only"]}, format="json")
    assert r.status_code == 400 and "between 2 and 6" in err(r)["message"]
    r = ca.post(C + f"threads/{g.pk}/polls/", {"options": ["a", "b"]}, format="json")
    assert r.status_code == 400 and "question" in err(r)["fields"]
    assert ChatPoll.objects.count() == 1


# ---------------------------------------------------------------------------
# Voice notes
# ---------------------------------------------------------------------------
def tone(seconds=2, ext="m4a"):
    with tempfile.TemporaryDirectory() as tmp:
        out = f"{tmp}/tone.{ext}"
        codec = ["-c:a", "aac"] if ext == "m4a" else ["-c:a", "libopus"]
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        f"sine=frequency=440:duration={seconds}", "-ac", "1", "-ar", "48000", *codec, out],
                       check=True, timeout=60)
        with open(out, "rb") as fh:
            return fh.read()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_voice_note_flow(staff_api, people, settings):
    from messaging import transcription

    settings.VOICE_TRANSCRIBE_BACKEND = "dummy"
    a, b, d = people["a"], people["b"], people["direct"]
    ca, cb = api(staff_api, a), api(staff_api, b)
    audio = SimpleUploadedFile("rec.m4a", tone(2), content_type="audio/mp4")
    r = ca.post(C + f"threads/{d.pk}/voice/", {"audio": audio, "duration_ms": 2000}, format="multipart")
    assert r.status_code == 201, r.content
    msg = r.json()
    v = msg["voice_note"]
    assert msg["kind"] == "voice" and 1500 <= v["duration_ms"] <= 2600 and len(v["waveform"]) == 48
    assert v["audio_path"] == f"/api/v1/chats/voice/{v['id']}/audio/" and v["mime"] == "audio/mp4"
    assert v["played"] is True  # the sender's own note

    voice = VoiceNote.objects.get(pk=v["id"])
    transcription.transcribe(voice)
    m = cb.get(C + f"messages/{msg['id']}/").json()
    assert m["voice_note"]["transcript_status"] == "done"
    assert m["voice_note"]["transcript"].startswith("Dummy transcript") and m["voice_note"]["played"] is False

    full = cb.get(v["audio_path"])
    assert full.status_code == 200 and full["Accept-Ranges"] == "bytes"
    size = int(full["Content-Length"])
    part = cb.get(v["audio_path"], HTTP_RANGE="bytes=0-99")
    assert part.status_code == 206 and part["Content-Range"] == f"bytes 0-99/{size}" and len(part.content) == 100
    assert cb.get(v["audio_path"], HTTP_RANGE=f"bytes={size + 10}-").status_code == 416
    assert api(staff_api, people["out"]).get(v["audio_path"]).status_code == 404

    assert cb.post(C + f"voice/{v['id']}/played/").status_code == 204
    assert cb.get(C + f"messages/{msg['id']}/").json()["voice_note"]["played"] is True
    assert api(staff_api, people["out"]).post(C + f"voice/{v['id']}/played/").status_code == 404

    r = cb.patch(C + f"voice/{v['id']}/transcript/", {"text": "hijack"}, format="json")
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"
    r = ca.patch(C + f"voice/{v['id']}/transcript/", {"text": "Corrected words"}, format="json")
    assert r.status_code == 200
    assert r.json()["voice_note"]["transcript"] == "Corrected words" and r.json()["voice_note"]["transcript_edited"]

    # search finds the transcript
    r = cb.get(C + "search/?q=corrected")
    assert r.status_code == 200 and r.json()["results"][0]["message_id"] == msg["id"]
    assert r.json()["results"][0]["kind"] == "voice"

    # opus upload works too; garbage does not
    r = ca.post(C + f"threads/{d.pk}/voice/", {"audio": SimpleUploadedFile("rec.ogg", tone(1, "ogg"),
                                                                           content_type="audio/ogg")},
                format="multipart")
    assert r.status_code == 201
    r = ca.post(C + f"threads/{d.pk}/voice/", {"audio": SimpleUploadedFile("x.m4a", b"not audio" * 20,
                                                                           content_type="audio/mp4")},
                format="multipart")
    assert r.status_code == 400 and "audio" in err(r)["message"]
    r = ca.post(C + f"threads/{d.pk}/voice/", {}, format="multipart")
    assert r.status_code == 400 and "audio" in err(r)["fields"]


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def test_search_across_my_chats(staff_api, people):
    a = people["a"]
    cs.send_message(people["group"], a, body="The budget review is on Friday")
    private = start_thread(creator=people["c"], participants=[people["out"]], initial_body="budget secrets")
    r = api(staff_api, people["b"]).get(C + "search/?q=budget")
    assert r.status_code == 200
    hits = r.json()["results"]
    assert len(hits) == 1 and hits[0]["thread_id"] == people["group"].pk
    assert set(hits[0]) == {"thread_id", "thread_title", "message_id", "kind", "snippet", "sender_name", "sent_at"}
    assert "budget" in hits[0]["snippet"].lower()
    assert all(h["thread_id"] != private.pk for h in hits)
    r = api(staff_api, people["b"]).get(C + "search/?q=b")
    assert r.status_code == 400 and "q" in err(r)["fields"]
