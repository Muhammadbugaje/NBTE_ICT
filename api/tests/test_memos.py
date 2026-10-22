"""Memos API (staff door): tabs, detail, every action, create, files."""
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from messaging import chat_services as cs
from messaging import memos as memo_service
from messaging.models import Memo, MemoAttachment, MemoRecipient, MemoRoute, MemoRouteRule, MemoRouteStep, \
    start_thread

from .conftest import BASE, bearer, err

pytestmark = pytest.mark.django_db

M = BASE + "memos/"


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.PROTECTED_MEDIA_ROOT = str(tmp_path / "protected")
    settings.USE_X_ACCEL_REDIRECT = False


def api(user):
    return bearer(APIClient(HTTP_HOST="localhost"), user)


@pytest.fixture
def org(make_user):
    from django.core.management import call_command

    from staff.models import Department

    call_command("seed_roles", "--sync", verbosity=0)
    Department.objects.create(name="Finance T", code="fin-t")
    Department.objects.create(name="ICT T", code="ict-t")
    route = MemoRoute.objects.create(name="Via director", code="via-director-t")
    MemoRouteStep.objects.create(route=route, position=1, name="Director approval", approver_kind="director")
    MemoRouteRule.objects.create(name="Any to a person", route=route, target_kind="specific_user", priority=10)
    return {
        "sender": make_user("sani.sender@t.local", role="officer", dept="fin-t"),
        "director": make_user("dora.director@t.local", role="director", dept="fin-t"),
        "recipient": make_user("rita.recipient@t.local", role="officer", dept="fin-t"),
        "other": make_user("olu.other@t.local", role="officer", dept="ict-t"),
        "outsider": make_user("ola.outsider@t.local", role="officer", dept="ict-t"),
    }


def create(org, **extra):
    body = {"subject": "Server upgrade", "body": "Please note the upgrade.", "visibility": "specific_user",
            "target_user": org["recipient"].pk, "action": "publish", **extra}
    r = api(org["sender"]).post(M, body, format="json")
    assert r.status_code == 201, r.content
    return r.json()


# ---------------------------------------------------------------------------
# Door, auth
# ---------------------------------------------------------------------------
def test_door_and_auth(staff_api, portal_api, institution_user, make_user):
    assert staff_api.get(M).status_code == 401
    r = bearer(staff_api, institution_user).get(M)
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"
    assert bearer(portal_api, institution_user).get(M).status_code == 404
    temp = make_user("temp.memo@t.local", must_change=True)
    r = api(temp).get(M)
    assert r.status_code == 403 and err(r)["code"] == "password_change_required"


# ---------------------------------------------------------------------------
# Full approval flow
# ---------------------------------------------------------------------------
def test_create_approve_publish_read_acknowledge_archive(org):
    sender, director, recipient, outsider = org["sender"], org["director"], org["recipient"], org["outsider"]
    created = create(org, requires_acknowledgment=True, priority="high")
    memo = created["memo"]
    assert created["notice"] == f"Memo {memo['reference']} sent for approval."
    assert memo["status"] == "awaiting" and memo["current_step"] == "Director approval"
    assert memo["route_steps"][0]["state"] == "current"
    assert memo["route_steps"][0]["actor"]["id"] == director.pk
    assert memo["actions"] == ["cancel"] and memo["is_sender"] is True
    assert memo["documents"]["watermarked"] is True
    mid = memo["id"]

    # not visible to the recipient (not published) or an outsider
    for user in (recipient, outsider):
        assert api(user).get(M + f"{mid}/").status_code == 404
        assert api(user).post(M + f"{mid}/approve/", {}, format="json").status_code == 404

    # the director sees it under "awaiting" with the decision buttons
    rows = api(director).get(M + "?tab=awaiting").json()
    assert [r["id"] for r in rows["results"]] == [mid] and rows["counts"]["awaiting"] == 1
    d = api(director).get(M + f"{mid}/").json()
    assert d["actions"] == ["approve", "return", "reject"] and d["documents"] is None
    assert api(director).get(M + f"{mid}/pdf/").status_code == 403

    # the sender can view but not approve
    r = api(sender).post(M + f"{mid}/approve/", {}, format="json")
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"
    # reject needs a comment
    r = api(director).post(M + f"{mid}/reject/", {}, format="json")
    assert r.status_code == 400 and "comment is required" in err(r)["message"]

    r = api(director).post(M + f"{mid}/approve/", {"comment": "Fine"}, format="json")
    assert r.status_code == 200
    assert r.json()["notice"] == f"Approved. Memo {memo['reference']} published to 1 recipient(s)."
    assert r.json()["memo"]["status"] == "published"
    assert r.json()["memo"]["route_steps"][0]["state"] == "approved"
    assert r.json()["memo"]["route_steps"][0]["comment"] == "Fine"

    inbox = api(recipient).get(M).json()
    assert [r["id"] for r in inbox["results"]] == [mid] and inbox["results"][0]["unread"] is True
    assert inbox["counts"]["inbox"] == 1
    d = api(recipient).get(M + f"{mid}/").json()
    assert d["my_receipt"]["read_at"] and d["actions"] == ["acknowledge", "archive"]
    assert d["stats"] is None and d["documents"]["watermarked"] is False
    assert MemoRecipient.objects.get(memo_id=mid, user=recipient).read_at is not None

    r = api(recipient).post(M + f"{mid}/acknowledge/", {}, format="json")
    assert r.status_code == 200 and r.json()["memo"]["acknowledged"] is True
    assert r.json()["memo"]["actions"] == ["archive"]
    r = api(recipient).post(M + f"{mid}/acknowledge/", {}, format="json")
    assert r.status_code == 200  # idempotent
    assert api(sender).post(M + f"{mid}/acknowledge/", {}, format="json").status_code == 403

    stats = api(sender).get(M + f"{mid}/").json()["stats"]
    assert stats == {"recipients": 1, "read": 1, "acknowledged": 1, "pending_acknowledgment": []}

    r = api(recipient).post(M + f"{mid}/archive/", {}, format="json")
    assert r.status_code == 200 and r.json()["notice"] == f"Memo {memo['reference']} archived."
    assert api(recipient).get(M).json()["count"] == 0
    assert [r["id"] for r in api(recipient).get(M + "?tab=archived").json()["results"]] == [mid]
    assert api(recipient).post(M + f"{mid}/unarchive/", {}, format="json").status_code == 200
    assert api(recipient).get(M).json()["count"] == 1
    assert api(director).post(M + f"{mid}/archive/", {}, format="json").status_code == 403

    # documents once published
    r = api(recipient).get(M + f"{mid}/pdf/")
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and r.content[:4] == b"%PDF"
    r = api(recipient).get(M + f"{mid}/docx/")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert api(outsider).get(M + f"{mid}/pdf/").status_code == 404


def test_return_resubmit_and_reject(org):
    sender, director = org["sender"], org["director"]
    mid = create(org)["memo"]["id"]
    r = api(director).post(M + f"{mid}/return/", {"comment": "Add the date"}, format="json")
    assert r.status_code == 200 and r.json()["memo"]["status"] == "returned"
    d = api(sender).get(M + f"{mid}/").json()
    assert d["actions"] == ["resubmit", "cancel"]
    assert d["returned"]["comment"] == "Add the date" and d["returned"]["by"]["id"] == director.pk
    assert api(director).post(M + f"{mid}/resubmit/", {}, format="json").status_code == 403
    r = api(sender).post(M + f"{mid}/resubmit/", {"body": "Upgrade on 1 Nov.", "comment": "Dated"}, format="json")
    assert r.status_code == 200 and r.json()["memo"]["status"] == "awaiting"
    assert r.json()["memo"]["body"] == "Upgrade on 1 Nov."
    r = api(director).post(M + f"{mid}/reject/", {"comment": "Not needed"}, format="json")
    assert r.status_code == 200 and r.json()["memo"]["status"] == "rejected"
    assert r.json()["memo"]["rejected"]["comment"] == "Not needed"
    r = api(director).post(M + f"{mid}/approve/", {}, format="json")
    assert r.status_code == 400  # no longer awaiting a decision
    r = api(sender).post(M + f"{mid}/cancel/", {}, format="json")
    assert r.status_code == 400 and "no longer be cancelled" in err(r)["message"]


def test_drafts_publish_and_cancel(org):
    sender = org["sender"]
    draft = create(org, action="draft")
    assert draft["notice"] == "Memo saved as draft." and draft["memo"]["status"] == "draft"
    mid = draft["memo"]["id"]
    assert draft["memo"]["actions"] == ["publish", "cancel"]
    lst = api(sender).get(M + "?tab=drafts").json()
    assert [r["id"] for r in lst["results"]] == [mid] and lst["counts"]["drafts"] == 1
    assert api(sender).get(M + "?tab=sent").json()["count"] == 0
    assert api(org["director"]).get(M + f"{mid}/").status_code == 404
    r = api(sender).post(M + f"{mid}/publish/", {}, format="json")
    assert r.status_code == 200 and r.json()["memo"]["status"] == "awaiting"
    assert r.json()["notice"].endswith("sent for approval.")
    assert api(sender).get(M + "?tab=sent").json()["count"] == 1
    r = api(sender).post(M + f"{mid}/publish/", {}, format="json")
    assert r.status_code == 400
    r = api(sender).post(M + f"{mid}/cancel/", {"reason": "Changed plans"}, format="json")
    assert r.status_code == 200 and r.json()["memo"]["status"] == "cancelled"


def test_publish_without_route_delivers_directly(org):
    """No routing rule for "individuals": the memo is delivered at once (and
    the website's own compose 'Send' does the same - it used to stop with
    'already entered the pipeline')."""
    r = api(org["sender"]).post(M, {"subject": "Team note", "body": "Hello all", "visibility": "individuals",
                                    "recipients": [org["recipient"].pk, org["other"].pk], "action": "publish"},
                                format="json")
    assert r.status_code == 201
    memo = r.json()["memo"]
    assert memo["status"] == "published" and r.json()["notice"].endswith("published to 2 recipient(s).")
    assert memo["audience"]
    assert api(org["other"]).get(M).json()["results"][0]["id"] == memo["id"]


def test_website_compose_send_publishes(client, org):
    client.force_login(org["sender"])
    r = client.post("/messages/memos/new/", {"subject": "Web memo", "body": "From the website",
                                             "visibility": "individuals", "recipients": [org["other"].pk],
                                             "priority": "normal", "action": "publish",
                                             "attachments": [SimpleUploadedFile("w.txt", b"web")]})
    assert r.status_code == 302
    memo = Memo.objects.get(subject="Web memo")
    assert memo.status == Memo.PUBLISHED and memo.recipient_count == 1
    assert MemoAttachment.objects.filter(memo=memo).count() == 1  # attachments used to fail silently


# ---------------------------------------------------------------------------
# Create validation, attachments, chat link
# ---------------------------------------------------------------------------
def test_create_validation(org):
    client = api(org["sender"])
    r = client.post(M, {"subject": "Hi", "body": "Body", "visibility": "specific_user"}, format="json")
    assert r.status_code == 400 and "target_user" in err(r)["fields"]
    r = client.post(M, {"subject": "Hello", "body": "Body", "visibility": "all_staff"}, format="json")
    assert r.status_code == 400 and "visibility" in err(r)["fields"]
    r = client.post(M, {"body": "Body", "visibility": "specific_user", "target_user": org["recipient"].pk},
                    format="json")
    assert r.status_code == 400 and "subject" in err(r)["fields"]
    r = client.post(M, {"subject": "Hello", "body": "Body", "visibility": "specific_user",
                        "target_user": org["sender"].pk}, format="json")
    assert r.status_code == 400  # not in the picker (yourself)
    big = SimpleUploadedFile("big.pdf", b"0" * (5 * 1024 * 1024 + 1))
    r = client.post(M, {"subject": "Hello", "body": "Body", "visibility": "specific_user",
                        "target_user": org["recipient"].pk, "attachments": [big]}, format="multipart")
    assert r.status_code == 400 and "attachments" in err(r)["fields"]
    assert not Memo.objects.exists()


def test_attachments_and_chat_link(org):
    sender, recipient = org["sender"], org["recipient"]
    thread = start_thread(creator=sender, participants=[recipient], initial_body="Let's write a memo")
    msg = thread.messages.get()
    up = SimpleUploadedFile("budget.txt", b"numbers", content_type="text/plain")
    r = api(sender).post(M, {"subject": "Budget", "body": "See attached", "visibility": "individuals",
                             "recipients": [recipient.pk], "action": "publish", "attachments": [up],
                             "from_message": msg.pk}, format="multipart")
    assert r.status_code == 201, r.content
    memo = r.json()["memo"]
    assert memo["attachments"], r.json()["notices"]
    att = memo["attachments"][0]
    assert att["name"] == "budget.txt" and att["download_path"] == f"/api/v1/memos/attachments/{att['id']}/"
    msg.refresh_from_db()
    assert msg.linked_memo_id == memo["id"]
    chat = api(recipient).get(BASE + f"chats/messages/{msg.pk}/").json()
    assert chat["linked_memo"] == {"id": memo["id"], "reference": memo["reference"], "route": f"/memos/{memo['id']}"}
    r = api(recipient).get(att["download_path"])
    assert r.status_code == 200 and b"".join(r.streaming_content) == b"numbers"
    assert api(org["outsider"]).get(att["download_path"]).status_code == 404
    assert api(recipient).get(M + "attachments/999999/").status_code == 404


def test_compose_options_and_people(org):
    opts = api(org["sender"]).get(M + "compose-options/").json()
    assert [o["value"] for o in opts["visibility"]] == ["specific_user", "individuals"]
    assert opts["can_broadcast"] is False and opts["departments"] == [] and opts["roles"] == []
    assert [p["value"] for p in opts["priorities"]] == ["normal", "high", "urgent"]
    boss = org["sender"].__class__.objects.create_superuser("boss.memo@t.local", "x-pass-12345")
    boss.must_change_password = False
    boss.save()
    opts = api(boss).get(M + "compose-options/").json()
    assert "all_staff" in [o["value"] for o in opts["visibility"]] and opts["departments"]
    people = api(org["sender"]).get(M + "people/?q=rita").json()
    assert [p["id"] for p in people["results"]] == [org["recipient"].pk]
    assert set(people["results"][0]) == {"id", "name", "initials", "color", "email", "department"}


def test_list_validation_and_search(org):
    create(org, subject="Quarterly budget")
    client = api(org["sender"])
    r = client.get(M + "?tab=nope")
    assert r.status_code == 400 and "tab" in err(r)["fields"]
    assert client.get(M + "?tab=sent&q=quarterly").json()["count"] == 1
    assert client.get(M + "?tab=sent&q=zzz").json()["count"] == 0
    assert client.get(M + "?tab=sent&priority=urgent").json()["count"] == 0
    row = client.get(M + "?tab=sent").json()["results"][0]
    assert set(row) == {"id", "reference", "subject", "summary", "priority", "status", "status_label", "sender",
                        "audience", "created_at", "published_at", "updated_at", "requires_acknowledgment",
                        "unread", "acknowledged", "current_step", "attachment_count"}
    assert row["audience"] == "Rita Test"


def test_counts_match_the_website(client, org):
    create(org)
    client.force_login(org["director"])
    page = client.get("/messages/memos/?tab=awaiting")
    data = api(org["director"]).get(M + "?tab=awaiting").json()
    for tab in ("inbox", "awaiting", "archived"):
        assert data["counts"][tab] == page.context["counts"][tab]
    assert memo_service.memo_tab_counts(org["director"])["awaiting"] == 1
