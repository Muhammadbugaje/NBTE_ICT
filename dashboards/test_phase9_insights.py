"""Phase 9: personal insights on "My workspace"."""
import datetime as dt

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from dashboards import insights

pytestmark = pytest.mark.django_db
TODAY = dt.date(2026, 10, 21)  # a Wednesday


@pytest.fixture
def user(db):
    return User.objects.create_user("insight@nbte.test", "Pass-word-123", must_change_password=False,
                                    first_name="In", last_name="Sight")


def _aware(day, hour, minute=0):
    return timezone.make_aware(dt.datetime.combine(day, dt.time(hour, minute)))


def test_period_ranges():
    start, end, buckets = insights.period_range("week", TODAY)
    assert start == TODAY - dt.timedelta(days=6) and end == TODAY and len(buckets) == 7
    start, end, buckets = insights.period_range("month", TODAY)
    assert len(buckets) == 4 and start.weekday() == 0 and buckets[-1]["end"] == TODAY
    _s, _e, buckets = insights.period_range("quarter", TODAY)
    assert len(buckets) == 13
    assert insights.clean_period("bogus") == "month"


def test_empty_user_has_no_data(user):
    a = insights.arrival_stats(user, "week", TODAY)
    assert a["average"] is None and a["days_recorded"] == 0 and len(a["buckets"]) == 7
    t = insights.task_state_counts(user, "week", TODAY)
    assert t["total"] == 0 and [s["key"] for s in t["states"]] == ["done", "in_progress", "waiting", "overdue"]
    k = insights.ticket_trend(user, "week", TODAY)
    assert k["raised_total"] == k["resolved_total"] == 0
    ctx = insights.chart_context(user, "week", TODAY)
    assert ctx["arrival_chart"] is None and ctx["task_chart"] is None and ctx["ticket_chart"] is None


def test_arrival_stats(user):
    from attendance.models import AttendanceRecord

    for offset, (h, m, status) in enumerate([(8, 0, "present"), (8, 30, "present"), (9, 30, "late")]):
        day = TODAY - dt.timedelta(days=offset)
        AttendanceRecord.objects.create(user=user, date=day, status=status, check_in_at=_aware(day, h, m))
    AttendanceRecord.objects.create(user=user, date=TODAY - dt.timedelta(days=3), status="absent")
    AttendanceRecord.objects.create(user=user, date=TODAY - dt.timedelta(days=30), status="present",
                                    check_in_at=_aware(TODAY - dt.timedelta(days=30), 6, 0))  # outside the week
    a = insights.arrival_stats(user, "week", TODAY)
    assert a["days_recorded"] == 3 and a["late_days"] == 1
    assert a["average"] == "08:40" and a["earliest"] == "08:00" and a["latest"] == "09:30"
    assert a["buckets"][-1]["average"] == "08:00" and a["buckets"][0]["average"] is None
    geo = insights._arrival_geometry(a)
    assert len(geo["bars"]) == 7 and geo["bars"][0]["empty"] and not geo["bars"][-1]["empty"]
    # earlier arrival -> taller bar
    assert geo["bars"][-1]["h"] > geo["bars"][-3]["h"]


def _task(user, ref, state, due=None, completed=None):
    from tasks.models import Task, TaskAssignee

    t = Task.objects.create(reference=ref, title=ref, created_by=user, state=state, due_date=due,
                            completed_at=completed)
    TaskAssignee.objects.create(task=t, user=user)
    return t


def test_task_state_counts(user, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *a, **k: TODAY)
    _task(user, "T-1", "completed", completed=_aware(TODAY - dt.timedelta(days=1), 10))
    _task(user, "T-2", "completed", completed=_aware(TODAY - dt.timedelta(days=60), 10))  # outside
    _task(user, "T-3", "in_progress", due=TODAY + dt.timedelta(days=2))
    _task(user, "T-4", "open")
    _task(user, "T-5", "awaiting_review")
    _task(user, "T-6", "in_progress", due=TODAY - dt.timedelta(days=1))  # overdue
    _task(user, "T-7", "cancelled")
    other = User.objects.create_user("x@nbte.test", "Pass-word-123")
    _task(other, "T-8", "in_progress")
    c = insights.task_state_counts(user, "month", TODAY)
    assert (c["done"], c["in_progress"], c["waiting"], c["overdue"], c["total"]) == (1, 2, 1, 1, 5)
    geo = insights._donut_geometry(c)
    assert geo["total"] == 5 and len(geo["segments"]) == 4


def test_ticket_trend(user):
    from staff.models import Department
    from tickets.models import Ticket, TicketCategory

    dept, _ = Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})
    cat = TicketCategory.objects.create(code="printer", name="Printer", handling_department=dept)

    def mk(ref, created, resolved=None, **kw):
        t = Ticket.objects.create(reference=ref, raised_by=kw.get("raised_by", user), handling_department=dept,
                                  category=cat, subject=ref, body="b", resolved_at=resolved,
                                  assigned_to=kw.get("assigned_to"))
        Ticket.objects.filter(pk=t.pk).update(created_at=created)
        return t
    mk("TK-1", _aware(TODAY, 9))
    mk("TK-2", _aware(TODAY - dt.timedelta(days=2), 9), resolved=_aware(TODAY, 11))
    someone = User.objects.create_user("s@nbte.test", "Pass-word-123")
    mk("TK-3", _aware(TODAY - dt.timedelta(days=1), 9), raised_by=someone, assigned_to=user,
       resolved=_aware(TODAY - dt.timedelta(days=1), 15))
    mk("TK-4", _aware(TODAY, 9), raised_by=someone)  # not mine
    k = insights.ticket_trend(user, "week", TODAY)
    assert k["raised_total"] == 3 and k["resolved_total"] == 2
    assert k["buckets"][-1] == {**k["buckets"][-1], "raised": 1, "resolved": 1}
    geo = insights._trend_geometry(k)
    assert len(geo["raised"]) == 7 and geo["raised_points"].count(",") == 7


def test_self_service_page_renders_insights(user):
    c = Client()
    c.force_login(user)
    for period in ("week", "month", "quarter", "nonsense"):
        r = c.get(reverse("dashboards:self_service") + f"?period={period}")
        html = r.content.decode()
        assert r.status_code == 200 and 'id="insights"' in html
        assert "No data yet" in html and "Arrival times" in html and "Tickets raised vs resolved" in html
    assert 'aria-current="true">Month' in c.get(reverse("dashboards:self_service")).content.decode()


def test_self_service_page_draws_charts(user):
    from attendance.models import AttendanceRecord

    day = timezone.localdate()
    AttendanceRecord.objects.create(user=user, date=day, status="present", check_in_at=_aware(day, 8, 5))
    c = Client()
    c.force_login(user)
    html = c.get(reverse("dashboards:self_service") + "?period=week").content.decode()
    assert 'class="p9-chart"' in html and "08:05" in html and '<table class="sr-only" data-no-wrap>' in html
