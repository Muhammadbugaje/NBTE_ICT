"""Add to calendar: inspection visits as .ics on both doors (core/calendar_views.py)."""
import pytest
from django.test import Client
from django.urls import reverse


@pytest.mark.django_db
def test_visit_ics_for_staff_who_see_the_visit_404_for_outsiders(p5_visit, make_user):
    insp = p5_visit.inspection
    lead = p5_visit.people.team_lead
    url = reverse("nbte_cal:inspection", args=[insp.pk])
    c = Client()
    c.force_login(lead)
    r = c.get(url)
    assert r.status_code == 200 and r["Content-Type"].startswith("text/calendar")
    body = r.content.decode()
    assert "BEGIN:VEVENT" in body and "BEGIN:VALARM" in body
    assert f"DTSTART;VALUE=DATE:{insp.scheduled_start:%Y%m%d}" in body
    # The detail page and the calendar page offer the button.
    page = c.get(reverse("qa_insp:inspection", args=[insp.pk])).content.decode()
    assert url in page and "calendar.google.com" in page
    assert url in c.get(reverse("qa_insp:calendar") + f"?month={insp.scheduled_start:%Y-%m}").content.decode()

    outsider = make_user("nobody@nbte.local")
    o = Client()
    o.force_login(outsider)
    assert o.get(url).status_code == 404


@pytest.mark.django_db
def test_portal_visit_ics_only_for_the_institution(p5_visit, make_user, make_institution, portal_host):
    insp = p5_visit.inspection
    mine = make_user("registrar@p5.local", user_type="institution", institution=p5_visit.inst, role="inst-admin")
    other_inst = make_institution("ZZZ", "dpap", "Zulu Polytechnic")
    theirs = make_user("registrar@zzz.local", user_type="institution", institution=other_inst, role="inst-admin")
    from django.conf import settings

    path = reverse("qa_portal:cal_inspection", args=[insp.pk], urlconf=settings.QA_PORTAL_URLCONF)
    c = Client(HTTP_HOST=portal_host)
    c.force_login(mine)
    r = c.get(path)
    assert r.status_code == 200 and r["Content-Type"].startswith("text/calendar")
    body = r.content.decode()
    assert "BEGIN:VEVENT" in body and "BEGIN:VALARM" in body
    assert "Team lead" not in body  # internal names stay internal
    o = Client(HTTP_HOST=portal_host)
    o.force_login(theirs)
    assert o.get(path).status_code == 404
