"""Phase 8: bulk notification actions (internal and portal)."""
import json

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import User
from core.models import AuditLogEntry
from notifications.models import Notification

pytestmark = pytest.mark.django_db


def _u(email):
    return User.objects.create_user(email, "Pass-word-123", must_change_password=False)


def _n(user, read=False, title="N"):
    return Notification.objects.create(recipient=user, title=title, is_read=read)


def _post(client, data):
    return client.post(reverse("notifications:bulk"), data, HTTP_ACCEPT="application/json")


def test_delete_selected_only_touches_own_rows():
    me, other = _u("me@n8.org"), _u("other@n8.org")
    a, b = _n(me), _n(me)
    theirs = _n(other)
    c = Client()
    c.force_login(me)
    r = _post(c, {"action": "delete", "ids": [a.pk, theirs.pk]})
    body = json.loads(r.content)
    assert r.status_code == 200 and body["done"] == [a.pk]
    assert [s["id"] for s in body["skipped"]] == [theirs.pk]
    assert not Notification.objects.filter(pk=a.pk).exists()
    assert Notification.objects.filter(pk__in=[b.pk, theirs.pk]).count() == 2
    assert AuditLogEntry.objects.filter(note__startswith="notifications deleted").exists()


def test_delete_all_read_keeps_unread():
    me = _u("me2@n8.org")
    read1, read2, unread = _n(me, True), _n(me, True), _n(me, False)
    c = Client()
    c.force_login(me)
    body = json.loads(_post(c, {"action": "delete_read"}).content)
    assert sorted(body["done"]) == sorted([read1.pk, read2.pk])
    assert list(Notification.objects.filter(recipient=me).values_list("pk", flat=True)) == [unread.pk]


def test_mark_read_and_unread():
    me = _u("me3@n8.org")
    n = _n(me)
    c = Client()
    c.force_login(me)
    _post(c, {"action": "mark_read", "ids": [n.pk]})
    n.refresh_from_db()
    assert n.is_read and n.read_at
    _post(c, {"action": "mark_unread", "ids": [n.pk]})
    n.refresh_from_db()
    assert not n.is_read


def test_unknown_action_and_empty_selection_are_rejected():
    me = _u("me4@n8.org")
    c = Client()
    c.force_login(me)
    assert _post(c, {"action": "explode", "ids": [1]}).status_code == 400
    assert _post(c, {"action": "delete"}).status_code == 400


def test_get_and_anonymous_are_refused():
    assert Client().post(reverse("notifications:bulk"), {"action": "delete_read"}).status_code == 302
    me = _u("me5@n8.org")
    c = Client()
    c.force_login(me)
    assert c.get(reverse("notifications:bulk")).status_code == 405


def test_plain_form_post_redirects_back():
    me = _u("me6@n8.org")
    n = _n(me, True)
    c = Client()
    c.force_login(me)
    r = c.post(reverse("notifications:bulk"), {"action": "delete", "ids": [n.pk],
                                                "next": reverse("notifications:list")})
    assert r.status_code == 302 and r["Location"] == reverse("notifications:list")
    assert not Notification.objects.filter(pk=n.pk).exists()
