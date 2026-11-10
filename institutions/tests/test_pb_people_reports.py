"""v2.6: the professional body as a portal client — its people (membership
rows, primary contact), visit representative assignment, and the body's visit
report chain (rep → registrar → NBTE) with the case's Reports tab surface."""
import pytest
from django.utils import timezone

from qa_core.tests.phase4_fixtures import p4  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def body_setup(p4, make_institution):
    """A professional body mapped onto the demo case's programme, with its
    registrar (No.1) and a representative account."""
    from accounts.models import Role, User

    from institutions.models import ProfessionalBody, Programme

    from . import pb_people as service

    case = p4.case
    programme = case.qa_request.programmes.first()
    cpn = ProfessionalBody.objects.create(name="Demo Computing Council", acronym="CPN")
    programme.professional_body = cpn
    programme.save(update_fields=["professional_body", "updated_at"])
    admin = User(email="registrar@cpn.local", first_name="Ada", last_name="Registrar",
                 user_type="professional_body", professional_body=cpn,
                 role=service.role_for("pb-admin"), must_change_password=False)
    admin.set_password("Test-pass-2026!")
    admin.save()
    rep = User(email="rep@cpn.local", first_name="Rex", last_name="Fielder",
               user_type="professional_body", professional_body=cpn,
               role=service.role_for("pb-rep"), must_change_password=False)
    rep.set_password("Test-pass-2026!")
    rep.save()
    from institutions.models import ProfessionalBodyMember

    ProfessionalBodyMember.objects.create(body=cpn, user=admin, job_title="Registrar",
                                          is_primary_contact=True, invited_at=timezone.now())
    return Simple(case=case, programme=programme, body=cpn, admin=admin, rep=rep)


class Simple:
    pass


# ---------------------------------------------------------------------------
# people
# ---------------------------------------------------------------------------
def test_add_member_creates_account_and_membership(body_setup, mailoutbox):
    from accounts.models import User

    from institutions import pb_people as service
    from institutions.models import ProfessionalBodyMember

    user = service.add_member(body_setup.body, email="secretary@cpn.local", full_name="Mina Scribe",
                              job_title="Secretariat", role_code="pb-viewer", invited_by=body_setup.admin)
    assert user.email == "secretary@cpn.local" and user.user_type == "professional_body"
    member = ProfessionalBodyMember.objects.get(user=user)
    assert member.body_id == body_setup.body.pk and member.job_title == "Secretariat"
    assert not member.is_primary_contact
    assert user.has_usable_password() is False
    assert any("set_password" in m.subject.lower() or "password" in m.subject.lower() for m in mailoutbox)
    assert User.objects.filter(email__iexact="SECRETARY@cpn.local").exists()


def test_add_member_rejects_duplicate_email(body_setup):
    from django.core.exceptions import ValidationError

    from institutions import pb_people as service

    with pytest.raises(ValidationError):
        service.add_member(body_setup.body, email="rep@cpn.local", full_name="Dup Person")


def test_primary_contact_moves(body_setup):
    from institutions import pb_people as service
    from institutions.models import ProfessionalBodyMember

    rep_member = ProfessionalBodyMember.objects.get(user=body_setup.rep)
    assert service.set_primary(body_setup.body, body_setup.rep)
    rep_member.refresh_from_db()
    admin_member = ProfessionalBodyMember.objects.get(user=body_setup.admin)
    assert rep_member.is_primary_contact and not admin_member.is_primary_contact


def test_deactivate_and_reactivate(body_setup, mailoutbox):
    from django.contrib.auth import get_user_model

    from institutions import pb_people as service

    user = service.add_member(body_setup.body, email="off@cpn.local", full_name="Gone Person",
                              send_link=False)
    member = user.body_membership
    mailoutbox.clear()
    service.deactivate_member(member, by=body_setup.admin)
    user.refresh_from_db()
    assert user.is_active is False and member.is_active_link is False
    service.reactivate_member(getattr(user, "body_membership"))
    User = get_user_model()
    user = User.objects.get(pk=user.pk)
    assert user.is_active is True
    assert len(mailoutbox) == 1  # the fresh set-password link


# ---------------------------------------------------------------------------
# visit assignment
# ---------------------------------------------------------------------------
def test_assign_representative_creates_visit_external_member(body_setup):
    from institutions import pb_people as service

    row = service.assign_representative(body_setup.case, body_setup.body,
                                        body_setup.admin, body_setup.rep, remark="Field officer")
    assert row.kind == row.PROFESSIONAL_BODY and row.user_id == body_setup.rep.pk
    assert row.case_id == body_setup.case.pk and row.remark == "Field officer"
    assert service.representative_for_case(body_setup.case, body_setup.body).pk == row.pk
    # exchanging: the same person again raises; a different one updates the row
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        service.assign_representative(body_setup.case, body_setup.body,
                                      body_setup.admin, body_setup.rep)
    from accounts.models import User

    from institutions.models import ProfessionalBodyMember

    second = User(email="rep2@cpn.local", first_name="Two", last_name="Fielder",
                  user_type="professional_body", professional_body=body_setup.body,
                  role=service.role_for("pb-rep"), must_change_password=False)
    second.set_password("Test-pass-2026!")
    second.save()
    ProfessionalBodyMember.objects.create(body=body_setup.body, user=second, invited_at=timezone.now())
    row = service.assign_representative(body_setup.case, body_setup.body,
                                        body_setup.admin, second, remark="")
    assert row.user_id == second.pk, "the member-of-record moved to the new representative"


def test_assign_representative_rejects_nonmember(body_setup):
    from django.core.exceptions import ValidationError

    from institutions import pb_people as service

    with pytest.raises(ValidationError):
        service.assign_representative(body_setup.case, body_setup.body, body_setup.admin,
                                      body_setup.case.desk_officer)


# ---------------------------------------------------------------------------
# the report chain
# ---------------------------------------------------------------------------
def test_report_chain_rep_to_nbte(body_setup):
    from accreditation import pb_reports as service
    from accreditation.models import BodyVisitReport, VisitExternalMember

    member_row = VisitExternalMember.unscoped.create(case=body_setup.case, body=body_setup.body,
                                                     kind=VisitExternalMember.PROFESSIONAL_BODY,
                                                     user=body_setup.rep, status="proposed",
                                                     added_by=body_setup.admin)
    # only an assigned person writes
    report = service.create_report(body_setup.rep, body_setup.case, title="CPN visit findings",
                                   content="Labs were good.", recommendation=BodyVisitReport.SATISFACTORY,
                                   member=member_row)
    assert report.status == BodyVisitReport.DRAFT and report.member_id == member_row.pk
    assert service.cases_awaiting_a_report(body_setup.rep) == []  # the case now has a report
    assert list(service.to_review(body_setup.admin)) == []  # not submitted yet

    service.submit_report(body_setup.rep, report)
    report.refresh_from_db()
    assert report.status == BodyVisitReport.SUBMITTED
    assert member_row.status == "confirmed"
    assert report in list(service.to_review(body_setup.admin))

    # only the body's administrator decides
    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        service.decide(body_setup.rep, report, "approve")
    with pytest.raises(Exception):
        service.decide(body_setup.admin, report, "return", remark="")  # remark needed
    service.decide(body_setup.admin, report, "approve")
    report.refresh_from_db()
    assert report.status == BodyVisitReport.APPROVED and report.reviewed_by_id == body_setup.admin.pk

    service.send_to_nbte(body_setup.admin, report)
    report.refresh_from_db()
    assert report.status == BodyVisitReport.WITH_NBTE and report.sent_at is not None


def test_case_reports_tab_shows_body_report(body_setup, officer):
    from django.test import Client

    from accreditation import pb_reports as service
    from accreditation.models import BodyVisitReport, VisitExternalMember

    member_row = VisitExternalMember.unscoped.create(case=body_setup.case, body=body_setup.body,
                                                     kind=VisitExternalMember.PROFESSIONAL_BODY,
                                                     user=body_setup.rep, status="proposed",
                                                     added_by=body_setup.admin)
    report = service.create_report(body_setup.rep, body_setup.case,
                                   content="Findings on the visit.", member=member_row)
    service.submit_report(body_setup.rep, report)
    service.decide(body_setup.admin, report, "approve")
    service.send_to_nbte(body_setup.admin, report)
    case, report = body_setup.case, BodyVisitReport.unscoped.get(pk=report.pk)

    c = Client()
    officer.is_superuser = True
    officer.save()
    c.force_login(officer)
    r = c.get(f"/qa/cases/{case.pk}/?tab=reports")
    assert r.status_code == 200, r.content[:300]
    assert b"CPN" in r.content and b"Findings on the visit" in r.content
    # and the private stage (draft) never shows on NBTE's desk
    second = service.create_report(body_setup.rep, case, content="WIP draft.", member=member_row)
    r = c.get(f"/qa/cases/{case.pk}/?tab=reports")
    assert "WIP draft" not in r.content.decode() and second.status == "draft"


def test_portal_pages_render(body_setup, client_for, portal_host):
    for path in ("/pb/people/", "/pb/visits/", "/pb/reports/", "/pb/institutions/", "/pb/requests/"):
        r = client_for(body_setup.admin, host=portal_host).get(path)
        assert r.status_code == 200, (path, r.status_code, getattr(r, "content", b"")[:300])


def test_portal_person_flow(body_setup, client_for, portal_host, mailoutbox):
    c = client_for(body_setup.admin, host=portal_host)
    # add
    r = c.post("/pb/people/new/", {"full_name": "Ngozi Officer", "email": "ngozi@cpn.local",
                                   "job_title": "Field officer", "role": "pb-rep"})
    assert r.status_code == 200, getattr(r, "content", b"")[:300]
    from institutions.models import ProfessionalBodyMember

    member = ProfessionalBodyMember.objects.get(user__email="ngozi@cpn.local")
    assert member.job_title == "Field officer"
    assert any("ngozi@cpn.local" in m.to[0] for m in mailoutbox)
    # deactivate + reactivate through the toggle
    r = c.post(f"/pb/people/{member.pk}/toggle/")
    assert r.status_code == 200
    member.refresh_from_db()
    assert member.is_active_link is False
    r = c.post(f"/pb/people/{member.pk}/toggle/")
    member.refresh_from_db()
    assert member.is_active_link is True


def test_portal_request_flow(body_setup, client_for, portal_host):
    from institutions.models import BodyProgrammeRequest, Institution

    c = client_for(body_setup.admin, host=portal_host)
    other = Institution.unscoped.create(code="reqd", name="Requested Poly", status="approved")
    r = c.post("/pb/requests/new/", {"institution": other.pk, "programme_name": "Diploma in Data Science",
                                     "level": body_setup.programme.level_id, "rationale": "CPN accredits it."})
    assert r.status_code in (302, 200), getattr(r, "content", b"")[:300]
    row = BodyProgrammeRequest.objects.get(body=body_setup.body)
    assert row.programme_name == "Diploma in Data Science" and row.status == BodyProgrammeRequest.PENDING
    assert row.requested_by_id == body_setup.admin.pk
    # duplicate pending raises (validation, not a crash)
    r = c.post("/pb/requests/new/", {"institution": other.pk, "programme_name": "diploma in data science",
                                     "rationale": ""})
    assert r.status_code == 422
    # withdraw
    r = c.post(f"/pb/requests/{row.pk}/cancel/")
    assert r.status_code in (302, 200)
    row.refresh_from_db()
    assert row.status == BodyProgrammeRequest.WITHDRAWN and row.decided_at is not None


def test_report_pages_scoped(body_setup, client_for, portal_host):
    from accreditation import pb_reports as service
    from accreditation.models import VisitExternalMember

    member_row = VisitExternalMember.unscoped.create(case=body_setup.case, body=body_setup.body,
                                                     kind=VisitExternalMember.PROFESSIONAL_BODY,
                                                     user=body_setup.rep, status="proposed",
                                                     added_by=body_setup.admin)
    report = service.create_report(body_setup.rep, body_setup.case, content="Scoped check.", member=member_row)
    # the author sees it, the registrar sees it in the review inbox
    assert client_for(body_setup.rep, host=portal_host).get(f"/pb/reports/{report.pk}/").status_code == 200
    r = client_for(body_setup.admin, host=portal_host).get("/pb/reports/")
    assert b"Scoped check" in r.content
    # another body's viewer cannot
    from accounts.models import Role, User

    from institutions.models import ProfessionalBody, ProfessionalBodyMember

    other = ProfessionalBody.objects.create(name="Other Body", acronym="OTH")
    outsider = User(email="out@oth.local", first_name="Out", last_name="Sider",
                    user_type="professional_body", professional_body=other,
                    role=Role.objects.get(code="pb-admin"), must_change_password=False)
    outsider.set_password("Test-pass-2026!")
    outsider.save()
    ProfessionalBodyMember.objects.create(body=other, user=outsider, invited_at=timezone.now())
    assert client_for(outsider, host=portal_host).get(f"/pb/reports/{report.pk}/").status_code == 404
    r = client_for(outsider, host=portal_host).get("/pb/reports/")
    assert b"Scoped check" not in r.content


def test_report_download_rights(body_setup, client_for, portal_host, officer):
    from qa_core.tests.phase2_fixtures import pdf_upload

    from accreditation import pb_reports as service
    from accreditation.models import VisitExternalMember

    member_row = VisitExternalMember.unscoped.create(case=body_setup.case, body=body_setup.body,
                                                     kind=VisitExternalMember.PROFESSIONAL_BODY,
                                                     user=body_setup.rep, status="proposed",
                                                     added_by=body_setup.admin)
    report = service.create_report(body_setup.rep, body_setup.case, content="File check.",
                                   member=member_row, upload=pdf_upload("findings.pdf"))
    assert report.file
    c = client_for(body_setup.rep, host=portal_host)
    assert c.get(f"/pb/reports/{report.pk}/file/").status_code == 200
    # register approves and sends; then NBTE downloads on the case
    service.submit_report(body_setup.rep, report)
    service.decide(body_setup.admin, report, "approve")
    service.send_to_nbte(body_setup.admin, report)
    officer.is_superuser = True
    officer.save()
    from django.test import Client

    ic = Client()
    ic.force_login(officer)
    assert ic.get(f"/qa/cases/{body_setup.case.pk}/pb-reports/{report.pk}/file/").status_code == 200
    # before it is sent NBTE cannot fetch it (404 stage guard) — build a second
    rank = service.create_report(body_setup.rep, body_setup.case, content="Unsent.", member=member_row)
    assert ic.get(f"/qa/cases/{body_setup.case.pk}/pb-reports/{report.pk}/file/").status_code == 200
    assert ic.get(f"/qa/cases/{body_setup.case.pk}/pb-reports/{rank.pk}/file/").status_code == 404
