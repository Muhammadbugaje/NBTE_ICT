"""v2.1 Phase D (F1-F5) through the API: any leave type, days mode, the
per-type balance list and the leave type on application items."""
import datetime

import pytest

from .conftest import BASE, err
from .test_requests_leave import next_monday, types, worker_staff  # noqa: F401
from .work_fixtures import api_as, boss, work_seed, worker  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def leave_types():
    from core import seeding
    seeding.run(only=["hr.leave_types"], no_deps=True)


def test_f4_api_days_mode_apply_and_preview(worker, types, worker_staff, leave_types):
    w = api_as(worker)
    start = next_monday(weeks=5)
    p = w.get(BASE + f"leave/preview/?leave_type=casual&start_date={start}&days=3")
    assert p.status_code == 200, p.content
    assert p.json()["end_date"] == str(start + datetime.timedelta(days=2)) and p.json()["leave_type"] == "casual"
    r = w.get(BASE + f"leave/preview/?start_date={start}")
    assert r.status_code == 400
    r = w.post(BASE + "leave/applications/", {"leave_type": "casual", "start_date": str(start), "days": 4},
               format="json")
    assert r.status_code == 400 and "at most 3" in err(r)["message"]
    r = w.post(BASE + "leave/applications/", {"leave_type": "casual", "start_date": str(start), "days": 3},
               format="json")
    assert r.status_code == 201, r.content
    assert r.json()["leave_type"] == "casual" and r.json()["end_date"] == str(start + datetime.timedelta(days=2))
    overview = w.get(BASE + "leave/").json()
    codes = [b["leave_type"] for b in overview["balances"]]
    assert codes[0] == "annual-leave" and "casual" in codes
    casual = next(b for b in overview["balances"] if b["leave_type"] == "casual")
    assert casual["pending"] == "3" and casual["entitlement_source"] == "type"
