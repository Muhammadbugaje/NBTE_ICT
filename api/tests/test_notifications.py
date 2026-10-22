"""notifications/ on both doors."""
import pytest

from .conftest import BASE, bearer, err

pytestmark = pytest.mark.django_db


def _n(user, title="Hello", **kw):
    from notifications.models import Notification

    return Notification.objects.create(recipient=user, title=title, body=kw.pop("body", "b"), **kw)


def test_list_shape_filters_and_targets(staff_api, staff_user, make_user):
    a = _n(staff_user, "Task", url="/tasks/418/", category="task")
    b = _n(staff_user, "Memo", url="/messages/memos/7/", category="request", is_read=True)
    c = _n(staff_user, "Ann", url="/announcements/3/", category="system", metadata={"announcement": 3})
    d = _n(staff_user, "Odd", url="/settings/backups/")
    _n(make_user("x@t.local"), "Not mine")
    bearer(staff_api, staff_user)
    r = staff_api.get(BASE + "notifications/")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"results", "next", "previous", "count"} and body["count"] == 4
    items = {i["id"]: i for i in body["results"]}
    assert set(items[a.pk]) >= {"id", "title", "body", "level", "kind", "created_at", "read", "target"}
    assert items[a.pk]["target"]["type"] == "task" and items[a.pk]["target"]["route"] == "/tasks/418"
    assert items[a.pk]["kind"] == "tasks_tickets"
    assert items[b.pk]["target"] == {"type": "memo", "id": 7, "route": "/memos/7",
                                     "url": "http://localhost/messages/memos/7/"}
    assert items[c.pk]["kind"] == "announcements" and items[c.pk]["target"]["type"] == "announcement"
    assert items[d.pk]["target"]["type"] == "web" and items[d.pk]["target"]["route"] is None

    assert staff_api.get(BASE + "notifications/?filter=unread").json()["count"] == 3
    assert staff_api.get(BASE + "notifications/?filter=read").json()["count"] == 1
    ids = [i["id"] for i in staff_api.get(BASE + "notifications/?kind=announcements").json()["results"]]
    assert ids == [c.pk]
    ids = {i["id"] for i in staff_api.get(BASE + "notifications/?kind=general").json()["results"]}
    assert ids == {d.pk}
    assert staff_api.get(BASE + "notifications/?page_size=2").json()["next"]
    r = staff_api.get(BASE + "notifications/?filter=bogus")
    assert r.status_code == 400 and "filter" in err(r)["fields"]


def test_target_mapping_portal_links():
    from types import SimpleNamespace

    from api.services.notifications import target_for

    def t(url):
        return target_for(SimpleNamespace(url=url, metadata={}))

    assert t("/qa-requests/4/ssq/9/")["type"] == "ssq" and t("/qa-requests/4/ssq/9/")["id"] == 9
    assert t("/qa-requests/4/")["route"] == "/qa-requests/4"
    assert t("/payments/12/")["route"] == "/bills/12"
    assert t("/application/")["route"] == "/application"
    assert t("/qa/cases/5/")["type"] == "case"
    assert t("/workflow/8/")["route"] == "/approvals/8"
    assert t("/messages/5/")["route"] == "/chats/5"
    assert t("") is None
    assert t("https://example.org/x")["type"] == "web"


def test_read_read_all_unread_count(staff_api, staff_user, make_user):
    n1, n2 = _n(staff_user), _n(staff_user)
    theirs = _n(make_user("y@t.local"))
    bearer(staff_api, staff_user)
    assert staff_api.get(BASE + "notifications/unread-count/").json() == {"unread": 2}
    r = staff_api.post(BASE + f"notifications/{n1.pk}/read/")
    assert r.status_code == 200 and r.json() == {"id": n1.pk, "read": True, "unread": 1}
    r = staff_api.post(BASE + f"notifications/{n1.pk}/unread/")
    assert r.json()["unread"] == 2
    r = staff_api.post(BASE + f"notifications/{theirs.pk}/read/")
    assert r.status_code == 404 and err(r)["code"] == "not_found"
    theirs.refresh_from_db()
    assert theirs.is_read is False
    r = staff_api.post(BASE + "notifications/read-all/")
    assert r.json() == {"marked": 2, "unread": 0}
    n2.refresh_from_db()
    assert n2.is_read


def test_bulk(staff_api, staff_user, make_user):
    from notifications.models import Notification

    mine = [_n(staff_user) for _ in range(3)]
    theirs = _n(make_user("z@t.local"))
    bearer(staff_api, staff_user)
    ids = [mine[0].pk, mine[1].pk, theirs.pk]
    r = staff_api.post(BASE + "notifications/bulk/", {"ids": ids, "action": "mark_read"}, format="json")
    assert r.status_code == 200
    body = r.json()
    assert body["done"] == [mine[0].pk, mine[1].pk] and body["skipped"][0]["id"] == theirs.pk
    assert body["unread"] == 1 and body["message"] == "2 notifications marked as read."
    r = staff_api.post(BASE + "notifications/bulk/", {"ids": ids, "action": "delete"}, format="json")
    assert r.json()["done"] == [mine[0].pk, mine[1].pk]
    assert Notification.objects.filter(pk=theirs.pk).exists()
    assert not Notification.objects.filter(pk=mine[0].pk).exists()
    r = staff_api.post(BASE + "notifications/bulk/", {"ids": [], "action": "explode"}, format="json")
    assert r.status_code == 400 and {"ids", "action"} <= set(err(r)["fields"])


def test_portal_door(portal_api, institution_user, staff_api):
    n = _n(institution_user, "Bill", url="/payments/3/", category="qa")
    bearer(portal_api, institution_user)
    body = portal_api.get(BASE + "notifications/").json()
    assert body["results"][0]["id"] == n.pk and body["results"][0]["target"]["type"] == "bill"
    assert body["results"][0]["target"]["url"].startswith("http://qa.")
    assert portal_api.post(BASE + f"notifications/{n.pk}/read/").status_code == 200
    # staff token on the portal door / no token
    r = bearer(staff_api, institution_user).get(BASE + "notifications/")
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"


def test_auth_rules(staff_api, make_user):
    assert staff_api.get(BASE + "notifications/").status_code == 401
    temp = make_user("t@t.local", must_change=True)
    r = bearer(staff_api, temp).get(BASE + "notifications/unread-count/")
    assert r.status_code == 403 and err(r)["code"] == "password_change_required"


def test_website_bulk_still_works(client, staff_user):
    from .conftest import PASSWORD

    a, b = _n(staff_user), _n(staff_user)
    client.login(username=staff_user.email, password=PASSWORD)
    r = client.post("/notifications/bulk/", {"ids": f"{a.pk},{b.pk}", "action": "mark_read"},
                    HTTP_ACCEPT="application/json")
    assert r.status_code == 200 and r.json()["done"] == [a.pk, b.pk]
