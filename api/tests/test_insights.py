"""insights/ (staff door)."""
import pytest

from .conftest import BASE, bearer, err

pytestmark = pytest.mark.django_db


def test_insights_periods(staff_api, staff_user):
    bearer(staff_api, staff_user)
    r = staff_api.get(BASE + "insights/")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["period"] == "month" and body["period_label"] == "Last 4 weeks"
    assert set(body) == {"period", "period_label", "arrivals", "tasks", "tickets"}
    assert len(body["arrivals"]["buckets"]) == 4 and len(body["tickets"]["buckets"]) == 4
    assert {s["key"] for s in body["tasks"]["states"]} == {"done", "in_progress", "waiting", "overdue"}
    week = staff_api.get(BASE + "insights/?period=week").json()
    assert len(week["arrivals"]["buckets"]) == 7
    assert len(staff_api.get(BASE + "insights/?period=quarter").json()["tickets"]["buckets"]) == 13
    r = staff_api.get(BASE + "insights/?period=year")
    assert r.status_code == 400 and "period" in err(r)["fields"]


def test_insights_matches_website_helpers(staff_api, staff_user):
    from dashboards import insights

    body = bearer(staff_api, staff_user).get(BASE + "insights/?period=week").json()
    assert body["tasks"]["total"] == insights.task_state_counts(staff_user, "week")["total"]
    assert body["arrivals"]["start"] == insights.arrival_stats(staff_user, "week")["start"]


def test_insights_doors(staff_api, portal_api, institution_user):
    assert staff_api.get(BASE + "insights/").status_code == 401
    assert bearer(portal_api, institution_user).get(BASE + "insights/").status_code == 404
