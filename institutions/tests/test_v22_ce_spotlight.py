"""v2.2: the Chief Executive's set-up link goes only to the CE (never shown to
the Head of ICT); NBTE ES/super admin/Director of ICT/assigned ICT officer can
resend it, reveal it once or issue a one-time password; new institutions
appear boldly on the right dashboards; assigned staff see all users and
contacts."""
import pytest
from django.core import mail
from django.test import Client

from institutions.tests.test_x50_registration import _data

pytestmark = pytest.mark.django_db


@pytest.fixture
def registered(lookups, portal_host, django_capture_on_commit_callbacks):
    from institutions.models import Institution

    client = Client(HTTP_HOST=portal_host)
    with django_capture_on_commit_callbacks(execute=True):
        r = client.post("/register/", _data(lookups))
    assert r.status_code == 302
    return Institution.unscoped.get(name="Zonkwa Technical College"), client


def test_ce_link_is_emailed_only_to_the_ce_never_shown(registered, settings):
    inst, client = registered
    page = client.get("/setup/").content.decode()
    assert "password/set/" not in page                     # no link on screen for the Head of ICT
    assert "sets up their own account" in page or "Chief Executive" in page
    from qa_core.models import TimelineEvent

    sent = TimelineEvent.unscoped.filter(institution=inst, event_code="credentials.sent") if hasattr(TimelineEvent, "unscoped") else TimelineEvent.objects.filter(institution=inst, event_code="credentials.sent")
    assert any("ce@ztc.edu.ng" in (e.internal_text or "") for e in sent)
    assert "qa_ce_activation_link_once" not in client.session


def test_nbte_can_reveal_link_and_issue_password(registered, p2, make_user):
    from institutions import services

    inst, _client = registered
    es = make_user("v22.es@t.local", role="executive-secretary")
    officer = make_user("v22.dpap@t.local", role="officer", dept="dpap")
    assert services.can_manage_ce_access(es, inst)
    assert not services.can_manage_ce_access(officer, inst)
    out = services.nbte_send_ce_link(inst, actor=es, reveal=True)
    assert out["url"] and "password/set" in out["url"] and out["email"] == "ce@ztc.edu.ng"
    pw = services.nbte_issue_ce_password(inst, actor=es)
    ce = services.chief_executive_link(inst).user
    ce.refresh_from_db()
    assert ce.check_password(pw) and ce.must_change_password


def test_assigned_ict_officer_can_manage_ce(registered, make_user):
    from assignments import work
    from assignments.models import WorkAssignment
    from institutions import services
    from qa_core.departments import qa_department

    inst, _client = registered
    ict = qa_department("ict")
    officer = make_user("v22.ict@t.local", role="officer", dept="ict")
    assert not services.can_manage_ce_access(officer, inst)
    work.assign(inst, WorkAssignment.OVERSIGHT_ICT, officer, subject=inst, department=ict,
                assigned_by=None, role_on_work="supervisor")
    from django.core.cache import cache
    cache.clear()
    assert services.can_manage_ce_access(officer, inst)


def test_spotlight_for_es_and_handling_director_only(registered, make_user, p2):
    from qa_core import spotlight

    inst, _client = registered
    es = make_user("v22.es2@t.local", role="executive-secretary")
    data = spotlight.new_institutions(es)
    assert data["kind"] == "full" and any(r["inst"].pk == inst.pk for r in data["rows"])
    assert data["unassigned"] >= 1
    plain = make_user("v22.plain@t.local", role="officer", dept="dpap")
    assert spotlight.new_institutions(plain)["rows"] == []
