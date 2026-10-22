"""search/ (staff door): the website's global search, grouped for the app."""
import pytest

from .conftest import BASE, bearer, err
from .test_tasks import make_task
from .work_fixtures import api_as, boss, outsider, work_seed, worker  # noqa: F401

pytestmark = pytest.mark.django_db

URL = BASE + "search/"


def test_not_signed_in_wrong_door_and_portal_host(staff_api, portal_api, rp_user):
    r = staff_api.get(URL + "?q=router")
    assert r.status_code == 401 and err(r)["code"] == "not_authenticated"
    r = bearer(staff_api, rp_user).get(URL + "?q=router")
    assert r.status_code == 403 and err(r)["code"] == "wrong_door"
    assert bearer(portal_api, rp_user).get(URL + "?q=router").status_code == 404  # staff-only route


def test_password_change_required(work_seed, make_user):
    u = make_user("temp.pw@t.local", role="officer", dept="ict", must_change=True)
    r = api_as(u).get(URL + "?q=router")
    assert r.status_code == 403 and err(r)["code"] == "password_change_required"


@pytest.mark.parametrize("qs, field", [("", "q"), ("?q=a", "q"), ("?q=%20a%20", "q"),
                                       ("?q=router&limit=0", "limit"), ("?q=router&limit=11", "limit")])
def test_invalid_input(worker, qs, field):
    r = api_as(worker).get(URL + qs)
    assert r.status_code == 400 and err(r)["code"] == "validation_error" and field in err(r)["fields"]


def test_groups_shape_and_scoping(boss, worker, outsider):
    mine = make_task(boss, [worker], title="Fix the router in Block A")
    make_task(outsider, [], title="Router for finance (private)")
    r = api_as(worker).get(URL + "?q=router")
    assert r.status_code == 200, r.content
    body = r.json()
    assert set(body) == {"query", "total", "groups"} and body["query"] == "router"
    tasks = next(g for g in body["groups"] if g["kind"] == "tasks")
    assert tasks["label"] == "Tasks" and tasks["count"] == len(tasks["results"]) == 1
    item = tasks["results"][0]
    assert set(item) == {"id", "title", "subtitle", "badge", "target", "web_url"}
    assert item["id"] == str(mine.pk) and item["title"] == "Fix the router in Block A"
    assert item["target"]["type"] == "task" and item["target"]["route"] == f"/tasks/{mine.pk}"
    assert item["target"]["url"].endswith(f"/tasks/{mine.pk}/") and item["web_url"] == f"/tasks/{mine.pk}/"
    assert body["total"] == sum(g["count"] for g in body["groups"])
    assert all(g["count"] > 0 for g in body["groups"])  # empty groups are left out
    # the outsider's private task is not visible to the worker (website scoping)
    assert all("finance" not in res["title"] for g in body["groups"] for res in g["results"])


def test_limit_per_group(boss, worker):
    for i in range(4):
        make_task(boss, [worker], title=f"Printer jam {i}")
    body = api_as(worker).get(URL + "?q=printer&limit=2").json()
    tasks = next(g for g in body["groups"] if g["kind"] == "tasks")
    assert tasks["count"] == 2


def test_announcements_group(boss, worker):
    from announcements import services
    from announcements.models import Announcement

    ann = Announcement.objects.create(title="Quarterly townhall", body="All staff", created_by=boss)
    services.publish(ann, actor=boss)
    body = api_as(worker).get(URL + "?q=townhall").json()
    group = next(g for g in body["groups"] if g["kind"] == "announcements")
    assert group["results"][0]["target"] == {"type": "announcement", "id": ann.pk,
                                             "route": f"/announcements/{ann.pk}",
                                             "url": f"http://localhost/announcements/{ann.pk}/"}


def test_nothing_found(worker):
    body = api_as(worker).get(URL + "?q=zzqqxxnothing").json()
    assert body == {"query": "zzqqxxnothing", "total": 0, "groups": []}
