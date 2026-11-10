"""SOP-03 visit invitations to professional bodies and external resource
persons (accreditation.visit_invitations).

The ES approval of the visit (stage 10) invites every body mapped to the
case's programmes and every external resource person on the team: a letter
in the documents registry, an e-mail (console outbox only — never real mail)
with a tokenised confirm link, and a portal notice. Staff track replies, call
people, resend; reminders go out once per threshold; new dates send amended
letters.
"""
import datetime

import pytest
from django.core import mail as django_mail
from django.test import Client
from django.utils import timezone

from qa_core.tests.phase4_fixtures import staff_client

pytestmark = pytest.mark.django_db


@pytest.fixture
def invited(p5, settings, django_capture_on_commit_callbacks):
    """p5's case with a body on the engineering programme, an unmapped body,
    one external and one NBTE-staff resource person, driven through the ES
    approval of the visit with the on-commit hooks executed."""
    from accreditation import case_demo
    from accreditation import case_services as cs
    from inspection import demo
    from inspection.models import ResourcePerson
    from institutions.models import ProfessionalBody, Programme

    settings.QA_EMAIL_DELIVERY = "console"
    settings.QA_EMAIL_ECHO_STDERR = False
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    body = ProfessionalBody.objects.create(name="Council for the Regulation of Engineering", acronym="COREN",
                                           email="registrar@coren.test", phone="0800 000 0000", portal_enabled=True)
    ProfessionalBody.objects.create(name="Unrelated Council", acronym="UC", email="uc@uc.test")
    Programme.unscoped.filter(pk=p5.eee.programme_id).update(professional_body=body)
    ResourcePerson.objects.filter(pk=p5.rps["rp_sci"].pk).update(source="nbte_staff")  # internal: not invited
    with cs.pinned_actors(case_demo.demo_pins(p5.people)):
        case = case_demo.advance(p5.case, p5.people, 9)
        p5.mappings = demo.map_demo_people(case, p5.people, p5.rps)
        with django_capture_on_commit_callbacks(execute=True):
            case = case_demo.advance(case, p5.people, 11)
    p5.case, p5.body = case, body
    return p5


def _invitations(case):
    from accreditation.models import VisitInvitation

    return {i.name: i for i in VisitInvitation.unscoped.filter(case=case)}


def test_approval_invites_mapped_bodies_and_external_resource_persons_only(invited):
    from accreditation.models import VisitDetail, VisitInvitation
    from qa_core.models import OutboundEmail

    rows = _invitations(invited.case)
    rp_eng = invited.rps["rp_eng"]
    assert set(rows) == {invited.body.name, rp_eng.full_name}  # no unmapped body, no NBTE-staff RP
    body_inv, rp_inv = rows[invited.body.name], rows[rp_eng.full_name]
    assert body_inv.kind == VisitInvitation.BODY and rp_inv.kind == VisitInvitation.RESOURCE_PERSON
    visit = VisitDetail.unscoped.get(case=invited.case)
    for inv in (body_inv, rp_inv):
        assert inv.status == VisitInvitation.SENT and inv.sent_at and inv.send_count == 1
        assert (inv.visit_start, inv.visit_end) == (visit.planned_start, visit.planned_end)
        assert inv.token and timezone.localtime(inv.token_expires_at).date() == visit.planned_end + datetime.timedelta(days=1)
    assert "Electrical" in " ".join(body_inv.programmes)
    # e-mails only in the console outbox, with the letter named and the confirm link
    outbox = list(OutboundEmail.objects.filter(subject__startswith="NBTE QA visit invitation"))
    assert {tuple(m.recipients) for m in outbox} == {("registrar@coren.test",), (rp_eng.email,)}
    msg = next(m for m in outbox if m.recipients == ["registrar@coren.test"])
    assert f"/invite/{body_inv.token}/" in msg.body_html and f"/invite/{body_inv.token}/" in msg.body_text
    assert "Attachments:" in msg.body_text
    rp_msg = next(m for m in outbox if m.recipients == [rp_eng.email])
    assert "/login/?username=" in rp_msg.body_html
    assert "/password/forgot/?email=" in rp_msg.body_html
    assert "Sign in to your resource-person portal" in rp_msg.body_html
    # our messages never reach a mail backend in console mode (other notices use the offline test backend)
    assert not [m for m in django_mail.outbox if "visit invitation" in m.subject.lower()]
    # the RP has a portal account: notified there
    from notifications.models import Notification

    assert Notification.objects.filter(recipient=rp_eng.user, url="/visit-invitations/").count() == 1
    assert rp_inv.portal_notified_at is not None


def test_mapped_invitees_are_visible_before_es_approval(p5):
    from accreditation import case_demo
    from accreditation import case_services as cs
    from inspection import demo
    from institutions.models import ProfessionalBody, Programme

    body = ProfessionalBody.objects.create(name="Council for the Regulation of Engineering", acronym="COREN",
                                           email="registrar@coren.test", portal_enabled=True)
    Programme.unscoped.filter(pk=p5.eee.programme_id).update(professional_body=body)
    with cs.pinned_actors(case_demo.demo_pins(p5.people)):
        case = case_demo.advance(p5.case, p5.people, 9)
        demo.map_demo_people(case, p5.people, p5.rps)

    page = staff_client(p5.people.desk_officer).get(f"/qa/cases/{case.pk}/?tab=visit").content.decode()
    assert "Mapped invitees" in page
    assert body.name in page and "Professional body" in page
    assert p5.rps["rp_eng"].full_name in page and "Resource person" in page
    assert "Invitations are sent after the ES approves the visit" in page


def test_letter_is_issued_from_the_new_templates(invited):
    from documents.models import DocumentTemplate, IssuedDocument

    rows = _invitations(invited.case)
    body_inv = rows[invited.body.name]
    rp_inv = rows[invited.rps["rp_eng"].full_name]
    assert DocumentTemplate.current("visit_invitation_body").title == "Visit invitation – professional body"
    assert DocumentTemplate.current("visit_invitation_rp").title == "Visit invitation – resource person"
    for inv, code in ((body_inv, "visit_invitation_body"), (rp_inv, "visit_invitation_rp")):
        doc = IssuedDocument.unscoped.get(pk=inv.letter_id)
        assert doc.template.code == code and doc.file and doc.status == "valid" and not doc.provisional
        assert doc.audience == "internal"  # never listed on the institution's Letters page
        body_text = " ".join(doc.payload["body"])
        assert invited.case.institution.name in body_text and inv.token in body_text
        facts = dict(doc.payload["facts"])
        assert facts["Dates"] and facts["Venue"] and facts["Team lead"] and facts["Contact officer"]
        assert "[" not in facts["Dates"]
        assert inv.letter_version == 1


def test_tokenised_link_confirms_without_sign_in_and_expires(invited, portal_host):
    from accreditation.models import VisitInvitation

    inv = _invitations(invited.case)[invited.body.name]
    c = Client(HTTP_HOST=portal_host)
    page = c.get(f"/invite/{inv.token}/")
    assert page.status_code == 200 and invited.case.institution.name in page.content.decode()
    assert "/login/?username=registrar%40coren.test" in page.content.decode()
    assert "/password/forgot/?email=registrar%40coren.test" in page.content.decode()
    assert "?next=/visit-invitations/" not in page.content.decode()
    assert b"Set or reset password securely" in page.content
    assert b"reply to this invitation above without signing in" in page.content
    assert b"white-space: normal" in page.content
    assert c.get(f"/invite/{inv.token}/letter/").status_code == 200
    assert c.get("/invite/not-a-real-token-at-all-xxxxxxxx/").status_code == 404
    r = c.post(f"/invite/{inv.token}/", {"answer": "confirm", "name": "Engr. Registrar", "note": "Engr. Bello"})
    assert r.status_code == 200
    inv.refresh_from_db()
    assert inv.status == VisitInvitation.ACKNOWLEDGED and inv.responded_via == "link"
    assert inv.responder_name == "Engr. Registrar" and inv.responded_by is None
    # after the visit the link stops working
    VisitInvitation.unscoped.filter(pk=inv.pk).update(token_expires_at=timezone.now() - datetime.timedelta(minutes=1),
                                                      status=VisitInvitation.SENT)
    r = c.post(f"/invite/{inv.token}/", {"answer": "confirm"})
    assert r.status_code == 422
    inv.refresh_from_db()
    assert inv.status == VisitInvitation.SENT
    assert c.get(f"/invite/{inv.token}/letter/").status_code == 404


def test_resource_person_login_and_password_reset_prefill_registered_email(invited, portal_host):
    rp = invited.rps["rp_eng"]
    c = Client(HTTP_HOST=portal_host)
    login = c.get(f"/login/?username={rp.email}")
    reset = c.get(f"/password/forgot/?email={rp.email}")

    assert login.status_code == 200 and f'value="{rp.email}"'.encode() in login.content
    assert reset.status_code == 200 and f'value="{rp.email}"'.encode() in reset.content
    assert f'/password/forgot/?email={rp.email.replace("@", "%40")}'.encode() in login.content


def test_visit_invitation_provisions_missing_resource_person_portal_account(invited):
    from accreditation import visit_invitations as vi
    from qa_core.models import OutboundEmail

    rp = invited.rps["rp_eng"]
    rp.user.delete()
    rp.refresh_from_db()
    assert rp.user_id is None

    vi.sync_for_case(invited.case, actor=invited.people.es)
    rp.refresh_from_db()

    assert rp.user_id and rp.user.user_type == "resource_person" and rp.user.is_active
    assert not rp.user.has_usable_password()
    assert OutboundEmail.objects.filter(
        recipients=[rp.email], subject="Your NBTE Quality Assurance Portal account"
    ).exists()


def test_resource_person_declines_on_the_portal_and_staff_are_alerted(invited, portal_host):
    from accreditation import case_services as cs
    from accreditation.models import VisitInvitation
    from notifications.models import Notification

    rp = invited.rps["rp_eng"]
    inv = _invitations(invited.case)[rp.full_name]
    type(rp.user).objects.filter(pk=rp.user.pk).update(must_change_password=False)
    rp.user.refresh_from_db()
    c = Client(HTTP_HOST=portal_host)
    c.force_login(rp.user)
    assert c.get("/visit-invitations/").status_code == 200
    # a decline needs a reason
    c.post(f"/visit-invitations/{inv.pk}/respond/", {"answer": "decline", "note": ""})
    inv.refresh_from_db()
    assert inv.status == VisitInvitation.SENT
    c.post(f"/visit-invitations/{inv.pk}/respond/", {"answer": "decline", "note": "Surgery that week"})
    inv.refresh_from_db()
    assert inv.status == VisitInvitation.DECLINED and inv.responded_via == "portal" and inv.responded_by == rp.user
    director = cs._director_of(invited.case.owning_department)[0]
    for user in (invited.case.desk_officer, director):
        assert Notification.objects.filter(recipient=user, title__contains="declined").exists(), user
    # another RP cannot see or answer it
    other = invited.rps["rp_sci"]
    type(other.user).objects.filter(pk=other.user.pk).update(must_change_password=False)
    other.user.refresh_from_db()
    c2 = Client(HTTP_HOST=portal_host)
    c2.force_login(other.user)
    assert c2.post(f"/visit-invitations/{inv.pk}/respond/", {"answer": "confirm"}).status_code == 404


def test_resend_contact_log_and_mark_confirmed_permissions(invited):
    from accreditation.models import VisitInvitation, VisitInvitationContact
    from accreditation import case_services as cs
    from accreditation import visit_invitations as vi

    inv = _invitations(invited.case)[invited.body.name]
    people = invited.people
    director = cs._director_of(invited.case.owning_department)[0]
    for user in (people.desk_officer, director, people.es):
        assert vi.can_manage(user, invited.case), user
    assert not vi.can_manage(people.audit_officer, invited.case)
    # an officer who may view the case but not manage the invitations
    outsider = staff_client(people.audit_officer)
    before = VisitInvitationContact.unscoped.filter(invitation=inv).count()
    outsider.post(f"/qa/visit-invitations/{inv.pk}/contact/", {"channel": "phone", "outcome": "no_answer"})
    outsider.post(f"/qa/visit-invitations/{inv.pk}/resend/")
    assert VisitInvitationContact.unscoped.filter(invitation=inv).count() == before
    inv.refresh_from_db()
    assert inv.send_count == 1
    # the desk officer resends and logs a call; the director marks it confirmed
    desk = staff_client(people.desk_officer)
    assert desk.post(f"/qa/visit-invitations/{inv.pk}/resend/").status_code == 302
    inv.refresh_from_db()
    assert inv.send_count == 2 and inv.status == VisitInvitation.SENT
    r = desk.post(f"/qa/visit-invitations/{inv.pk}/contact/",
                  {"channel": "phone", "outcome": "no_answer", "note": "Rang twice"})
    assert r.status_code == 302
    entry = VisitInvitationContact.unscoped.filter(invitation=inv, channel="phone").get()
    assert entry.by == people.desk_officer and entry.outcome == "no_answer" and entry.note == "Rang twice"
    staff_client(director).post(f"/qa/visit-invitations/{inv.pk}/acknowledge/",
                                {"via": "phone", "contact_name": "The Registrar"})
    inv.refresh_from_db()
    assert inv.status == VisitInvitation.ACKNOWLEDGED and inv.responded_via == "phone"
    assert inv.responded_by == director
    # the ES regenerates the letter: the old one is superseded
    from documents.models import IssuedDocument

    old = inv.letter_id
    staff_client(people.es).post(f"/qa/visit-invitations/{inv.pk}/regenerate/")
    inv.refresh_from_db()
    assert inv.letter_id != old and inv.letter_version == 2
    assert IssuedDocument.unscoped.get(pk=old).status == "superseded"
    # staff can download the letter; the case page shows the panel
    assert desk.get(f"/qa/visit-invitations/{inv.pk}/letter/").status_code == 200
    page = desk.get(f"/qa/cases/{invited.case.pk}/?tab=visit").content.decode()
    assert "Invitations and notices" in page and "1 of 2 confirmed" in page


def test_staff_urls_answer_404_out_of_scope(invited, make_user):
    inv = _invitations(invited.case)[invited.body.name]
    stranger = make_user("o@vtsd-x.local", role="officer", dept="vtsd", groups=["QA Desk"])
    c = staff_client(stranger)
    assert c.get(f"/qa/visit-invitations/{inv.pk}/letter/").status_code == 404
    assert c.post(f"/qa/visit-invitations/{inv.pk}/resend/").status_code == 404


def test_new_dates_send_an_amended_letter(invited):
    from accreditation import visit_invitations as vi
    from accreditation.models import VisitDetail, VisitInvitation
    from qa_core.models import OutboundEmail

    inv = _invitations(invited.case)[invited.body.name]
    vi.acknowledge(inv, via="link", attending=True)
    visit = VisitDetail.unscoped.get(case=invited.case)
    new_start = visit.planned_start + datetime.timedelta(days=7)
    VisitDetail.unscoped.filter(pk=visit.pk).update(planned_start=new_start,
                                                    planned_end=new_start + datetime.timedelta(days=2))
    result = vi.sync_for_case(invited.case)
    assert result["amended"] == 2 and result["created"] == 0
    inv.refresh_from_db()
    assert inv.visit_start == new_start and inv.letter_version == 2
    assert inv.status == VisitInvitation.SENT  # must confirm again
    assert inv.letter.title.startswith("Amended")
    assert OutboundEmail.objects.filter(subject__startswith="Amended NBTE QA visit invitation").count() == 2
    assert vi.sync_for_case(invited.case) == {"created": 0, "amended": 0, "withdrawn": 0}  # idempotent


def test_reminders_once_per_threshold_and_staff_alert(invited):
    from accreditation import visit_invitations as vi
    from accreditation.models import VisitDetail, VisitInvitation
    from notifications.models import Notification
    from qa_core.models import OutboundEmail

    rows = _invitations(invited.case)
    vi.acknowledge(rows[invited.body.name], via="phone", attending=True)  # the body confirmed
    start = VisitDetail.unscoped.get(case=invited.case).planned_start
    reminders = lambda: OutboundEmail.objects.filter(subject__startswith="Reminder").count()  # noqa: E731
    assert vi.send_reminders(today=start - datetime.timedelta(days=10)) == []  # before the first threshold
    out = vi.send_reminders(today=start - datetime.timedelta(days=7))
    assert [k for _pk, k in out] == ["reminder", "alert"] and reminders() == 1  # only the RP, not the body
    assert vi.send_reminders(today=start - datetime.timedelta(days=6)) == []  # once per threshold
    assert vi.send_reminders(today=start - datetime.timedelta(days=5)) == []
    assert [k for _pk, k in vi.send_reminders(today=start - datetime.timedelta(days=2))] == ["reminder", "alert"]
    assert vi.send_reminders(today=start - datetime.timedelta(days=1)) == []
    assert reminders() == 2
    rp_inv = VisitInvitation.unscoped.get(pk=rows[invited.rps["rp_eng"].full_name].pk)
    assert rp_inv.reminders_sent == [7, 2] and rp_inv.status == VisitInvitation.SENT
    assert Notification.objects.filter(recipient=invited.case.desk_officer, title__contains="not confirmed").count() == 2


def test_reminder_command_and_setting(invited, settings):
    import io

    from django.core.management import call_command

    from qa_core.tests.phase3_fixtures import set_config

    set_config("qa.visit.invite_reminder_days", "[]")
    from accreditation import visit_invitations as vi

    assert vi.reminder_days() == []
    out = io.StringIO()
    call_command("qa_stage_reminders", stdout=out)
    assert "Visit invitations: 0 reminder(s)" in out.getvalue()


def test_case_opening_joint_qa_notice_still_works_and_shows(invited):
    from accreditation.models import ProfessionalBodyNotice
    from institutions.professional_bodies import notify_bodies_for_case

    notices = notify_bodies_for_case(invited.case)
    assert [n.body for n in notices] == [invited.body]
    assert ProfessionalBodyNotice.unscoped.get(case=invited.case, body=invited.body).status == "sent"
    page = staff_client(invited.people.desk_officer).get(f"/qa/cases/{invited.case.pk}/?tab=visit").content.decode()
    assert "Joint QA notice sent" in page


def test_word_copy_only_for_director_es_and_super_admin(invited, portal_host):
    from accreditation import case_services as cs

    inv = _invitations(invited.case)[invited.body.name]
    url = f"/qa/visit-invitations/{inv.pk}/letter.docx"
    people = invited.people
    director = cs._director_of(invited.case.owning_department)[0]
    from django.contrib.auth import get_user_model

    root = get_user_model().objects.create_superuser(email="root@word.test", password="x-Pass-12345")
    for user in (director, people.es, root):
        r = staff_client(user).get(url)
        assert r.status_code == 200 and r.content[:2] == b"PK", user  # a .docx is a zip
        assert "wordprocessingml" in r["Content-Type"]
    for user in (people.desk_officer, people.audit_officer):  # may see the PDF, never the Word file
        assert staff_client(user).get(url).status_code == 404, user
        assert staff_client(user).get(f"/qa/visit-invitations/{inv.pk}/letter/").status_code in (200, 404)
    assert Client().get(url).status_code in (302, 403, 404)
    page = staff_client(people.desk_officer).get(f"/qa/cases/{invited.case.pk}/?tab=visit").content.decode()
    assert "letter.docx" not in page
    page = staff_client(director).get(f"/qa/cases/{invited.case.pk}/?tab=visit").content.decode()
    assert "letter.docx" in page
