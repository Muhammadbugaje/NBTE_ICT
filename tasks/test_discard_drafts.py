"""Phase 8: "Discard my drafts" deletes only the user's own draft tasks."""
import json

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import User
from tasks.models import Task

pytestmark = pytest.mark.django_db


def _u(email):
    return User.objects.create_user(email, "Pass-word-123", must_change_password=False)


def _task(ref, creator, state=Task.DRAFT):
    return Task.objects.create(reference=ref, title=ref, created_by=creator, state=state)


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def test_discards_all_own_drafts_only():
    me, other = _u("me@t8.org"), _u("other@t8.org")
    d1, d2 = _task("TD-1", me), _task("TD-2", me)
    live = _task("TD-3", me, Task.OPEN if hasattr(Task, "OPEN") else "open")
    theirs = _task("TD-4", other)
    r = _client(me).post(reverse("tasks:discard_drafts"), HTTP_ACCEPT="application/json")
    body = json.loads(r.content)
    assert sorted(body["done"]) == sorted([d1.pk, d2.pk])
    assert not Task.objects.filter(pk__in=[d1.pk, d2.pk]).exists()
    assert Task.objects.filter(pk__in=[live.pk, theirs.pk]).count() == 2


def test_selected_ids_of_someone_else_are_skipped():
    me, other = _u("me2@t8.org"), _u("other2@t8.org")
    theirs = _task("TD-5", other)
    body = json.loads(_client(me).post(reverse("tasks:discard_drafts"), {"ids": [theirs.pk]},
                                       HTTP_ACCEPT="application/json").content)
    assert body["done"] == [] and body["skipped"][0]["id"] == theirs.pk
    assert Task.objects.filter(pk=theirs.pk).exists()


def test_my_tasks_page_offers_the_button_only_with_drafts():
    me = _u("me3@t8.org")
    c = _client(me)
    assert b"Discard my drafts" not in c.get(reverse("tasks:my_tasks")).content
    _task("TD-6", me)
    assert b"Discard my drafts (1)" in c.get(reverse("tasks:my_tasks")).content
