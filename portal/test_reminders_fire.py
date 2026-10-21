"""Reminders fire once (Issue 5).

Run:  pytest portal/test_reminders_fire.py -q
"""
import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from staff.models import Department

pytestmark = pytest.mark.django_db


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def fin():
    return Department.objects.get_or_create(code="fin", defaults={"name": "Finance"})[0]


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


def drop_perm(role, codename):
    role.permissions.remove(Permission.objects.get(codename=codename))

from datetime import timedelta

from notifications.models import Notification
from portal.models import Reminder
from portal.services import fire_due_reminders


@pytest.fixture
def owner(ict, roles):
    return mk("own@x.org", ict, roles["staff"])


def mkrem(owner, when, **kw):
    return Reminder.objects.create(title="Call vendor", creator=owner, remind_at=when, **kw)


def notes(user):
    return Notification.objects.filter(recipient=user, title="Call vendor")


def test_due_reminder_fires_exactly_once(owner):
    now = timezone.now()
    r = mkrem(owner, now - timedelta(minutes=1))
    assert fire_due_reminders(now=now) == 1
    assert notes(owner).count() == 1
    n = notes(owner).first()
    assert n.url == reverse("portal:reminder_detail", kwargs={"pk": r.pk})
    assert not n.is_read if hasattr(n, "is_read") else True
    assert fire_due_reminders(now=now) == 0          # second run does nothing
    assert notes(owner).count() == 1


def test_future_reminder_does_not_fire(owner):
    now = timezone.now()
    mkrem(owner, now + timedelta(hours=2))
    assert fire_due_reminders(now=now) == 0
    assert notes(owner).count() == 0
    assert fire_due_reminders(now=now + timedelta(hours=3)) == 1     # fires once due


def test_dismissed_and_expired_do_not_fire(owner):
    now = timezone.now()
    mkrem(owner, now - timedelta(minutes=5), is_dismissed=True)
    mkrem(owner, now - timedelta(hours=2), expires_at=now - timedelta(hours=1))
    assert fire_due_reminders(now=now) == 0


def test_soft_deleted_does_not_fire(owner):
    now = timezone.now()
    r = mkrem(owner, now - timedelta(minutes=5))
    r.delete()
    assert fire_due_reminders(now=now) == 0


def test_personal_reminder_never_reaches_someone_else(owner, ict, roles):
    other = mk("oth@x.org", ict, roles["staff"])
    mkrem(owner, timezone.now() - timedelta(minutes=1))
    fire_due_reminders()
    assert notes(owner).count() == 1 and notes(other).count() == 0


def test_department_reminder_reaches_dept_and_link_opens(client, owner, ict, fin, roles):
    mate = mk("mate@x.org", ict, roles["staff"])
    stranger = mk("str@x.org", fin, roles["staff"])
    r = mkrem(owner, timezone.now() - timedelta(minutes=1),
              kind=Reminder.Kind.DEPARTMENT, department=ict)
    fire_due_reminders()
    assert notes(mate).count() == 1 and notes(stranger).count() == 0
    client.force_login(mate)
    assert client.get(reverse("portal:reminder_detail", args=[r.pk])).status_code == 200
    client.force_login(stranger)
    assert client.get(reverse("portal:reminder_detail", args=[r.pk])).status_code == 404


def test_fire_reminders_command_with_fake_now(owner):
    from django.core.management import call_command
    when = timezone.now() + timedelta(days=1)
    mkrem(owner, when)
    call_command("fire_reminders", verbosity=0)
    assert notes(owner).count() == 0
    local = timezone.localtime(when + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    call_command("fire_reminders", "--now", local, verbosity=0)
    assert notes(owner).count() == 1
