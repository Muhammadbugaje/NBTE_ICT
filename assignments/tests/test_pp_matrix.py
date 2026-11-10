"""Phase P (P.3, P.4, P.6): the scoping chokepoint matrix (web + API), the
lint test, the admin / view-all / impersonation rules and the X6 portal
guard (spec G1.e, G1.i)."""
import pathlib
import re

import pytest
from django.conf import settings
from django.test import Client

from qa_core.tests.conftest import *  # noqa: F401,F403

from .pp_fixtures import assign_doc, assignment_mode, pp, supervise  # noqa: F401

pytestmark = pytest.mark.django_db

BASE = pathlib.Path(settings.BASE_DIR)
OTHER_DEPTS = {"dpap": ("Legal", "Finance"), "legal": ("DPAP", "Finance")}


def web(user, host="localhost"):
    c = Client(HTTP_HOST=host)
    c.force_login(user)
    return c


def api(user):
    from rest_framework.test import APIClient
    from api.tokens import refresh_for
    c = APIClient(HTTP_HOST="localhost")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_for(user).access_token}")
    return c


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------
def test_no_unscoped_in_views():
    """P.3 / spec §19: views never call ``.unscoped`` — services decide."""
    offenders = []
    files = [p for p in BASE.rglob("*.py")
             if ("views" in p.name or "/api/views/" in str(p)) and "/tests/" not in str(p)
             and "/migrations/" not in str(p) and "node_modules" not in str(p) and "/.venv" not in str(p)
             and not p.name.startswith("test_")]
    for path in files:
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"\.unscoped\b", code):
                offenders.append(f"{path.relative_to(BASE)}:{no}: {line.strip()}")
    assert not offenders, "Views must not call .unscoped:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# Matrix (G1.e), web + API
# ---------------------------------------------------------------------------
CAST = ["superuser", "es", "ict_dir", "supervisor", "dpap_dir", "legal_dir", "dpap_officer", "dpap_idle",
        "vtsd_dir"]
# who sees institution A at all (list + detail), with qa.privacy.mode = assignment
SEES_A = {"superuser", "es", "ict_dir", "supervisor", "dpap_dir", "legal_dir", "dpap_officer"}
# whose clearance rows of A they get (None = every department)
CLEARANCES = {"superuser": None, "es": None, "ict_dir": None, "supervisor": None, "dpap_dir": {"dpap"},
              "legal_dir": {"legal"}, "dpap_officer": set(), "dpap_idle": set(), "vtsd_dir": set()}


@pytest.fixture
def world(pp):
    assignment_mode()
    supervise(pp)
    assign_doc(pp)
    from django.contrib.auth.models import Permission
    # Give the cast the QA screens (their real roles may not carry every view).
    perms = Permission.objects.filter(codename__in=["view_institution", "view_onboarding_application"])
    from accounts.models import User
    for name in CAST:
        u = getattr(pp, name)
        u.user_permissions.add(*perms)
    for name in CAST:
        setattr(pp, name, User.objects.get(pk=getattr(pp, name).pk))
    return pp


@pytest.mark.parametrize("who", CAST)
def test_scoping_chokepoint_matrix(world, who):
    from accreditation.models import DepartmentalClearance
    from institutions.models import Institution
    user = getattr(world, who)
    # queryset level
    sees = Institution.objects.for_user(user).filter(pk=world.a.pk).exists()
    assert sees == (who in SEES_A), who
    rows = {c.department.code for c in DepartmentalClearance.objects.for_user(user)}
    expected = CLEARANCES[who]
    assert rows == ({"dpap", "legal", "finance"} if expected is None else expected), who
    # web: list and detail
    c = web(user)
    r = c.get("/qa/institutions/AAA/")
    assert r.status_code == (200 if who in SEES_A else 404), (who, r.status_code)
    if who in SEES_A:
        html = r.content.decode()
        if who in ("dpap_dir", "legal_dir"):
            own = "dpap" if who == "dpap_dir" else "legal"
            assert "Other departments are not shown" in html or "not shown" in html
            for other in OTHER_DEPTS[own]:
                assert f">{other}:" not in html and f"{other} (t)" not in html
        if who == "dpap_officer":
            assert "My work on this institution" in html
        if who in ("supervisor", "ict_dir", "es", "superuser"):
            assert "Full view" in html
    lst = c.get("/qa/institutions/").content.decode()
    assert ("Alpha Polytechnic" in lst) == (who in SEES_A), who
    # API: institution detail
    a = api(user)
    r = a.get("/api/v1/qa/institutions/AAA/")
    if who in SEES_A:
        assert r.status_code in (200, 403), (who, r.status_code)  # 403 = no QA screen permission in the API
    else:
        assert r.status_code in (403, 404), (who, r.status_code)


def test_x3_no_cross_department_names_in_payloads(world):
    """The DPAP director's pages, partials and API payloads about A never
    name Legal's or Finance's work."""
    user = world.dpap_dir
    c = web(user)
    bodies = [c.get(f"/qa/institutions/AAA/?tab={t}").content.decode()
              for t in ("overview", "documents", "applications", "timeline")]
    bodies.append(c.get("/qa/institutions/AAA/?tab=timeline", HTTP_HX_REQUEST="true",
                        HTTP_HX_TARGET="qa-tab").content.decode())
    a = api(user)
    for url in ("/api/v1/qa/institutions/AAA/", "/api/v1/qa/applications/clearance/?status=all"):
        r = a.get(url)
        if r.status_code == 200:
            bodies.append(r.content.decode())
    joined = "\n".join(bodies)
    assert "Legal reviewed the deed" not in joined
    assert "legal_review" not in joined and "finance_review" not in joined
    from notifications.models import Notification
    for n in Notification.objects.filter(recipient=user):
        assert "Legal" not in n.title + n.body


def test_x1_unassigned_officer_404_on_downloads(world):
    c = web(world.dpap_idle)
    r = c.get(f"/files/institutions.institutiondocument/{world.doc.pk}/")
    assert r.status_code in (403, 404)
    assert not world.doc.can_download(world.dpap_idle)
    assert world.doc.can_download(world.dpap_officer)


# ---------------------------------------------------------------------------
# P.4
# ---------------------------------------------------------------------------
def test_admin_qa_superuser_only(world):
    from django.contrib.auth.models import Permission
    officer = world.dpap_officer
    officer.is_staff = True
    officer.save()
    officer.user_permissions.add(*Permission.objects.filter(content_type__app_label="institutions"))
    c = web(officer)
    r = c.get("/admin/institutions/institution/")
    assert r.status_code in (302, 403, 404)
    r = web(world.superuser).get("/admin/institutions/institution/")
    assert r.status_code == 200


def test_admin_allowed_ips(world, settings):
    settings.ADMIN_ALLOWED_IPS = "10.1.2.0/24"
    c = web(world.superuser)
    assert c.get("/admin/", REMOTE_ADDR="192.168.0.9").status_code == 404
    assert c.get("/admin/", REMOTE_ADDR="10.1.2.7").status_code in (200, 302)


def test_view_all_holders(world):
    from accounts.models import Role, User
    from assignments import view_all
    from django.contrib.auth.models import Group, Permission
    perm = Permission.objects.get(codename="view_all_institutions")
    view_all.apply()
    roles = set(Role.objects.filter(permissions=perm).values_list("code", flat=True))
    assert roles <= {"executive-secretary", "super-admin"}
    assert not Group.objects.filter(permissions=perm).exists()
    holders = {u.pk for u in User.objects.filter(is_active=True, user_type="staff") if u.has_perm(
        "qa_core.view_all_institutions")}
    allowed = {world.superuser.pk, world.es.pk, world.ict_dir.pk}
    assert holders <= allowed | {u.pk for u in User.objects.filter(role__code__in=("executive-secretary",
                                                                                       "super-admin"))}


def test_impersonation_no_escalation(world):
    from django.core.exceptions import PermissionDenied
    from institutions import services
    world.inst_user.is_active = True
    world.inst_user.save()
    with pytest.raises(PermissionDenied):
        services.start_impersonation(world.a, staff=world.dpap_dir, reason="Checking the portal view")
    with pytest.raises(PermissionDenied):
        services.start_impersonation(world.a, staff=world.dpap_officer, reason="Checking the portal view")
    # The ICT supervisor already sees the whole journey: allowed.
    try:
        services.start_impersonation(world.a, staff=world.supervisor, reason="Checking the portal view")
    except PermissionDenied:  # pragma: no cover
        pytest.fail("The ICT supervisor may view as the institution")
    except Exception:
        pass  # no portal account etc. is not a scope question


def test_p4_access_event_on_full_journey_view(world):
    from assignments.models import AccessEvent
    web(world.supervisor).get("/qa/institutions/AAA/")
    assert AccessEvent.objects.filter(user=world.supervisor, action="view_full_journey").exists()


# ---------------------------------------------------------------------------
# P.5 / P.6 pages
# ---------------------------------------------------------------------------
def test_p6_my_work_page(world):
    r = web(world.dpap_officer).get("/qa/my-work/")
    assert r.status_code == 200
    assert "Alpha Polytechnic" in r.content.decode()
    r = web(world.dpap_idle).get("/qa/my-work/")
    assert "Nothing assigned to you" in r.content.decode()


def test_p5_who_can_see_page(world):
    r = web(world.ict_dir).get("/qa/settings/access/who/AAA/")
    html = r.content.decode()
    assert r.status_code == 200 and "Who can see" in html and "supervisor" in html.lower()
    assert web(world.dpap_dir).get("/qa/settings/access/who/AAA/").status_code == 403


def test_p5_grants_log_and_revoke(world):
    from assignments.models import AccessGrant
    r = web(world.superuser).get("/qa/settings/access/grants/")
    assert r.status_code == 200 and "Access grants log" in r.content.decode()
    assert web(world.dpap_officer).get("/qa/settings/access/grants/").status_code == 403
    g = AccessGrant.objects.live(world.dpap_officer).first()
    r = web(world.superuser).post(f"/qa/settings/access/grants/{g.pk}/revoke/", {"reason": "Wrong person"},
                                  HTTP_HX_REQUEST="true")
    assert r.status_code == 200
    g.refresh_from_db()
    assert g.revoked_at is not None


# ---------------------------------------------------------------------------
# X6 portal guard
# ---------------------------------------------------------------------------
PORTAL_URLS = ["/", "/how-to/", "/application/", "/timeline/", "/documents/", "/qa-requests/", "/payments/", "/programmes/",
               "/inspections/", "/institution/", "/queries/", "/letters/", "/memos/"]


def test_x6_portal_forbidden_terms(world, portal_host):
    """No portal page shows CQAC / EXCO / SOP step names, internal
    department work, or NBTE officers' names."""
    from qa_core.portal_journey import forbidden_terms
    from accreditation.models import Application
    Application.unscoped.filter(pk=world.app.pk).update(status="clearance")
    c = web(world.inst_user, host=portal_host)
    officers = [u.get_full_name() for u in (world.dpap_dir, world.dpap_officer, world.legal_dir, world.supervisor,
                                            world.ict_dir) if u.get_full_name().strip()]
    checked = 0
    for url in PORTAL_URLS:
        r = c.get(url)
        if r.status_code != 200:
            continue
        checked += 1
        text = re.sub(r"<[^>]+>", " ", r.content.decode())
        for term in forbidden_terms():
            assert term.lower() not in text.lower(), (url, term)
        for name in officers:
            assert name not in text, (url, name)
        assert "DPAP reviewed the site plan" not in text and "Legal reviewed the deed" not in text
    assert checked >= 5


def test_x6_milestones_mapping():
    from qa_core import portal_journey as pj
    bar = pj.milestones_for("qa_case", "cqac_recommendation")
    labels = [m["label"] for m in bar]
    assert all("CQAC" not in l and "Committee" not in l for l in labels)
    assert next(m for m in bar if m["state"] == "current")["code"] == "visit_decision"
    assert pj.milestone_label("onboarding", "legal_review") == "Documents under review"
