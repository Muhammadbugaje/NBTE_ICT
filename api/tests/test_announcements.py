"""announcements/ (staff door) plus home/ and me/ announcement fields."""
import pytest
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from .conftest import BASE, bearer, err

pytestmark = pytest.mark.django_db


def _grant(user, *codenames):
    from accounts.models import User

    for code in codenames:
        user.user_permissions.add(Permission.objects.get(content_type__app_label="announcements", codename=code))
    return User.objects.get(pk=user.pk)


@pytest.fixture
def poster(make_user):
    return _grant(make_user("poster@t.local"), "post_announcement")


@pytest.fixture
def reader(make_user):
    return make_user("reader@t.local")


def _ann(author, title="Town hall", publish=True, **kw):
    from announcements import services
    from announcements.models import Announcement

    ann = Announcement.objects.create(title=title, body=kw.pop("body", "Body text"), created_by=author, **kw)
    if publish:
        services.publish(ann, actor=author)
    return ann


def _poll(ann, *texts, multiple=False):
    from announcements import services

    return services.save_poll(ann, question="Which day?", options=list(texts), allow_multiple=multiple)


def test_list_filters_and_pinned_first(staff_api, poster, reader):
    old = _ann(poster, "Old notice")
    pinned = _ann(poster, "Pinned", pinned=True)
    ack = _ann(poster, "Sign this", requires_ack=True, kind="action_needed")
    _ann(poster, "Draft", publish=False)
    bearer(staff_api, reader)
    r = staff_api.get(BASE + "announcements/")
    assert r.status_code == 200, r.content
    body = r.json()
    assert [i["id"] for i in body["results"]][0] == pinned.pk
    assert body["count"] == 3 and body["can_post"] is False
    assert body["counts"]["unread"] == 3 and body["counts"]["needs_ack"] == 1
    assert [i["id"] for i in staff_api.get(BASE + "announcements/?filter=needs_ack").json()["results"]] == [ack.pk]
    # drafts filter falls back to "all" for non-posters
    assert staff_api.get(BASE + "announcements/?filter=drafts").json()["filter"] == "all"
    assert staff_api.get(BASE + f"announcements/{old.pk}/").json()["read"] is True
    unread = staff_api.get(BASE + "announcements/?filter=unread").json()
    assert old.pk not in [i["id"] for i in unread["results"]]
    r = staff_api.get(BASE + "announcements/?filter=nope")
    assert r.status_code == 400


def test_detail_marks_read_and_hides_drafts(staff_api, poster, reader):
    from announcements.models import AnnouncementRead

    ann = _ann(poster)
    draft = _ann(poster, "Secret draft", publish=False)
    bearer(staff_api, reader)
    r = staff_api.get(BASE + f"announcements/{ann.pk}/")
    assert r.status_code == 200 and r.json()["body"] == "Body text"
    assert AnnouncementRead.objects.filter(announcement=ann, user=reader).exists()
    assert staff_api.get(BASE + f"announcements/{draft.pk}/").status_code == 404
    assert staff_api.get(BASE + "announcements/999999/").status_code == 404


def test_acknowledge(staff_api, poster, reader):
    ann = _ann(poster, requires_ack=True)
    plain = _ann(poster, "No ack")
    bearer(staff_api, reader)
    assert staff_api.get(BASE + f"announcements/{ann.pk}/").json()["can_acknowledge"] is True
    r = staff_api.post(BASE + f"announcements/{ann.pk}/acknowledge/")
    assert r.status_code == 200 and r.json()["created"] is True
    assert staff_api.post(BASE + f"announcements/{ann.pk}/acknowledge/").json()["created"] is False
    r = staff_api.post(BASE + f"announcements/{plain.pk}/acknowledge/")
    assert r.status_code == 400 and err(r)["code"] == "validation_error"


def test_vote(staff_api, poster, reader):
    ann = _ann(poster)
    poll = _poll(ann, "Mon", "Tue")
    mon, tue = list(poll.options.values_list("pk", flat=True))
    bearer(staff_api, reader)
    detail = staff_api.get(BASE + f"announcements/{ann.pk}/").json()
    assert detail["poll"]["show_results"] is False and detail["poll"]["options"][0]["votes"] is None
    r = staff_api.post(BASE + f"announcements/{ann.pk}/vote/", {"option_ids": [tue]}, format="json")
    assert r.status_code == 200 and r.json()["has_voted"] is True
    assert [o["chosen"] for o in r.json()["options"]] == [False, True]
    r = staff_api.post(BASE + f"announcements/{ann.pk}/vote/", {"option_ids": [mon, tue]}, format="json")
    assert r.status_code == 400 and "option_ids" in err(r)["fields"]
    r = staff_api.post(BASE + f"announcements/{ann.pk}/vote/", {"option_ids": []}, format="json")
    assert r.status_code == 400
    no_poll = _ann(poster, "No poll")
    assert staff_api.post(BASE + f"announcements/{no_poll.pk}/vote/", {"option_ids": [mon]},
                          format="json").status_code == 404


def test_attachment_download(staff_api, poster, reader, make_user):
    from announcements.models import AnnouncementAttachment

    ann = _ann(poster)
    att = AnnouncementAttachment.objects.create(
        announcement=ann, file=SimpleUploadedFile("agenda.pdf", b"%PDF-1.4 test", content_type="application/pdf"))
    bearer(staff_api, reader)
    detail = staff_api.get(BASE + f"announcements/{ann.pk}/").json()
    assert detail["attachments"][0]["download_url"].endswith(f"/api/v1/announcements/{ann.pk}/attachments/{att.pk}/")
    r = staff_api.get(BASE + f"announcements/{ann.pk}/attachments/{att.pk}/")
    assert r.status_code == 200 and b"".join(r.streaming_content).startswith(b"%PDF")
    assert "agenda.pdf" in r["Content-Disposition"]
    # wrong announcement id, or a draft's attachment
    other = _ann(poster, "Other")
    assert staff_api.get(BASE + f"announcements/{other.pk}/attachments/{att.pk}/").status_code == 404
    draft = _ann(poster, "Draft", publish=False)
    datt = AnnouncementAttachment.objects.create(
        announcement=draft, file=SimpleUploadedFile("d.pdf", b"%PDF-1.4", content_type="application/pdf"))
    assert staff_api.get(BASE + f"announcements/{draft.pk}/attachments/{datt.pk}/").status_code == 404


def test_create_edit_publish(staff_api, poster, reader):
    from notifications.models import Notification

    bearer(staff_api, reader)
    r = staff_api.post(BASE + "announcements/", {"title": "x", "body": "y"}, format="json")
    assert r.status_code == 403 and err(r)["code"] == "permission_denied"

    bearer(staff_api, poster)
    r = staff_api.post(BASE + "announcements/", {"title": "", "body": "Hello", "audience": "department"},
                       format="json")
    assert r.status_code == 400 and "title" in err(r)["fields"]
    r = staff_api.post(BASE + "announcements/", {"title": "Hi", "body": "Hello", "audience": "department"},
                       format="json")
    assert r.status_code == 400 and "department" in err(r)["fields"]
    r = staff_api.post(BASE + "announcements/", {"title": "Hi", "body": "Hello", "poll_question": "Q?",
                                                 "poll_options": ["Only one"]}, format="json")
    assert r.status_code == 400 and "poll_options" in err(r)["fields"]

    r = staff_api.post(BASE + "announcements/", {"title": "Hi", "body": "Hello", "kind": "urgent",
                                                 "poll_question": "Q?", "poll_options": ["A", "B"]}, format="json")
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["status"] == "draft" and body["poll"]["question"] == "Q?" and len(body["poll"]["options"]) == 2
    pk = body["id"]
    assert not Notification.objects.filter(recipient=reader).exists()

    r = staff_api.patch(BASE + f"announcements/{pk}/", {"title": "Hi again"}, format="json")
    assert r.status_code == 200 and r.json()["title"] == "Hi again" and r.json()["kind"] == "urgent"
    assert r.json()["poll"]["question"] == "Q?"  # untouched

    r = staff_api.post(BASE + f"announcements/{pk}/publish/")
    assert r.status_code == 200 and r.json()["notified"] >= 1 and r.json()["announcement"]["status"] == "published"
    assert Notification.objects.filter(recipient=reader, metadata__announcement=pk).exists()
    assert staff_api.post(BASE + f"announcements/{pk}/publish/").json()["notified"] == 0

    # the reader can't edit or publish it
    bearer(staff_api, reader)
    assert staff_api.patch(BASE + f"announcements/{pk}/", {"title": "x"}, format="json").status_code == 403
    assert staff_api.post(BASE + f"announcements/{pk}/publish/").status_code == 403


def test_create_multipart_with_attachment_and_publish(staff_api, poster, reader):
    bearer(staff_api, poster)
    upload = SimpleUploadedFile("memo.pdf", b"%PDF-1.4 x", content_type="application/pdf")
    r = staff_api.post(BASE + "announcements/", {"title": "With file", "body": "See attached", "publish": "true",
                                                 "attachment": upload}, format="multipart")
    assert r.status_code == 201, r.content
    assert r.json()["status"] == "published" and r.json()["attachments"][0]["name"] == "memo.pdf"
    bad = SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream")
    r = staff_api.post(BASE + "announcements/", {"title": "Bad", "body": "x", "attachment": bad}, format="multipart")
    assert r.status_code == 400 and "attachment" in err(r)["fields"]


def test_report(staff_api, poster, reader, make_user):
    ann = _ann(poster, requires_ack=True)
    bearer(staff_api, reader)
    assert staff_api.get(BASE + f"announcements/{ann.pk}/report/").status_code == 403
    bearer(staff_api, poster)
    r = staff_api.get(BASE + f"announcements/{ann.pk}/report/")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["audience_size"] >= 2 and body["acknowledged"] == 1  # the poster acknowledged on publish
    assert reader.pk in [p["id"] for p in body["pending"]]
    reporter = _grant(make_user("rep@t.local"), "view_announcement_report")
    assert bearer(staff_api, reporter).get(BASE + f"announcements/{ann.pk}/report/").status_code == 200


def test_portal_and_auth_rules(staff_api, portal_api, institution_user, reader, poster):
    ann = _ann(poster)
    assert staff_api.get(BASE + "announcements/").status_code == 401
    # not routed on the portal door at all
    assert bearer(portal_api, institution_user).get(BASE + "announcements/").status_code == 404
    r = bearer(staff_api, institution_user).get(BASE + f"announcements/{ann.pk}/")
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"


def test_me_and_home_announcement_fields(staff_api, poster, reader):
    from messaging.models import ThreadParticipant, start_thread

    _ann(poster, "Plain notice")
    urgent = _ann(poster, "Fire drill", kind="urgent", requires_ack=True)
    thread = start_thread(creator=poster, participants=[reader], subject="Pinned chat", initial_body="hi")
    ThreadParticipant.objects.filter(thread=thread, user=reader).update(is_pinned=True)

    bearer(staff_api, reader)
    me = staff_api.get(BASE + "me/").json()
    assert me["unread"]["announcements"] == 2 and me["capabilities"]["post_announcements"] is False
    home = staff_api.get(BASE + "home/").json()
    assert home["announcement"] == {"id": urgent.pk, "title": "Fire drill", "kind": "urgent",
                                    "requires_ack": True, "acknowledged": False}
    assert home["pinned_chats"][0]["thread_id"] == thread.pk and home["pinned_chats"][0]["unread"] == 1
    staff_api.post(BASE + f"announcements/{urgent.pk}/acknowledge/")
    assert staff_api.get(BASE + "home/").json()["announcement"] is None
    assert bearer(staff_api, poster).get(BASE + "me/").json()["capabilities"]["post_announcements"] is True


def test_expired_hidden_from_list_but_detail_kept(staff_api, poster, reader):
    from announcements.models import Announcement

    ann = _ann(poster)
    Announcement.objects.filter(pk=ann.pk).update(expires_at=timezone.now() - timezone.timedelta(days=1))
    bearer(staff_api, reader)
    assert ann.pk not in [i["id"] for i in staff_api.get(BASE + "announcements/").json()["results"]]
    assert staff_api.get(BASE + f"announcements/{ann.pk}/").status_code == 200
