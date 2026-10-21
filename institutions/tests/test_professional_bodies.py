"""Phase 6 Checkpoint A: professional bodies, programme mappings, the Excel
import / export, and the joint QA flag + notice."""
import io

import pytest
from django.test import Client
from openpyxl import Workbook, load_workbook

from accreditation.tests.conftest import *  # noqa: F401,F403
from institutions.models import ProfessionalBody, ProgrammeBodyMapping, ProgrammeLevel
from institutions.professional_bodies import bodies_for_programme, refresh_joint_qa
from institutions.professional_body_io import (
    BODY_SHEET,
    MAP_SHEET,
    export_xlsx,
    import_xlsx,
    template_xlsx,
)

pytestmark = pytest.mark.django_db


def _workbook(bodies=(), maps=(), body_header=None, map_header=None):
    wb = Workbook()
    ws = wb.active
    ws.title = BODY_SHEET
    ws.append(body_header or ["Name", "Acronym", "Address", "Contact person", "E-mail", "Phone", "Active"])
    for row in bodies:
        ws.append(list(row))
    ws2 = wb.create_sheet(MAP_SHEET)
    ws2.append(map_header or ["Professional body", "Programme", "Level", "Note", "Active"])
    for row in maps:
        ws2.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _dept():
    from qa_core.departments import qa_department

    return qa_department("dpap")


def _pcn(**kw):
    data = {"name": "Pharmacy Council of Nigeria", "acronym": "PCN", "email": "registrar@pcn.example.org"}
    data.update(kw)
    return ProfessionalBody.objects.create(**data)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------
def test_mapping_matches_by_name_and_level(qa_seed):
    from institutions.models import Institution, Programme

    body = _pcn()
    nd = ProgrammeLevel.objects.get(code="nd")
    hnd = ProgrammeLevel.objects.get(code="hnd")
    ProgrammeBodyMapping.objects.create(body=body, programme_name="Pharmacy  Technician", level=nd)
    inst = Institution.unscoped.create(code="PHX", name="PHX Polytechnic", status="approved", handling_department=_dept())
    nd_prog = Programme.unscoped.create(institution=inst, name="pharmacy technician", level=nd)
    hnd_prog = Programme.unscoped.create(institution=inst, name="Pharmacy Technician", level=hnd)
    other = Programme.unscoped.create(institution=inst, name="Statistics", level=nd)
    assert bodies_for_programme(nd_prog) == [body]
    assert bodies_for_programme(hnd_prog) == []
    assert bodies_for_programme(other) == []


def test_blank_level_covers_every_level_and_inactive_rows_do_not_match(qa_seed):
    from institutions.models import Institution, Programme

    body = _pcn()
    mapping = ProgrammeBodyMapping.objects.create(body=body, programme_name="Pharmacy Technician")
    inst = Institution.unscoped.create(code="PHY", name="PHY Polytechnic", status="approved", handling_department=_dept())
    hnd_prog = Programme.unscoped.create(institution=inst, name="Pharmacy Technician",
                                         level=ProgrammeLevel.objects.get(code="hnd"))
    assert bodies_for_programme(hnd_prog) == [body]
    mapping.is_active = False
    mapping.save()
    assert bodies_for_programme(hnd_prog) == []
    mapping.is_active = True
    mapping.save()
    body.is_active = False
    body.save()
    assert bodies_for_programme(hnd_prog) == []


# ---------------------------------------------------------------------------
# Joint QA flag and the notice when the case opens
# ---------------------------------------------------------------------------
@pytest.fixture
def joint_case(qa_seed, settings):
    from accreditation import case_demo

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    inst = case_demo.ensure_institution(tag=False)
    people, _creds = case_demo.ensure_accounts(force_change=False, tag=False)
    from institutions.models import Programme

    programme = Programme.unscoped.filter(institution=inst, status="accredited").first()
    body = _pcn()
    ProgrammeBodyMapping.objects.create(body=body, programme_name=programme.name, level=programme.level)
    case = case_demo.create_case(people, inst)
    return case, body, programme


def test_request_for_a_mapped_programme_is_flagged_joint_qa(joint_case):
    case, body, _programme = joint_case
    req = case.qa_request
    req.refresh_from_db()
    assert req.is_joint_qa is True
    assert list(req.joint_bodies.all()) == [body]


def test_opening_the_case_e_mails_the_body_once(joint_case):
    from accreditation.models import ProfessionalBodyNotice
    from institutions.professional_bodies import notify_bodies_for_case
    from qa_core.models import OutboundEmail, TimelineEvent

    case, body, programme = joint_case
    notices = ProfessionalBodyNotice.unscoped.filter(case=case)
    assert notices.count() == 1
    notice = notices.get()
    assert notice.body == body and notice.status == "sent" and notice.sent_at is not None
    assert str(programme) in notice.programmes
    mail = [m for m in OutboundEmail.objects.order_by("-pk") if body.email in (m.recipients or [])][0]
    assert case.reference in mail.body_text and "joint QA" in mail.subject
    assert TimelineEvent.unscoped.filter(event_code="qa_case.body_notified").exists()
    # A second call (e.g. a re-run) does not e-mail again.
    notify_bodies_for_case(case)
    assert ProfessionalBodyNotice.unscoped.filter(case=case).count() == 1


def test_body_without_e_mail_is_recorded_not_sent(qa_seed, settings):
    from accreditation import case_demo
    from accreditation.models import ProfessionalBodyNotice
    from institutions.models import Programme

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    inst = case_demo.ensure_institution(tag=False)
    people, _creds = case_demo.ensure_accounts(force_change=False, tag=False)
    programme = Programme.unscoped.filter(institution=inst, status="accredited").first()
    body = _pcn(email="")
    ProgrammeBodyMapping.objects.create(body=body, programme_name=programme.name)
    case = case_demo.create_case(people, inst)
    assert ProfessionalBodyNotice.unscoped.get(case=case).status == "no_email"


def test_unmapped_request_is_not_joint_qa(p4):
    from accreditation.models import ProfessionalBodyNotice

    req = p4.case.qa_request
    req.refresh_from_db()
    assert req.is_joint_qa is False
    assert not ProfessionalBodyNotice.unscoped.filter(case=p4.case).exists()


def test_flag_follows_the_programmes(p4):
    """Mapping a programme later and refreshing flags the request; removing it clears the flag."""
    req = p4.case.qa_request
    programme = req.active_programmes()[0].programme
    body = _pcn()
    mapping = ProgrammeBodyMapping.objects.create(body=body, programme_name=programme.name)
    assert refresh_joint_qa(req) == [body]
    req.refresh_from_db()
    assert req.is_joint_qa
    mapping.delete()
    assert refresh_joint_qa(req) == []
    req.refresh_from_db()
    assert not req.is_joint_qa


# ---------------------------------------------------------------------------
# Excel import / export
# ---------------------------------------------------------------------------
def test_template_has_both_sheets_and_a_reference(qa_seed):
    wb = load_workbook(io.BytesIO(template_xlsx()))
    assert wb.sheetnames == [BODY_SHEET, MAP_SHEET, "Reference"]


def test_import_creates_bodies_and_mappings_and_is_idempotent(qa_seed):
    file = _workbook(
        bodies=[["Pharmacy Council of Nigeria", "PCN", "Abuja", "The Registrar", "reg@pcn.example.org", "", "Yes"],
                ["Medical Laboratory Science Council of Nigeria", "MLSCN", "", "", "", "", ""]],
        maps=[["PCN", "Pharmacy Technician", "ND", "", "Yes"],
              ["Medical Laboratory Science Council of Nigeria", "Science Laboratory Technology", "", "", ""]])
    result = import_xlsx(file)
    assert result.ok, result.errors
    assert (result.bodies_created, result.mappings_created) == (2, 2)
    assert ProgrammeBodyMapping.objects.get(programme_name="Science Laboratory Technology").level is None
    again = import_xlsx(io.BytesIO(export_xlsx()))
    assert again.ok, again.errors
    assert again.changed == 0
    assert again.bodies_unchanged == 2 and again.mappings_unchanged == 2


def test_import_updates_a_matching_body(qa_seed):
    _pcn(email="")
    result = import_xlsx(_workbook(bodies=[["Pharmacy Council of Nigeria", "PCN", "", "", "new@pcn.example.org",
                                             "", "Yes"]]))
    assert result.ok and result.bodies_updated == 1
    assert ProfessionalBody.objects.get().email == "new@pcn.example.org"


def test_one_bad_row_rejects_the_whole_file(qa_seed):
    file = _workbook(
        bodies=[["Good Council", "GC", "", "", "ok@example.org", "", "Yes"],
                ["Bad Council", "BC", "", "", "not-an-email", "", "Maybe"]],
        maps=[["Nobody Council", "Pharmacy Technician", "PhD", "", ""]])
    result = import_xlsx(file)
    assert not result.ok
    messages = " ".join(e["message"] for e in result.errors)
    assert "not a valid e-mail" in messages and "Yes or No" in messages
    assert "No professional body called" in messages and "Unknown level" in messages
    assert {e["row"] for e in result.errors if e["sheet"] == BODY_SHEET} == {3}
    assert ProfessionalBody.objects.count() == 0


def test_dry_run_saves_nothing(qa_seed):
    result = import_xlsx(_workbook(bodies=[["Good Council", "GC", "", "", "", "", ""]]), dry_run=True)
    assert result.ok and result.bodies_created == 1
    assert ProfessionalBody.objects.count() == 0


def test_duplicate_rows_in_the_file_are_errors(qa_seed):
    result = import_xlsx(_workbook(bodies=[["Good Council", "", "", "", "", "", ""],
                                           ["good  council", "", "", "", "", "", ""]]))
    assert not result.ok and "Duplicate of row 2" in result.errors[0]["message"]


def test_missing_required_column_is_reported(qa_seed):
    result = import_xlsx(_workbook(bodies=[["x"]], body_header=["Acronym"]))
    assert not result.ok and "Column “Name” is missing" in result.errors[0]["message"]


def test_not_a_workbook_is_refused(qa_seed):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        import_xlsx(io.BytesIO(b"not excel"))


# ---------------------------------------------------------------------------
# Screens and permissions
# ---------------------------------------------------------------------------
def _staff(make_user, perm=None, email="curriculum@nbte.local"):
    from django.contrib.auth.models import Permission

    from accounts.models import User

    user = make_user(email, dept="dpap")
    if perm:
        user.user_permissions.add(Permission.objects.get(codename=perm))
    return User.objects.get(pk=user.pk)


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def test_curriculum_officer_role_can_manage(qa_seed):
    from accounts.models import Role

    role = Role.objects.get(code="curriculum-officer")
    assert role.permissions.filter(codename="manage_professional_bodies").exists()


def test_list_has_search_filters_and_pagination(qa_seed, make_user):
    user = _staff(make_user, "manage_professional_bodies")
    for i in range(30):
        ProfessionalBody.objects.create(name=f"Council {i:02d}", acronym=f"C{i:02d}")
    c = _client(user)
    page = c.get("/qa/professional-bodies/")
    assert page.status_code == 200
    html = page.content.decode()
    for name in ('name="q"', 'name="status"', 'name="level"', 'name="contact"'):
        assert name in html
    assert "Council 24" in html and "Council 25" not in html
    assert "Council 29" in c.get("/qa/professional-bodies/?all=1").content.decode()
    rows = c.get("/qa/professional-bodies/?q=Council+07", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-rows")
    assert rows.status_code == 200 and "Council 07" in rows.content.decode() and "<header" not in rows.content.decode()


def test_add_body_with_inline_errors(qa_seed, make_user):
    c = _client(_staff(make_user, "manage_professional_bodies"))
    bad = c.post("/qa/professional-bodies/new/", {"name": "", "email": "nope"}, HTTP_HX_REQUEST="true",
                 HTTP_HX_TARGET="pb-form")
    assert bad.status_code == 422
    assert "qa-field__error" in bad.content.decode()
    ok = c.post("/qa/professional-bodies/new/", {"name": "Nursing and Midwifery Council of Nigeria", "acronym": "nmcn",
                                                 "email": "reg@nmcn.example.org", "is_active": "on"},
                HTTP_HX_REQUEST="true", HTTP_HX_TARGET="pb-form")
    assert ok.status_code == 204
    assert ProfessionalBody.objects.get().acronym == "NMCN"


def test_map_a_programme_and_toggle_it(qa_seed, make_user):
    c = _client(_staff(make_user, "manage_professional_bodies"))
    body = _pcn()
    nd = ProgrammeLevel.objects.get(code="nd")
    response = c.post("/qa/professional-bodies/mappings/save/",
                      {"fixed_body": body.pk, "body": body.pk, "programme_name": "Pharmacy Technician",
                       "level": nd.pk, "is_active": "on"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 204 and "pb:changed" in response["HX-Trigger"]
    dup = c.post("/qa/professional-bodies/mappings/save/",
                 {"fixed_body": body.pk, "body": body.pk, "programme_name": "pharmacy technician", "level": nd.pk},
                 HTTP_HX_REQUEST="true")
    assert dup.status_code == 422 and "already mapped" in dup.content.decode()
    mapping = ProgrammeBodyMapping.objects.get()
    toggled = c.post(f"/qa/professional-bodies/mappings/{mapping.pk}/toggle/", HTTP_HX_REQUEST="true")
    assert toggled.status_code == 200
    mapping.refresh_from_db()
    assert mapping.is_active is False


def test_import_screen_checks_then_imports(qa_seed, make_user):
    c = _client(_staff(make_user, "manage_professional_bodies"))
    file = _workbook(bodies=[["Good Council", "GC", "", "", "", "", ""]])
    file.name = "bodies.xlsx"
    checked = c.post("/qa/professional-bodies/import/", {"file": file, "mode": "check"}, HTTP_HX_REQUEST="true")
    assert checked.status_code == 200 and "ready to import" in checked.content.decode()
    assert ProfessionalBody.objects.count() == 0
    file.seek(0)
    applied = c.post("/qa/professional-bodies/import/", {"file": file, "mode": "apply"}, HTTP_HX_REQUEST="true")
    assert applied.status_code == 200 and "Import complete" in applied.content.decode()
    assert ProfessionalBody.objects.count() == 1


def test_export_downloads_a_workbook(qa_seed, make_user):
    _pcn()
    c = _client(_staff(make_user, "manage_professional_bodies"))
    response = c.get("/qa/professional-bodies/export/")
    assert response.status_code == 200
    wb = load_workbook(io.BytesIO(response.content))
    assert wb[BODY_SHEET]["A2"].value == "Pharmacy Council of Nigeria"


def test_viewers_cannot_edit(qa_seed, make_user):
    viewer = make_user("viewer@nbte.local", dept="dpap")
    c = _client(viewer)
    assert c.get("/qa/professional-bodies/").status_code == 200
    assert c.get("/qa/professional-bodies/new/").status_code == 403
    assert c.post("/qa/professional-bodies/import/").status_code == 403
    assert "Add a body" not in c.get("/qa/professional-bodies/").content.decode()


def test_institution_users_are_refused(qa_seed, make_user):
    from institutions.models import Institution

    inst = Institution.unscoped.create(code="PHZ", name="PHZ Polytechnic", status="approved", handling_department=_dept())
    user = make_user("admin@phz.local", user_type="institution", institution=inst)
    assert _client(user).get("/qa/professional-bodies/").status_code in (302, 403, 404)
