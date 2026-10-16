"""Regression tests for review findings M9 (dependencies), M10 (task
service gaps) and M11 (task-from-ticket scope)."""
from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from staff.models import Department

pytestmark = pytest.mark.django_db


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def roles():
    from django.core.management import call_command
    call_command("seed_roles", "--sync", verbosity=0)
    return {r.code: r for r in Role.objects.all()}


def mk(email, dept=None, role=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F",
                                 last_name="L", department=dept, role=role, **kw)
    u.must_change_password = False
    u.save()
    return u


def _task(ref, creator, dept=None, **kw):
    from tasks.models import Task
    return Task.objects.create(reference=ref, title=ref, created_by=creator,
                               owner_department=dept, **kw)


# ----------------------------------------------------------------- M9
def test_dependency_form_accepts_visible_task(ict, roles):
    from tasks.forms import DependencyForm
    u = mk("m9a@x.org", ict, roles["director"])
    t1 = _task("TSK-M9-1", u, ict)
    t2 = _task("TSK-M9-2", u, ict)
    form = DependencyForm({"depends_on": t2.pk}, task=t1, user=u)
    assert form.is_valid(), form.errors


def test_dependency_form_hides_other_departments_and_invisible_tasks(ict, roles):
    from tasks.forms import DependencyForm
    fin = Department.objects.create(name="Fin", code="fin-m9")
    u = mk("m9b@x.org", ict, roles["staff"])
    other = mk("m9c@x.org", fin, roles["staff"])
    mine = _task("TSK-M9-3", u, ict)
    foreign = _task("TSK-M9-4", other, fin)
    hidden_same_dept = _task("TSK-M9-5", other, ict)
    qs = DependencyForm(task=mine, user=u).fields["depends_on"].queryset
    assert foreign not in qs
    assert hidden_same_dept not in qs


def test_add_dependency_view_saves(client, ict, roles):
    from tasks.models import TaskDependency
    u = mk("m9d@x.org", ict, roles["director"])
    t1 = _task("TSK-M9-6", u, ict)
    t2 = _task("TSK-M9-7", u, ict)
    client.force_login(u)
    resp = client.post(reverse("tasks:add_dependency", args=[t1.pk]), {"depends_on": t2.pk})
    assert resp.status_code in (200, 302)
    assert TaskDependency.objects.filter(task=t1, depends_on=t2).exists()


# ----------------------------------------------------------------- M10
def test_tasks_views_defines_logger():
    import tasks.views
    assert hasattr(tasks.views, "logger")


def test_clone_keeps_subtask_parent(ict, roles):
    from tasks.models import Task
    from tasks.services import clone_task
    u = mk("m10a@x.org", ict, roles["director"])
    parent = _task("TSK-M10-1", u, ict)
    _task("TSK-M10-2", u, ict, parent=parent)
    new = clone_task(parent, u)
    assert Task.objects.filter(parent=new).count() == 1


def _assigned(ict, roles, email):
    from tasks.models import TaskAssignee
    u = mk(email, ict, roles["director"])
    t = _task("TSK-" + email.split("@")[0], u, ict)
    TaskAssignee.objects.create(task=t, user=u)
    return u, t


def test_log_time_rejects_non_assignee(ict, roles):
    from tasks.services import log_time
    _, t = _assigned(ict, roles, "m10b@x.org")
    stranger = mk("m10c@x.org", ict, roles["staff"])
    now = timezone.now()
    with pytest.raises(PermissionDenied):
        log_time(t, stranger, now - timedelta(hours=1), now)


def test_log_time_rejects_future_and_huge_entries(ict, roles):
    from tasks.services import log_time
    u, t = _assigned(ict, roles, "m10d@x.org")
    now = timezone.now()
    with pytest.raises(ValidationError):
        log_time(t, u, now, now + timedelta(hours=2))
    with pytest.raises(ValidationError):
        log_time(t, u, now - timedelta(hours=30), now)


def test_log_time_accepts_normal_entry(ict, roles):
    from tasks.services import log_time
    u, t = _assigned(ict, roles, "m10e@x.org")
    now = timezone.now()
    entry = log_time(t, u, now - timedelta(hours=2), now - timedelta(hours=1))
    assert entry.duration_seconds == 3600


# ----------------------------------------------------------------- M11
def test_create_from_ticket_requires_ticket_visibility(client, ict, roles):
    from tickets import services as ts
    from tickets.models import TicketCategory
    raiser = mk("m11a@x.org", ict, roles["staff"])
    cat = TicketCategory.objects.create(code="hw-m11", name="HW", handling_department=ict)
    ticket = ts.create_ticket(raised_by=raiser, category=cat, subject="secret printer", body="x")
    url = reverse("tasks:create_from_ticket", args=[ticket.pk])

    fin = Department.objects.create(name="Fin", code="fin-m11")
    client.force_login(mk("m11b@x.org", fin, roles["staff"]))
    assert client.get(url).status_code == 404

    client.force_login(raiser)
    resp = client.get(url)
    assert resp.status_code in (200, 403)  # may still lack task-create rights
    if resp.status_code == 200:
        assert b"secret printer" in resp.content
