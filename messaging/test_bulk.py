"""Phase 8: bulk "delete for me" / archive for conversations, and memo
bulk actions with record-keeping rules."""
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.models import AuditLogEntry
from messaging import memos as memo_service
from messaging.models import (Memo, MemoRecipient, Message, MessageAttachment, MessageDeletion, Thread,
                              ThreadParticipant, start_thread)

pytestmark = pytest.mark.django_db


def _u(email):
    return User.objects.create_user(email, "Pass-word-123", must_change_password=False)


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _post(client, name, data):
    return client.post(reverse(name), data, HTTP_ACCEPT="application/json")


# ------------------------------------------------------------ conversations
def test_delete_for_me_hides_thread_and_its_history_only_for_me():
    a, b = _u("a@m8.org"), _u("b@m8.org")
    t = start_thread(creator=a, participants=[b], subject="Old talk", initial_body="first")
    body = json.loads(_post(_client(a), "messaging:bulk_threads", {"action": "delete", "ids": [t.pk]}).content)
    assert body["done"] == [t.pk]
    assert ThreadParticipant.objects.get(thread=t, user=a).is_deleted
    assert not ThreadParticipant.objects.get(thread=t, user=b).is_deleted
    assert MessageDeletion.objects.filter(user=a, message__thread=t).count() == 1
    assert Thread.objects.filter(pk=t.pk).exists()
    # b still sees it; a no longer does
    assert b"Old talk" in _client(b).get("/messages/").content
    assert b"Old talk" not in _client(a).get("/messages/").content


def test_new_message_brings_thread_back_without_old_history():
    a, b = _u("a2@m8.org"), _u("b2@m8.org")
    t = start_thread(creator=a, participants=[b], initial_body="secret old line")
    _post(_client(a), "messaging:bulk_threads", {"action": "delete", "ids": [t.pk]})
    Message.objects.create(thread=t, sender=b, body="fresh line")
    assert not ThreadParticipant.objects.get(thread=t, user=a).is_deleted
    html = _client(a).get(f"/messages/{t.pk}/").content.decode()
    assert "fresh line" in html and "secret old line" not in html


def test_thread_and_attachments_erased_when_everyone_deleted(settings):
    a, b = _u("a3@m8.org"), _u("b3@m8.org")
    t = start_thread(creator=a, participants=[b], initial_body="with file")
    att = MessageAttachment.objects.create(message=t.messages.first(),
                                           file=SimpleUploadedFile("x.pdf", b"%PDF-x"))
    path = settings.PROTECTED_MEDIA_ROOT / att.file.name
    assert path.exists()
    _post(_client(a), "messaging:bulk_threads", {"action": "delete", "ids": [t.pk]})
    assert Thread.objects.filter(pk=t.pk).exists()
    body = json.loads(_post(_client(b), "messaging:bulk_threads", {"action": "delete", "ids": [t.pk]}).content)
    assert "erased" in body["message"]
    assert not Thread.objects.filter(pk=t.pk).exists()
    assert not MessageAttachment.all_objects.filter(pk=att.pk).exists()
    assert not path.exists()
    assert AuditLogEntry.objects.filter(note__startswith="conversation erased", object_pk=str(t.pk)).exists()


def test_single_delete_chat_uses_the_same_rules():
    a, b = _u("a4@m8.org"), _u("b4@m8.org")
    t = start_thread(creator=a, participants=[b], initial_body="hi")
    _client(a).post(f"/messages/{t.pk}/delete/")
    _client(b).post(f"/messages/{t.pk}/delete/")
    assert not Thread.objects.filter(pk=t.pk).exists()


def test_non_participant_ids_are_skipped():
    a, b, c = _u("a5@m8.org"), _u("b5@m8.org"), _u("c5@m8.org")
    t = start_thread(creator=a, participants=[b], initial_body="private")
    body = json.loads(_post(_client(c), "messaging:bulk_threads", {"action": "delete", "ids": [t.pk]}).content)
    assert body["done"] == [] and body["skipped"][0]["id"] == t.pk
    assert not ThreadParticipant.objects.filter(thread=t, is_deleted=True).exists()


def test_bulk_archive_and_unarchive():
    a, b = _u("a6@m8.org"), _u("b6@m8.org")
    t = start_thread(creator=a, participants=[b], subject="Arch", initial_body="x")
    ca = _client(a)
    _post(ca, "messaging:bulk_threads", {"action": "archive", "ids": [t.pk]})
    assert ThreadParticipant.objects.get(thread=t, user=a).is_archived
    _post(ca, "messaging:bulk_threads", {"action": "unarchive", "ids": [t.pk]})
    assert not ThreadParticipant.objects.get(thread=t, user=a).is_archived


# -------------------------------------------------------------------- memos
_seq = [0]


def _memo(sender, status=Memo.DRAFT, recipients=()):
    _seq[0] += 1
    m = Memo.objects.create(reference=f"MEMO-T{_seq[0]:04d}", subject="Budget", body="Body", sender=sender,
                            status=status, is_draft=status == Memo.DRAFT,
                            published_at=timezone.now() if status == Memo.PUBLISHED else None)
    for r in recipients:
        MemoRecipient.objects.create(memo=m, user=r)
    return m


def test_sender_deletes_own_drafts():
    s = _u("s@m8.org")
    d = _memo(s)
    body = json.loads(_post(_client(s), "messaging:memo_bulk", {"action": "delete", "ids": [d.pk]}).content)
    assert body["done"] == [d.pk]
    assert not Memo.objects.filter(pk=d.pk).exists()


def test_published_memo_a_sender_sent_is_never_deleted():
    s, r = _u("s2@m8.org"), _u("r2@m8.org")
    m = _memo(s, Memo.PUBLISHED, [r])
    body = json.loads(_post(_client(s), "messaging:memo_bulk", {"action": "delete", "ids": [m.pk]}).content)
    assert body["done"] == [] and "official records" in body["skipped"][0]["reason"]
    assert Memo.objects.filter(pk=m.pk).exists()


def test_recipient_removes_memo_from_own_list_but_record_stays():
    s, r, r2 = _u("s3@m8.org"), _u("r3@m8.org"), _u("r3b@m8.org")
    m = _memo(s, Memo.PUBLISHED, [r, r2])
    assert memo_service.inbox_memos_for(r).filter(pk=m.pk).exists()
    body = json.loads(_post(_client(r), "messaging:memo_bulk", {"action": "hide", "ids": [m.pk]}).content)
    assert body["done"] == [m.pk]
    assert MemoRecipient.objects.get(memo=m, user=r).hidden_at is not None
    assert not memo_service.inbox_memos_for(r).filter(pk=m.pk).exists()
    assert memo_service.inbox_memos_for(r2).filter(pk=m.pk).exists()
    assert Memo.objects.filter(pk=m.pk).exists()


def test_recipient_archive_and_unarchive():
    s, r = _u("s4@m8.org"), _u("r4@m8.org")
    m = _memo(s, Memo.PUBLISHED, [r])
    c = _client(r)
    _post(c, "messaging:memo_bulk", {"action": "archive", "ids": [m.pk]})
    assert MemoRecipient.objects.get(memo=m, user=r).is_archived
    _post(c, "messaging:memo_bulk", {"action": "unarchive", "ids": [m.pk]})
    assert not MemoRecipient.objects.get(memo=m, user=r).is_archived


def test_stranger_cannot_touch_someone_elses_draft():
    s, x = _u("s5@m8.org"), _u("x5@m8.org")
    d = _memo(s)
    body = json.loads(_post(_client(x), "messaging:memo_bulk", {"action": "delete", "ids": [d.pk]}).content)
    assert body["done"] == []
    assert Memo.objects.filter(pk=d.pk).exists()
