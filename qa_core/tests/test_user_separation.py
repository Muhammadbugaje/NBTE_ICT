"""Strict separation of NBTE staff and institution users.

* Act as user: the default list holds NBTE staff only; ?segment=institution
  holds institution users only; acting as an institution user goes to the
  QA portal (a view-as session bound to that account), never the internal
  system.
* User management: the NBTE / institution / portal lists are disjoint and
  each create form can only make its own kind of account.
* Roles: NBTE internal roles cannot be given to institution users.
* Data Hub: institution users are imported into their institution by code
  (unknown codes refused); NBTE accounts need a department; a row of the
  wrong kind is refused on each sheet.
"""
import io
import re

import pytest
from openpyxl import load_workbook

from core.datahub import engine

pytestmark = pytest.mark.django_db
DEFAULT_PW = "Imported-Default-2026!"


@pytest.fixture(autouse=True)
def _locmem_mail(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


@pytest.fixture
def world(qa_seed, make_user, make_institution):
    class W:
        pass
    w = W()
    w.su = make_user("root@nbte.gov.ng", superuser=True, dept="dpap")
    w.staff = make_user("officer.sep@nbte.gov.ng", role="officer", dept="dpap")
    w.inst = make_institution("SEP", name="Separation Polytechnic")
    w.ce = make_user("ce@sep.local", role="inst-chief-executive", user_type="institution", institution=w.inst)
    w.ict = make_user("ict@sep.local", role="inst-admin", user_type="institution", institution=w.inst)
    w.rp = make_user("rp@sep.local", role="resource-person", user_type="resource_person")
    return w


def _emails(html):
    return set(re.findall(r"[\w.+-]+@[\w.-]+\.(?:local|gov\.ng)", html))


# ---------------------------------------------------------------------------
# Act as user
# ---------------------------------------------------------------------------
def test_act_as_default_lists_only_nbte_staff(world, client_for):
    html = client_for(world.su).get("/impersonate/").content.decode()
    found = _emails(html)
    assert "officer.sep@nbte.gov.ng" in found
    assert not found & {"ce@sep.local", "ict@sep.local", "rp@sep.local"}
    assert "Show institution users" in html and 'class="qa-hero' in html


def test_act_as_institution_toggle_lists_only_institution_users(world, client_for):
    c = client_for(world.su)
    html = c.get("/impersonate/?segment=institution").content.decode()
    found = _emails(html)
    assert {"ce@sep.local", "ict@sep.local"} <= found
    assert not found & {"officer.sep@nbte.gov.ng", "rp@sep.local"}
    assert "Separation Polytechnic" in html                       # grouped by institution
    # Filters stay inside the segment: a department filter cannot pull staff in.
    html = c.get(f"/impersonate/?segment=institution&department={world.staff.department_id}").content.decode()
    assert "officer.sep@nbte.gov.ng" not in html
    # Institution filter narrows to that institution.
    html = c.get(f"/impersonate/?segment=institution&institution={world.inst.pk}&kind=").content.decode()
    assert "ce@sep.local" in html


def test_acting_as_institution_user_goes_to_the_portal(world, client_for):
    from core.impersonation import IMPERSONATE_SESSION_KEY
    from institutions.models import ImpersonationSession

    c = client_for(world.su)
    resp = c.post(f"/impersonate/{world.ce.pk}/start/")
    assert resp.status_code == 302 and "/impersonate/" in resp["Location"]
    session = ImpersonationSession.unscoped.get(view_as=world.ce)
    assert session.institution_id == world.inst.pk and session.staff_id == world.su.pk
    assert resp["Location"].endswith(f"/impersonate/{session.token}/")
    assert IMPERSONATE_SESSION_KEY not in c.session        # the internal system is not switched
    # Acting as NBTE staff still switches the internal session.
    resp = c.post(f"/impersonate/{world.staff.pk}/start/")
    assert resp.status_code == 302 and c.session[IMPERSONATE_SESSION_KEY] == world.staff.pk


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------
def test_user_management_segments_are_disjoint(world, client_for):
    c = client_for(world.su)
    me = {world.su.email}     # the viewer's own e-mail is in the top bar of every page
    nbte = _emails(c.get("/accounts/users/").content.decode()) - me
    inst = _emails(c.get("/accounts/users/institutions/").content.decode()) - me
    portal = _emails(c.get("/accounts/users/portal/").content.decode()) - me
    assert "officer.sep@nbte.gov.ng" in nbte and "ce@sep.local" in inst and "rp@sep.local" in portal
    assert not (nbte & inst) and not (nbte & portal) and not (inst & portal)
    # A type filter on the NBTE list cannot pull institution users in: it is
    # redirected to the institution section.
    resp = c.get("/accounts/users/?type=institution")
    assert resp.status_code == 302 and resp["Location"].startswith("/accounts/users/institutions/")


def test_create_from_nbte_section_makes_staff_and_needs_department(world, client_for):
    from accounts.models import Role, User

    c = client_for(world.su)
    base = {"first_name": "New", "last_name": "Staffer", "email": "new.staffer@nbte.gov.ng"}
    resp = c.post("/accounts/users/nbte/new/", base)
    assert resp.status_code == 200 and not User.objects.filter(email=base["email"]).exists()
    # An institution role is not even offered, and is refused.
    inst_role = Role.objects.get(code="inst-admin")
    resp = c.post("/accounts/users/nbte/new/", {**base, "department": world.staff.department_id,
                                                "role": inst_role.pk})
    assert resp.status_code == 200 and not User.objects.filter(email=base["email"]).exists()
    resp = c.post("/accounts/users/nbte/new/", {**base, "department": world.staff.department_id,
                                                "role": Role.objects.get(code="officer").pk})
    assert resp.status_code == 302
    user = User.objects.get(email=base["email"])
    assert user.user_type == "staff" and user.department_id == world.staff.department_id
    assert user.institution_id is None and user.role.code == "officer"


def test_create_from_institution_section_needs_institution_and_portal_role(world, client_for):
    from accounts.models import Role, User
    from institutions.models import InstitutionUser

    c = client_for(world.su)
    base = {"first_name": "Ada", "last_name": "Portal", "email": "ada@sep.local"}
    resp = c.post("/accounts/users/institutions/new/", {**base, "role": Role.objects.get(code="inst-admin").pk})
    assert resp.status_code == 200 and not User.objects.filter(email=base["email"]).exists()   # no institution
    resp = c.post("/accounts/users/institutions/new/", {**base, "institution": world.inst.pk,
                                                        "role": Role.objects.get(code="officer").pk})
    assert resp.status_code == 200 and not User.objects.filter(email=base["email"]).exists()   # NBTE role
    resp = c.post("/accounts/users/institutions/new/", {**base, "institution": world.inst.pk,
                                                        "role": Role.objects.get(code="inst-admin").pk})
    assert resp.status_code == 302, resp.content.decode()[:500]
    user = User.objects.get(email=base["email"])
    assert user.user_type == "institution" and user.institution_id == world.inst.pk and user.department_id is None
    assert InstitutionUser.unscoped.filter(user=user, institution=world.inst).exists()
    assert not hasattr(user, "staff_profile") or user.staff_profile is None


def test_institution_accounts_get_no_staff_directory_record(world):
    from staff.models import Staff
    assert not Staff.objects.filter(user=world.ce).exists()
    assert Staff.objects.filter(user=world.staff).exists()


def test_nbte_roles_cannot_be_given_to_institution_users(world):
    import org
    from accounts.models import Role
    from staff.role_ui import grantable_roles

    with pytest.raises(org.OrgRuleError, match="NBTE staff role"):
        org.assign_role(world.su, world.ict, Role.objects.get(code="director"), "test")
    with pytest.raises(org.OrgRuleError, match="institution portal role"):
        org.assign_role(world.su, world.staff, Role.objects.get(code="inst-admin"), "test")
    codes = set(grantable_roles(world.su, world.ict).values_list("code", flat=True))
    assert codes and all(c.startswith("inst-") for c in codes)


def test_edit_role_page_refuses_nbte_role_for_institution_user(world, client_for):
    from accounts.models import Role
    c = client_for(world.su)
    resp = c.post(f"/accounts/users/{world.ict.pk}/roles/", {"role": Role.objects.get(code="director").pk})
    world.ict.refresh_from_db()
    assert world.ict.role.code == "inst-admin"
    assert resp.status_code in (200, 302)


# ---------------------------------------------------------------------------
# Data Hub
# ---------------------------------------------------------------------------
def _import(data, apply=True):
    return engine.import_workbook(io.BytesIO(data), apply=apply, default_password=DEFAULT_PW)


def _set_cell(data, sheet, email, column, value):
    wb = load_workbook(io.BytesIO(data))
    ws = wb[sheet]
    hdr = [c.value for c in ws[1]]
    for row in ws.iter_rows(min_row=2):
        if row[hdr.index("email")].value == email:
            row[hdr.index(column)].value = value
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _sheet_report(report, sheet_id):
    return next(s for s in report.sheets if s.id == sheet_id)


def test_datahub_user_sheets_are_separate(world):
    data = engine.export_workbook(["users", "institution_users", "portal_users"])
    wb = load_workbook(io.BytesIO(data), read_only=True)

    def emails(title):
        rows = list(wb[title].iter_rows(values_only=True))
        i = rows[0].index("email")
        return {r[i] for r in rows[1:]}
    nbte, inst, portal = emails("Users"), emails("Institution users"), emails("Portal users")
    wb.close()
    assert "officer.sep@nbte.gov.ng" in nbte and "ce@sep.local" in inst and "rp@sep.local" in portal
    assert not (nbte & inst) and not (nbte & portal) and not (inst & portal)
    # Institutions are loaded before institution users.
    ids = [s.id for s in engine.plan()]
    assert ids.index("institutions.Institution") < ids.index("accounts.User#institution")
    assert ids.index("accounts.User#institution") < ids.index("institutions.InstitutionUser")


def test_datahub_institution_user_import_maps_to_institution_and_refuses_unknown_codes(world):
    from accounts.models import User

    data = engine.export_workbook(["institution_users"])
    User.objects.filter(email="ce@sep.local").delete()
    report = _import(data)
    assert report.applied and report.error_count == 0, [(s.title, s.errors) for s in report.sheets if s.errors]
    ce = User.objects.get(email="ce@sep.local")
    assert ce.user_type == "institution" and ce.institution_id == world.inst.pk

    User.objects.filter(email="ce@sep.local").delete()
    bad = _set_cell(data, "Institution users", "ce@sep.local", "institution", "NOPE")
    report = _import(bad)
    errors = _sheet_report(report, "accounts.User#institution").errors
    assert errors and "no institution with code 'NOPE'" in errors[0][1]
    assert not User.objects.filter(email="ce@sep.local").exists()


def test_datahub_nbte_import_requires_department_and_refuses_institution_rows(world):
    from accounts.models import User

    data = engine.export_workbook(["users"])
    User.objects.filter(email="officer.sep@nbte.gov.ng").delete()
    no_dept = _set_cell(data, "Users", "officer.sep@nbte.gov.ng", "department", None)
    report = _import(no_dept)
    errors = _sheet_report(report, "accounts.User").errors
    assert any("department is required" in e[1] for e in errors)
    assert not User.objects.filter(email="officer.sep@nbte.gov.ng").exists()

    # An institution account on the NBTE sheet is refused (it belongs on its own sheet).
    wrong = _set_cell(data, "Users", "officer.sep@nbte.gov.ng", "user_type", "institution")
    report = _import(wrong)
    errors = _sheet_report(report, "accounts.User").errors
    assert any("Institution users" in e[1] for e in errors)

    # With its department the row imports as an NBTE staff account.
    report = _import(data)
    assert report.applied and _sheet_report(report, "accounts.User").errors == []
    user = User.objects.get(email="officer.sep@nbte.gov.ng")
    assert user.user_type == "staff" and user.department_id == world.staff.department_id


def test_datahub_refuses_nbte_role_on_institution_sheet(world):
    data = engine.export_workbook(["institution_users"])
    bad = _set_cell(data, "Institution users", "ict@sep.local", "role", "director")
    report = _import(bad)
    errors = _sheet_report(report, "accounts.User#institution").errors
    assert errors and "role" in errors[0][1]
    world.ict.refresh_from_db()
    assert world.ict.role.code == "inst-admin"
