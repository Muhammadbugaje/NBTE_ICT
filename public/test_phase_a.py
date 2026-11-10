"""v2.1 Phase A: hero from settings (A8), history (A1, A2), leadership (A3, A4, T-CONS-4)."""
import datetime
import io

import pytest
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from accounts.models import Role, User
from config.models import ConfigEntry
from config.registry import invalidate
from public.models import HistoryMilestone
from staff.models import Department, ExecutivePosition, ExecutiveTenure, Staff

pytestmark = pytest.mark.django_db


def _set(key, value, vtype="str"):
    e, _ = ConfigEntry.objects.get_or_create(key=key, scope="global", defaults={"value_type": vtype, "value": value})
    e.value, e.value_type = value, vtype
    e.save()
    invalidate(key)


def _admin(email="history.admin@demo.nbte.local"):
    role = Role.objects.create(code=email[:40], name=email)
    role.permissions.add(Permission.objects.get(codename="manage_history"))
    u = User.objects.create_user(email, "Pass-word-123", role=role, must_change_password=False)
    c = Client()
    c.force_login(u)
    return c


def test_a8_hero_from_settings(client):
    html = client.get("/").content.decode()
    assert "One workspace to" in html and "hero-crest" in html and "scene--laminar" in html
    _set("home.hero.title", "Standards that build")
    _set("home.hero.ticker", "[]", "json")
    _set("home.hero.title_accent", "a skilled Nigeria")
    _set("home.hero.lede", "Lede for {organisation}.")
    _set("home.hero.announcement", "Re-accreditation closes on 30 November")
    _set("home.hero.note_badge", "2026 cycle")
    _set("home.hero.scene", "classic")
    _set("home.hero.crest", "false", "bool")
    _set("home.hero.stats", '[{"value": 479, "label": "Programmes"}, {"value": 1305, "suffix": "+", "label": "NSQ"}]', "json")
    html = client.get("/").content.decode()
    assert "Standards that build" in html and "a skilled Nigeria" in html
    assert "Lede for National Board for Technical Education." in html
    assert "2026 cycle" in html and "Re-accreditation closes" in html
    assert 'data-countup="1305"' in html and 'data-countup-suffix="+"' in html and "1,305+" in html
    assert "scene--laminar" not in html and 'data-scene="classic"' in html
    assert 'class="hero-crest"' not in html


def test_a8_hero_blank_falls_back_and_escapes(client):
    _set("home.hero.title", "")
    _set("home.hero.lede", "<script>alert(1)</script>")
    html = client.get("/").content.decode()
    assert "One workspace to" in html
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_a8_portal_hero_from_settings():
    _set("portal.home.hero.title", "Accreditation for")
    _set("portal.home.hero.title_accent", "every college.")
    c = Client(HTTP_HOST="qa.localhost")
    html = c.get("/").content.decode()
    assert "Accreditation for" in html and "every college." in html and "hero-crest" in html


def test_a1_history_crud_and_render(client):
    call_command("seed_nbte", "--only", "public.history", stdout=io.StringIO())
    assert HistoryMilestone.objects.count() == 6
    # off by default: no landing section, /history/ hidden from visitors
    assert 'id="history"' not in client.get("/").content.decode()
    assert client.get("/history/").status_code == 404
    admin = _admin()
    assert admin.get("/history/").status_code == 200          # administrators preview it
    assert admin.get(reverse("config:history")).status_code == 200
    # switch on, create, edit, reorder, delete — all HTMX fragments
    r = admin.post(reverse("config:history_settings_save"),
                   {"enabled": "1", "title": "Our story", "intro": "Intro"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and b"history-admin" not in r.content[:0]
    r = admin.post(reverse("config:history_create"),
                   {"year": "2030", "title": "Future step", "body": "Body", "led_by": "", "is_published": "on"},
                   HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and b"Future step" in r.content
    m = HistoryMilestone.objects.get(title="Future step")
    assert m.order > HistoryMilestone.objects.exclude(pk=m.pk).order_by("-order").first().order
    admin.post(reverse("config:history_move", args=[m.pk, "up"]), HTTP_HX_REQUEST="true")
    ordered = list(HistoryMilestone.objects.values_list("title", flat=True))
    assert ordered.index("Future step") == len(ordered) - 2
    r = admin.post(reverse("config:history_edit", args=[m.pk]),
                   {"year": "2031", "title": "Future step", "body": "New", "is_published": "on"}, HTTP_HX_REQUEST="true")
    m.refresh_from_db()
    assert m.year == "2031" and m.body == "New"
    html = client.get("/").content.decode()
    assert 'id="history"' in html and "Our story" in html and "2031" in html and "to confirm" in html
    assert client.get("/history/").status_code == 200
    admin.post(reverse("config:history_delete", args=[m.pk]), HTTP_HX_REQUEST="true")
    assert not HistoryMilestone.objects.filter(pk=m.pk).exists()
    # invalid input re-renders the form with an error, nothing saved
    r = admin.post(reverse("config:history_create"), {"year": "", "title": ""}, HTTP_HX_REQUEST="true")
    assert r.status_code == 422


def test_a2_history_settings_need_permission():
    u = User.objects.create_user("nobody@demo.nbte.local", "Pass-word-123", must_change_password=False)
    c = Client()
    c.force_login(u)
    assert c.get(reverse("config:history")).status_code == 403
    assert c.post(reverse("config:history_create"), {"year": "1", "title": "x"}).status_code == 403


def test_a1_history_seed_is_idempotent():
    out1, out2 = io.StringIO(), io.StringIO()
    call_command("seed_nbte", "--only", "public.history", stdout=out1)
    call_command("seed_nbte", "--only", "public.history", stdout=out2)
    assert "No changes" in out2.getvalue()
    assert HistoryMilestone.objects.filter(is_placeholder=True).count() == 6


def _position(name, code, order):
    return ExecutivePosition.objects.create(name=name, code=code, scope="federal", order=order)


def test_a4_tenure_without_staff():
    es = _position("Executive Secretary", "executive-secretary", 1)
    t = ExecutiveTenure(position=es, display_name="", started_on=datetime.date(1990, 1, 1),
                        ended_on=datetime.date(1995, 1, 1))
    with pytest.raises(Exception):
        t.full_clean()
    t.display_name, t.title, t.is_placeholder = "Ada Past", "Dr.", True
    t.full_clean()
    t.save()
    assert t.person_name == "Dr. Ada Past" and t.person_initials == "AP" and t.period_label == "1990–1995"
    # deleting a linked staff record keeps the tenure (SET_NULL)
    dept = Department.objects.create(name="ES Office A4", code="es-a4")
    s = Staff.objects.create(employee_no="A4-1", first_name="Bola", last_name="Now", department=dept)
    t2 = ExecutiveTenure.objects.create(position=es, staff=s, started_on=datetime.date(2020, 1, 1))
    s.delete() if not hasattr(s, "hard_delete") else Staff.objects.filter(pk=s.pk).delete()
    assert ExecutiveTenure.objects.filter(pk=t2.pk).exists()


def test_a3_leadership_order(client):
    es = _position("Executive Secretary", "executive-secretary", 1)
    dir_ = _position("Director ICT", "director-ict", 5)
    dept = Department.objects.create(name="ICT A3", code="ict-a3")
    cur_dir = Staff.objects.create(employee_no="A3-1", first_name="Dee", last_name="Rector", department=dept)
    cur_es = Staff.objects.create(employee_no="A3-2", first_name="Esse", last_name="Current", department=dept)
    ExecutiveTenure.objects.create(position=dir_, staff=cur_dir, started_on=datetime.date(2022, 1, 1))
    ExecutiveTenure.objects.create(position=es, staff=cur_es, started_on=datetime.date(2021, 1, 1))
    ExecutiveTenure.objects.create(position=es, display_name="Older Past", started_on=datetime.date(1980, 1, 1),
                                   ended_on=datetime.date(1986, 1, 1))
    ExecutiveTenure.objects.create(position=es, display_name="Newer Past", started_on=datetime.date(2010, 1, 1),
                                   ended_on=datetime.date(2016, 1, 1))
    ExecutiveTenure.objects.create(position=dir_, display_name="Old Director", started_on=datetime.date(2000, 1, 1),
                                   ended_on=datetime.date(2005, 1, 1))
    html = client.get("/leadership/").content.decode()
    # current leaders first (ES before directors), then previous ESs newest first, then other offices
    order = [html.index(x) for x in ("Esse Current", "Dee Rector", "Previous Executive Secretaries",
                                     "Newer Past", "Older Past", "Previous holders: Director ICT", "Old Director")]
    assert order == sorted(order)
    # the landing strip lists previous ESs too
    assert "Newer Past" in client.get("/").content.decode()


def test_t_cons_4_old_leadership_url_redirects(client):
    r = client.get("/staff/leadership/")
    assert r.status_code == 302 and r["Location"] == "/leadership/"
