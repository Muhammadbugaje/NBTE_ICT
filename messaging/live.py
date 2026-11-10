"""Cheap answers for the chat short-polls (v2.1 Phase E, L9 / T-PERF-5).

Two tools, both in the shared cache (Redis in production, like the typing
indicator):

* **Version tokens.** ``thread_version(id)`` / ``inbox_version(user_id)`` are
  random tokens replaced ("bumped") after any write that changes what a poll
  would return: a message saved, a reaction, a vote, a read receipt, a member
  added... Bumps run on transaction commit, so a poll never sees a new token
  before the row it stands for. A client sends back the token of its last
  answer (``?v=``); when it still matches, the poll answers ``unchanged``
  without touching the database (no message queries, no read-receipt write).
  A token that has expired or was evicted is simply re-created, so the worst
  case is one ordinary poll.
* **Short payload cache.** The inbox payload is cached per user and version
  for ``messaging.poll_cache_seconds`` (default 5 s), so several clients of
  the same person (tabs without BroadcastChannel, the phone app) share one
  computation.

The browser side (static/js/shared-poller.js) makes one tab per browser poll
for all the others.
"""
from __future__ import annotations

import uuid

from django.core.cache import cache
from django.db import transaction

TOKEN_TTL = 120          # seconds a version token lives without a bump
THREAD_KEY = "msg:tv:{}"
INBOX_KEY = "msg:iv:{}"
INBOX_PAYLOAD_KEY = "msg:inbox:{}:{}"
NOTIF_KEY = "msg:notif-unread:{}"


def poll_cache_seconds():
    try:
        from config.registry import get

        value = int(get("messaging.poll_cache_seconds", 5))
    except Exception:  # pragma: no cover - config table missing during migrations
        value = 5
    return max(0, min(value, 60))


def _new_token():
    return uuid.uuid4().hex[:12]


def _current(key):
    token = cache.get(key)
    if token is None:
        token = _new_token()
        # add() keeps a token another request set in the meantime.
        if not cache.add(key, token, TOKEN_TTL):
            token = cache.get(key) or token
    return token


def thread_version(thread_id):
    return _current(THREAD_KEY.format(thread_id))


def inbox_version(user_id):
    return _current(INBOX_KEY.format(user_id))


def _bump_now(keys):
    if keys:
        cache.set_many({k: _new_token() for k in keys}, TOKEN_TTL)


def _on_commit(fn):
    """Run now AND again after commit. Now: the change is never hidden behind
    an old token (and tests inside one transaction see it). After commit: a
    poll that took the early token before the row was visible polls again."""
    fn()
    try:
        transaction.on_commit(fn)
    except Exception:  # pragma: no cover - outside a usable connection
        pass


def bump_thread(thread_id, *, inbox_user_ids=()):
    """The thread changed (and, optionally, these users' inbox rows)."""
    keys = [THREAD_KEY.format(thread_id)] + [INBOX_KEY.format(u) for u in inbox_user_ids]
    _on_commit(lambda: _bump_now(keys))


def bump_inbox(*user_ids):
    keys = [INBOX_KEY.format(u) for u in user_ids if u]
    _on_commit(lambda: _bump_now(keys))


def participant_user_ids(thread_id):
    from .models import ThreadParticipant

    return list(ThreadParticipant.objects.filter(thread_id=thread_id).values_list("user_id", flat=True))


def bump_thread_and_members(thread_id):
    bump_thread(thread_id, inbox_user_ids=participant_user_ids(thread_id))


# ---------------------------------------------------------------------------
# Inbox payload cache
# ---------------------------------------------------------------------------
def cached_inbox_payload(user, compute):
    """(version, payload): payload from the cache when this user's inbox has
    not changed in the last poll_cache_seconds, else compute() and store."""
    version = inbox_version(user.pk)
    ttl = poll_cache_seconds()
    key = INBOX_PAYLOAD_KEY.format(user.pk, version)
    if ttl:
        payload = cache.get(key)
        if payload is not None:
            return version, payload
    payload = compute()
    if ttl:
        cache.set(key, payload, ttl)
    return version, payload


# ---------------------------------------------------------------------------
# Notification badge (unread count) - 5 s cache, dropped on every change
# ---------------------------------------------------------------------------
def notification_unread(user):
    from notifications.models import Notification

    ttl = poll_cache_seconds()
    key = NOTIF_KEY.format(user.pk)
    if ttl:
        value = cache.get(key)
        if value is not None:
            return value
    value = Notification.objects.filter(recipient=user, is_read=False).count()
    if ttl:
        cache.set(key, value, ttl)
    return value


def forget_notification_unread(*user_ids):
    keys = [NOTIF_KEY.format(u) for u in user_ids if u]
    if keys:
        _on_commit(lambda: cache.delete_many(keys))
