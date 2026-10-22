"""Push notifications to the mobile app through Firebase Cloud Messaging
(FCM HTTP v1). Off unless configured - see PUSH_NOTIFICATIONS.md.

    enabled() -> bool
    queue(notifications)                   called by notifications.services
    send_notification(notification_id)     the Celery task body; returns pushes accepted

Enabled only when ``FCM_SERVICE_ACCOUNT_FILE`` (setting, or environment
variable) points to a Firebase service-account JSON file. Otherwise every
function is a no-op and nothing is imported or contacted.

Rules:
* every active app device of the recipient (``accounts.UserSession`` kind
  "app", not revoked, with a ``push_token``) gets one message;
* the recipient's preferences decide (``notifications.preferences
  .push_allowed``: the kind's switch, quiet hours, urgent break-through);
  ``show_previews = never`` sends a generic body instead of the text;
* a token FCM reports as UNREGISTERED (app uninstalled / token rotated) or
  invalid is removed from its device row.

Auth: a service-account JWT (RS256, signed with PyJWT + cryptography, both
already installed) is exchanged for an OAuth access token, cached until
shortly before it expires. HTTP goes through ``_http_post`` (requests) so
tests can replace the transport.
"""
from __future__ import annotations

import json
import logging
import os
import time

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
SEND_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
TOKEN_CACHE_KEY = "notifications:fcm:access_token"
GENERIC_BODY = "You have a new notification. Open the app to read it."
TIMEOUT = 10
# FCM errors that mean "this token will never work again".
DEAD_TOKEN_ERRORS = {"UNREGISTERED"}


class PushError(Exception):
    pass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def service_account_file():
    return (getattr(settings, "FCM_SERVICE_ACCOUNT_FILE", "") or os.environ.get("FCM_SERVICE_ACCOUNT_FILE", "")
            ).strip()


def enabled():
    return bool(service_account_file())


_ACCOUNT = {}


def service_account():
    """The parsed service-account JSON (cached per path + mtime)."""
    path = service_account_file()
    try:
        mtime = os.path.getmtime(path)
    except OSError as exc:
        raise PushError(f"FCM service account file not readable: {path}") from exc
    cached = _ACCOUNT.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for key in ("project_id", "client_email", "private_key"):
        if not data.get(key):
            raise PushError(f"FCM service account file has no {key}.")
    _ACCOUNT[path] = (mtime, data)
    return data


# ---------------------------------------------------------------------------
# Transport (replaced in tests)
# ---------------------------------------------------------------------------
def _http_post(url, *, data=None, json_body=None, headers=None):
    """POST and return (status_code, parsed JSON body or {})."""
    import requests

    response = requests.post(url, data=data, json=json_body, headers=headers or {}, timeout=TIMEOUT)
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body


# ---------------------------------------------------------------------------
# OAuth access token
# ---------------------------------------------------------------------------
def _signed_assertion(account, now=None):
    import jwt  # PyJWT; RS256 uses the installed cryptography package

    now = int(now or time.time())
    token_uri = account.get("token_uri") or DEFAULT_TOKEN_URI
    claims = {"iss": account["client_email"], "scope": SCOPE, "aud": token_uri, "iat": now, "exp": now + 3600}
    headers = {"kid": account["private_key_id"]} if account.get("private_key_id") else None
    return jwt.encode(claims, account["private_key"], algorithm="RS256", headers=headers)


def access_token():
    token = cache.get(TOKEN_CACHE_KEY)
    if token:
        return token
    account = service_account()
    status, body = _http_post(account.get("token_uri") or DEFAULT_TOKEN_URI, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": _signed_assertion(account)})
    if status != 200 or not body.get("access_token"):
        raise PushError(f"FCM auth failed ({status}): {body.get('error_description') or body.get('error') or ''}")
    ttl = max(60, int(body.get("expires_in") or 3600) - 120)
    cache.set(TOKEN_CACHE_KEY, body["access_token"], ttl)
    return body["access_token"]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
def _absolute(url, user):
    if not url or url.startswith(("http://", "https://")):
        return url
    if getattr(user, "user_type", "staff") != "staff":
        from qa_core.hosts import portal_absolute

        return portal_absolute(url)
    return (getattr(settings, "SITE_URL", "") or "").rstrip("/") + url


def build_message(token, notification, pref):
    from api.services.notifications import kind_of, target_for

    target = target_for(notification)
    if target and target.get("url"):
        target["url"] = _absolute(target["url"], notification.recipient)
    body = notification.body or ""
    if getattr(pref, "show_previews", "always") == "never":
        body = GENERIC_BODY
    android = {"priority": "high", "notification": {"tag": f"n{notification.pk}"}}
    if getattr(pref, "show_previews", "always") == "unlocked":
        android["notification"]["visibility"] = "PRIVATE"
    return {"message": {
        "token": token,
        "notification": {"title": notification.title[:200], "body": body[:500]},
        "data": {"notification_id": str(notification.pk), "category": notification.category,
                 "kind": kind_of(notification), "target": json.dumps(target) if target else ""},
        "android": android,
        "apns": {"payload": {"aps": {"sound": "default"}}},
    }}


def _error_code(body):
    """The FCM error code (UNREGISTERED, INVALID_ARGUMENT, ...) of a failed send."""
    err = (body or {}).get("error") or {}
    for detail in err.get("details") or []:
        if detail.get("errorCode"):
            return detail["errorCode"]
    return err.get("status") or ""


def _dead_token(status, body):
    code = _error_code(body)
    if code in DEAD_TOKEN_ERRORS or status == 404:
        return True
    message = str(((body or {}).get("error") or {}).get("message") or "").lower()
    return code == "INVALID_ARGUMENT" and "registration token" in message


def active_devices(user):
    from accounts.models import UserSession

    return (UserSession.objects.filter(user=user, kind=UserSession.APP, revoked_at__isnull=True)
            .exclude(push_token=""))


def send_notification(notification_id):
    """Push one in-app notification to every active app device of its
    recipient. Returns how many messages FCM accepted. Never raises."""
    if not enabled():
        return 0
    from accounts.sessions_service import set_push_token
    from api.services.notifications import kind_of

    from .models import Notification
    from .preferences import get_preferences, push_allowed

    n = Notification.objects.select_related("recipient").filter(pk=notification_id).first()
    if n is None or not n.recipient.is_active:
        return 0
    pref = get_preferences(n.recipient)
    if not push_allowed(pref, n, kind_of(n)):
        return 0
    devices = list(active_devices(n.recipient))
    if not devices:
        return 0
    try:
        project = service_account()["project_id"]
        token = access_token()
    except Exception:
        logger.exception("FCM push not sent for notification %s", notification_id)
        return 0
    sent = 0
    for device in devices:
        try:
            status, body = _http_post(SEND_URL.format(project=project), json_body=build_message(
                device.push_token, n, pref), headers={"Authorization": f"Bearer {token}"})
        except Exception:
            logger.exception("FCM push to device %s failed", device.pk)
            continue
        if status == 200:
            sent += 1
        elif _dead_token(status, body):
            set_push_token(device, "")
            logger.info("FCM token of device %s removed (%s)", device.pk, _error_code(body) or status)
        else:
            if status == 401:
                cache.delete(TOKEN_CACHE_KEY)
            logger.warning("FCM push to device %s refused (%s): %s", device.pk, status, _error_code(body))
    return sent


def queue(notifications):
    """Send pushes for freshly created notifications after the surrounding
    transaction commits (Celery task; eager in development)."""
    if not enabled():
        return
    from django.db import transaction

    ids = [n.pk for n in notifications if n is not None and getattr(n, "pk", None)]
    if not ids:
        return

    def _dispatch():
        from .tasks import send_push_task

        for pk in ids:
            try:
                send_push_task.delay(pk)
            except Exception:  # broker down: never break the action that notified
                logger.exception("Could not queue push for notification %s", pk)

    transaction.on_commit(_dispatch)
