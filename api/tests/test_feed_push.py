"""notifications/feed/ (both doors) and FCM push sending (notifications/push.py)."""
import datetime
import json

import pytest
from django.core.cache import cache
from django.utils import timezone

from .conftest import BASE, bearer, err
from .work_fixtures import api_as, work_seed, worker  # noqa: F401

pytestmark = pytest.mark.django_db

URL = BASE + "notifications/feed/"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def _notify(user, title, category="task", url="/tasks/7/", **kw):
    from notifications.services import notify

    return notify(user, title=title, body=f"{title} body", url=url, category=category, **kw)


def _quiet_now(user):
    """Quiet hours that cover the present moment."""
    from notifications.preferences import save_preferences

    now = timezone.localtime(timezone.now(), timezone=datetime.timezone(datetime.timedelta(hours=1)))
    start = (now - datetime.timedelta(hours=1)).time().replace(second=0, microsecond=0)
    end = (now + datetime.timedelta(hours=1)).time().replace(second=0, microsecond=0)
    save_preferences(user, {"quiet_hours_enabled": True, "quiet_from": start, "quiet_to": end,
                            "timezone": "Africa/Lagos", "allow_urgent_during_quiet": True})


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------
def test_feed_auth_door_and_validation(staff_api, portal_api, worker, rp_user):
    r = staff_api.get(URL)
    assert r.status_code == 401 and err(r)["code"] == "not_authenticated"
    r = bearer(staff_api, rp_user).get(URL)
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"
    r = api_as(worker).get(URL + "?after=-1")
    assert r.status_code == 400 and "after" in err(r)["fields"]
    r = api_as(worker).get(URL + "?after=abc")
    assert r.status_code == 400 and "after" in err(r)["fields"]


def test_feed_password_change_required(work_seed, make_user):
    u = make_user("temp.pw@t.local", role="officer", dept="ict", must_change=True)
    r = api_as(u).get(URL)
    assert r.status_code == 403 and err(r)["code"] == "password_change_required"


def test_feed_first_run_gives_only_the_cursor(worker):
    a = _notify(worker, "One")
    b = _notify(worker, "Two")
    body = api_as(worker).get(URL).json()
    assert set(body) >= {"server_time", "latest_id", "quiet_now", "items"}
    assert body["latest_id"] == max(a.pk, b.pk) and body["items"] == [] and body["quiet_now"] is False


def test_feed_items_newest_first_own_only(worker, work_seed, make_user):
    other = make_user("other.user@t.local", role="officer", dept="ict")
    old = _notify(worker, "Old")
    _notify(other, "Not mine")
    n1 = _notify(worker, "Task assigned")
    n2 = _notify(worker, "Ticket updated", category="ticket", url="/tickets/3/")
    read = _notify(worker, "Already read")
    read.mark_read()
    body = api_as(worker).get(URL + f"?after={old.pk}").json()
    assert [i["id"] for i in body["items"]] == [n2.pk, n1.pk]
    assert body["latest_id"] == read.pk  # the cursor moves past read rows too
    item = body["items"][1]
    assert {"id", "category", "title", "body", "target", "created_at"} <= set(item)
    assert item["category"] == "task" and item["target"]["type"] == "task" and item["target"]["id"] == 7
    again = api_as(worker).get(URL + f"?after={body['latest_id']}").json()
    assert again["items"] == [] and again["latest_id"] == body["latest_id"]


def test_feed_limit_twenty(worker):
    for i in range(25):
        _notify(worker, f"N{i}")
    body = api_as(worker).get(URL + "?after=0").json()
    assert len(body["items"]) == 20 and body["items"][0]["title"] == "N24"


def test_feed_respects_kind_switches_and_quiet_hours(worker):
    from notifications.preferences import save_preferences

    save_preferences(worker, {"tasks_tickets": False})
    _notify(worker, "Task off")
    chat = _notify(worker, "Chat on", category="message", url="/messages/4/")
    body = api_as(worker).get(URL + "?after=0").json()
    assert [i["id"] for i in body["items"]] == [chat.pk]
    _quiet_now(worker)
    urgent = _notify(worker, "Server down", category="system", url="", level="error")
    _notify(worker, "Quiet chat", category="message", url="/messages/4/")
    body = api_as(worker).get(URL + "?after=0").json()
    assert body["quiet_now"] is True and [i["id"] for i in body["items"]] == [urgent.pk]
    assert body["items"][0]["target"] is None


def test_feed_is_cheap(worker, django_assert_max_num_queries):
    from api.services.notifications import feed

    for i in range(5):
        _notify(worker, f"N{i}")
    with django_assert_max_num_queries(2):
        out = feed(worker, after=0)
    assert len(out["items"]) == 5


def test_feed_portal_door(portal_api, rp_user):
    n = _notify(rp_user, "Engagement letter", category="qa", url="/engagements/")
    body = bearer(portal_api, rp_user).get(URL + "?after=0").json()
    assert [i["id"] for i in body["items"]] == [n.pk]


# ---------------------------------------------------------------------------
# Push (FCM HTTP v1) - transport mocked
# ---------------------------------------------------------------------------
@pytest.fixture
def fcm(tmp_path, settings, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    path = tmp_path / "sa.json"
    path.write_text(json.dumps({"type": "service_account", "project_id": "nbte-test", "private_key_id": "k1",
                                "private_key": pem, "client_email": "push@nbte-test.iam.gserviceaccount.com",
                                "token_uri": "https://oauth2.googleapis.com/token"}))
    settings.FCM_SERVICE_ACCOUNT_FILE = str(path)
    settings.CELERY_TASK_ALWAYS_EAGER = True
    calls = []
    replies = {}

    def fake_post(url, *, data=None, json_body=None, headers=None):
        calls.append({"url": url, "data": data, "json": json_body, "headers": headers})
        if url.endswith("/token"):
            return 200, {"access_token": "ya29.test", "expires_in": 3599}
        token = json_body["message"]["token"]
        return replies.get(token, (200, {"name": "projects/nbte-test/messages/1"}))

    monkeypatch.setattr("notifications.push._http_post", fake_post)
    return {"calls": calls, "replies": replies, "key": key}


def _device(user, token, n, revoked=False):
    from accounts.models import UserSession

    return UserSession.objects.create(user=user, session_key=f"app:{user.pk}:{n}", kind="app", platform="android",
                                      push_token=token, revoked_at=timezone.now() if revoked else None)


def test_push_off_without_configuration(worker, monkeypatch, django_capture_on_commit_callbacks, settings):
    from notifications import push

    settings.FCM_SERVICE_ACCOUNT_FILE = ""
    monkeypatch.delenv("FCM_SERVICE_ACCOUNT_FILE", raising=False)
    monkeypatch.setattr("notifications.push._http_post", lambda *a, **k: pytest.fail("no HTTP when disabled"))
    _device(worker, "tok-a", 1)
    with django_capture_on_commit_callbacks(execute=True):
        n = _notify(worker, "Hello")
    assert not push.enabled() and push.send_notification(n.pk) == 0


def test_push_sent_to_active_devices_and_dead_tokens_removed(worker, fcm, django_capture_on_commit_callbacks):
    import jwt

    from accounts.models import UserSession

    good = _device(worker, "tok-good", 1)
    dead = _device(worker, "tok-dead", 2)
    _device(worker, "tok-revoked", 3, revoked=True)
    _device(worker, "", 4)
    fcm["replies"]["tok-dead"] = (404, {"error": {"code": 404, "status": "NOT_FOUND", "details": [
        {"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": "UNREGISTERED"}]}})
    with django_capture_on_commit_callbacks(execute=True):
        n = _notify(worker, "Task assigned to you", url="/tasks/418/")
    token_calls = [c for c in fcm["calls"] if c["url"].endswith("/token")]
    sends = [c for c in fcm["calls"] if "messages:send" in c["url"]]
    assert len(token_calls) == 1
    assertion = token_calls[0]["data"]["assertion"]
    claims = jwt.decode(assertion, fcm["key"].public_key(), algorithms=["RS256"],
                        audience="https://oauth2.googleapis.com/token")
    assert claims["iss"] == "push@nbte-test.iam.gserviceaccount.com" and "firebase.messaging" in claims["scope"]
    assert sorted(c["json"]["message"]["token"] for c in sends) == ["tok-dead", "tok-good"]
    assert all(c["url"] == "https://fcm.googleapis.com/v1/projects/nbte-test/messages:send" for c in sends)
    assert all(c["headers"]["Authorization"] == "Bearer ya29.test" for c in sends)
    msg = sends[0]["json"]["message"]
    assert msg["notification"] == {"title": "Task assigned to you", "body": "Task assigned to you body"}
    assert msg["data"]["notification_id"] == str(n.pk) and msg["data"]["category"] == "task"
    target = json.loads(msg["data"]["target"])
    assert target["type"] == "task" and target["id"] == 418 and target["route"] == "/tasks/418"
    assert target["url"].startswith("http") and target["url"].endswith("/tasks/418/")
    assert all(isinstance(v, str) for v in msg["data"].values())  # FCM data values must be strings
    assert UserSession.objects.get(pk=dead.pk).push_token == ""
    assert UserSession.objects.get(pk=good.pk).push_token == "tok-good"
    # the access token is cached: a second notification needs no new token
    with django_capture_on_commit_callbacks(execute=True):
        _notify(worker, "Second")
    assert len([c for c in fcm["calls"] if c["url"].endswith("/token")]) == 1


def test_push_bulk_notifications(worker, work_seed, make_user, fcm, django_capture_on_commit_callbacks):
    from notifications.services import notify_many

    other = make_user("second.user@t.local", role="officer", dept="ict")
    _device(worker, "tok-1", 1)
    _device(other, "tok-2", 2)
    with django_capture_on_commit_callbacks(execute=True):
        notify_many([worker, other], title="Office closes at 2pm", category="system")
    assert sorted(c["json"]["message"]["token"] for c in fcm["calls"] if c["json"]) == ["tok-1", "tok-2"]


def test_push_respects_preferences(worker, fcm, django_capture_on_commit_callbacks):
    from notifications.preferences import save_preferences

    _device(worker, "tok-good", 1)
    save_preferences(worker, {"tasks_tickets": False, "show_previews": "never"})
    with django_capture_on_commit_callbacks(execute=True):
        _notify(worker, "Task off")
        _notify(worker, "Chat", category="message", url="/messages/9/")
    sends = [c for c in fcm["calls"] if c["json"]]
    assert len(sends) == 1 and sends[0]["json"]["message"]["data"]["category"] == "message"
    assert "Open the app" in sends[0]["json"]["message"]["notification"]["body"]  # previews off
    _quiet_now(worker)
    with django_capture_on_commit_callbacks(execute=True):
        _notify(worker, "Quiet chat", category="message", url="/messages/9/")
        _notify(worker, "Urgent", category="system", url="", level="error")
    sends = [c for c in fcm["calls"] if c["json"]]
    assert len(sends) == 2 and sends[-1]["json"]["message"]["notification"]["title"] == "Urgent"
