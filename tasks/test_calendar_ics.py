"""Add to calendar: a task's due date as .ics, and the personal feed (core/calendar_views.py)."""
import datetime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.calendar_views import feed_token
from tasks.models import Task, TaskAssignee


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _task(people, depts, due=True):
    t = Task.objects.create(reference="TSK-CAL-0001", title="Renew the firewall licence",
                            created_by=people["director"], owner_department=depts["ict"],
                            due_date=timezone.localdate() + datetime.timedelta(days=5) if due else None)
    TaskAssignee.objects.create(task=t, user=people["officer"])
    return t


@pytest.mark.django_db
def test_task_ics_for_people_involved_and_404_for_outsiders(people, depts):
    t = _task(people, depts)
    url = reverse("nbte_cal:task", args=[t.pk])
    r = _client(people["officer"]).get(url)
    assert r.status_code == 200 and r["Content-Type"].startswith("text/calendar")
    body = r.content.decode()
    assert "BEGIN:VEVENT" in body and "BEGIN:VALARM" in body
    assert f"DTSTART;VALUE=DATE:{t.due_date:%Y%m%d}" in body and "TSK-CAL-0001" in body
    # HR cannot see an ICT task: same 404 as the detail page.
    assert _client(people["hr_officer"]).get(url).status_code == 404
    assert _client(people["hr_officer"]).get(reverse("tasks:detail", args=[t.pk])).status_code == 404
    # Signed out: sent to sign in.
    assert Client().get(url).status_code == 302


@pytest.mark.django_db
def test_task_without_due_date_has_no_ics(people, depts):
    t = _task(people, depts, due=False)
    assert _client(people["officer"]).get(reverse("nbte_cal:task", args=[t.pk])).status_code == 404


@pytest.mark.django_db
def test_task_detail_shows_add_to_calendar(people, depts):
    t = _task(people, depts)
    page = _client(people["officer"]).get(reverse("tasks:detail", args=[t.pk])).content.decode()
    assert reverse("nbte_cal:task", args=[t.pk]) in page and "data-nbte-cal" in page


@pytest.mark.django_db
def test_personal_feed_lists_my_due_tasks_and_rejects_bad_tokens(people, depts):
    t = _task(people, depts)
    officer = people["officer"]
    r = Client().get(reverse("nbte_cal:feed", args=[feed_token(officer)]))
    assert r.status_code == 200 and r["Content-Type"].startswith("text/calendar")
    assert f"UID:task-{t.pk}@" in r.content.decode()
    # The HR officer's feed does not include it.
    other = Client().get(reverse("nbte_cal:feed", args=[feed_token(people["hr_officer"])])).content.decode()
    assert f"UID:task-{t.pk}@" not in other
    # Tampered token, or a token from before a password change: 404.
    assert Client().get(reverse("nbte_cal:feed", args=[f"{officer.pk}-deadbeef"])).status_code == 404
    old = feed_token(officer)
    officer.set_password("A-new-password-2026!")
    officer.save()
    assert Client().get(reverse("nbte_cal:feed", args=[old])).status_code == 404
