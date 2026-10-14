import pytest
from django.test import Client

from accounts.models import User
from messaging.models import Message, Thread, ThreadParticipant, start_thread

pytestmark = pytest.mark.django_db


def _user(email):
    return User.objects.create_user(email, "Pass-word-123", must_change_password=False)


def test_start_thread_creates_participants_and_first_message():
    a, b = _user("a@x.org"), _user("b@x.org")
    t = start_thread(creator=a, participants=[b], subject="Hello", initial_body="Hi there")
    assert t.thread_type == Thread.DIRECT
    assert set(t.participants.values_list("user_id", flat=True)) == {a.pk, b.pk}
    assert t.messages.count() == 1
    assert t.last_message_at is not None
    # Creator has read it; other participant has not.
    creator_p = t.participants.get(user=a)
    other_p = t.participants.get(user=b)
    assert creator_p.last_read_at is not None
    assert other_p.last_read_at is None
    assert t.unread_count_for(b) == 1
    assert t.unread_count_for(a) == 0


def test_inbox_lists_my_threads_only():
    a, b, c = _user("a@x.org"), _user("b@x.org"), _user("c@x.org")
    t1 = start_thread(creator=a, participants=[b], initial_body="hi")
    t2 = start_thread(creator=b, participants=[c], initial_body="hello")
    client = Client()
    client.force_login(a)
    html = client.get("/messages/").content.decode()
    assert "hi" in html
    assert "hello" not in html


def test_replying_marks_read_and_updates_thread():
    a, b = _user("a@x.org"), _user("b@x.org")
    t = start_thread(creator=a, participants=[b], initial_body="original")
    client = Client()
    client.force_login(b)
    r = client.post(f"/messages/{t.pk}/reply/", {"body": "replying now"})
    assert r.status_code == 302
    t.refresh_from_db()
    assert t.messages.count() == 2
    # b has now read it
    assert t.participants.get(user=b).last_read_at is not None
    # a now has 1 unread
    assert t.unread_count_for(a) == 1


def test_non_participant_cannot_view_thread():
    a, b, c = _user("a@x.org"), _user("b@x.org"), _user("c@x.org")
    t = start_thread(creator=a, participants=[b], initial_body="hi")
    client = Client()
    client.force_login(c)
    assert client.get(f"/messages/{t.pk}/").status_code == 404


def test_archive_hides_thread_from_inbox():
    a, b = _user("a@x.org"), _user("b@x.org")
    t = start_thread(creator=a, participants=[b], subject="Archive me", initial_body="hi")
    client = Client()
    client.force_login(a)
    assert b"Archive me" in client.get("/messages/").content
    client.post(f"/messages/{t.pk}/archive/")
    assert b"Archive me" not in client.get("/messages/").content