"""Official letters pipeline: the institution's Issued documents tab, the
queue scoping (Director: own department; ES: all; others: no access), the
assign -> draft -> send back -> draft -> official copy flow with
notifications and the portal status line, reminders/escalation."""
import datetime

import pytest
from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from documents import issue as issuing
from documents import official_letters as ol
from documents.models import IssuedDocument, OfficialCopy, OfficialLetterJob

from .conftest import portal_client, staff_client

pytestmark = pytest.mark.django_db
QUEUE = "/qa/documents/official-letters/"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _pdf(name="signed.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4\n%signed\n", content_type="application/pdf")


@pytest.fixture
def world(light):
    """light + the Director in the seat of light.dept, a second department
    with its own Director and institution, the ES, and a staff member of
    light.dept who prepares letters."""
    from accounts.models import Role, User
    from institutions.models import Institution
    from documents import catalogue
    from staff.models import Department

    catalogue.ensure()  # templates and rules (the data migration is skipped with --nomigrations)
    light.dept.director = light.director
    light.dept.save(update_fields=["director"])
    other_dept, _ = Department.objects.get_or_create(code="dsci", defaults={"name": "Science Programmes"})
    light.other_inst = Institution.unscoped.create(code="OTHP", name="Other Poly", handling_department=other_dept,
                                                   status="approved")
    director_role = Role.objects.get(code="director")
    es_role, _ = Role.objects.get_or_create(code="executive-secretary",
                                            defaults={"name": "Executive Secretary", "rank": 999})

    def staff(email, role=None, dept=None):
        u = User.objects.create(email=email, first_name=email.split(".")[0].title(), last_name="Demo",
                                user_type="staff", role=role, department=dept, must_change_password=False)
        return User.objects.get(pk=u.pk)

    light.other_director = staff("other.director@demo.nbte.local", director_role, other_dept)
    from documents.testing import give_signature

    give_signature(light.other_director)
    other_dept.director = light.other_director
    other_dept.save(update_fields=["director"])
    light.other_dept = other_dept
    light.es = staff("es.office@demo.nbte.local", es_role)
    light.preparer = staff("prep.officer@demo.nbte.local", Role.objects.get(code="officer"), light.dept)
    light.outsider = staff("out.sider@demo.nbte.local", Role.objects.get(code="officer"), other_dept)
    # the ES office holds the register permissions (qa_core.seed_data ROLE_PERMISSIONS)
    from django.contrib.auth.models import Permission

    light.es.user_permissions.add(*Permission.objects.filter(
        content_type__app_label="documents", codename__in=("view_issued_documents", "upload_official_copy")))
    light.es = User.objects.get(pk=light.es.pk)
    return light


def _letter(inst, actor):
    return issuing.approval_recorded("institution.approved", inst, actor=actor, institution=inst,
                                     context={"department": "Polytechnic Programmes"})


def test_institution_tab_lists_issued_documents(world, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        doc = _letter(world.inst, world.director)
    receipt = issuing.issue("fee_receipt", None, actor=world.director, institution=world.inst,
                            context={"fee": "Institution fee"}, kind="receipt", title="Official receipt",
                            amount=1000, provisional=False)
    other = _letter(world.other_inst, world.other_director)

    r = staff_client(world.es).get(f"/qa/institutions/{world.inst.code}/?tab=issued")
    assert r.status_code == 200
    html = r.content.decode()
    assert "Issued documents" in html
    assert doc.reference in html and receipt.reference in html and other.reference not in html
    assert "Provisional" in html and doc.short_code in html
    assert issuing.verify_url(doc.token) in html and doc.download_url() in html

    # once the official copy is uploaded the tab shows it with its download
    with django_capture_on_commit_callbacks(execute=True):
        ol.upload_official(doc, actor=world.director, upload=_pdf())
    copy = OfficialCopy.unscoped.get(document=doc)
    html = staff_client(world.es).get(f"/qa/institutions/{world.inst.code}/?tab=issued").content.decode()
    assert "Official copy uploaded" in html and copy.download_url() in html
    # an officer of another department without the register permission does not see the letters
    html = staff_client(world.outsider).get(f"/qa/institutions/{world.inst.code}/?tab=issued")
    assert html.status_code == 404 or doc.reference.encode() not in html.content


def test_queue_scoping(world):
    mine = _letter(world.inst, world.director)
    theirs = _letter(world.other_inst, world.other_director)

    # Director of the handling department: own department only
    r = staff_client(world.director).get(QUEUE)
    assert r.status_code == 200
    assert mine.reference in r.content.decode() and theirs.reference not in r.content.decode()
    assert staff_client(world.director).get(f"{QUEUE}{theirs.pk}/").status_code == 404
    assert staff_client(world.director).get(f"{QUEUE}{mine.pk}/").status_code == 200

    # ES: everything
    r = staff_client(world.es).get(QUEUE)
    assert r.status_code == 200
    assert mine.reference in r.content.decode() and theirs.reference in r.content.decode()

    # other staff: no queue, no letter page
    assert staff_client(world.preparer).get(QUEUE).status_code == 403
    assert staff_client(world.outsider).get(f"{QUEUE}{mine.pk}/").status_code == 404
    assert staff_client(world.keeper).get(QUEUE).status_code == 403

    # QA home card: the Director sees their letter, the ES both, the officer none
    from qa_core import home

    w = home.build(world.director)["by_key"]["official_letters"]
    assert w.title == "Official letters to upload" and w.count == 1
    assert w.items[0].label == world.inst.name and "day" in w.items[0].sub
    assert home.build(world.es)["by_key"]["official_letters"].count == 2
    assert "official_letters" not in home.build(world.preparer)["by_key"]
    page = staff_client(world.director).get("/qa/")
    assert page.status_code == 200 and b"Official letters to upload" in page.content


def test_assign_draft_review_upload_flow(world, django_capture_on_commit_callbacks):
    from notifications.models import Notification
    from tasks.models import Task

    with django_capture_on_commit_callbacks(execute=True):
        doc = _letter(world.inst, world.director)

    # the portal shows the plain status line (no staff names)
    pc = portal_client(world.inst_user)
    letters = pc.get("/letters/").content.decode()
    assert "Official signed copy: being prepared by NBTE" in letters
    assert world.preparer.first_name not in letters

    d = staff_client(world.director)
    due = (timezone.localdate() + datetime.timedelta(days=3)).isoformat()
    with django_capture_on_commit_callbacks(execute=True):
        r = d.post(f"{QUEUE}{doc.pk}/assign/", {"assignee": world.preparer.pk, "due_date": due, "note": "Use v2"})
    assert r.status_code == 302
    job = OfficialLetterJob.objects.get(document=doc)
    assert job.state == job.ASSIGNED and job.assignee == world.preparer and job.due_date.isoformat() == due
    task = Task.objects.get(pk=job.task_id)
    assert task.state == Task.OPEN and task.assignee_rows.filter(user=world.preparer).exists()
    assert Notification.objects.filter(recipient=world.preparer, title__startswith="Prepare the official letter").exists()
    # the Tasks app cannot close it (completed from the letter page)
    from tasks.hooks import is_externally_managed
    assert is_externally_managed(task)

    # the preparer can open the letter page but not the queue, nor upload the official copy
    p = staff_client(world.preparer)
    assert p.get(f"{QUEUE}{doc.pk}/").status_code == 200
    assert p.post(f"{QUEUE}{doc.pk}/upload/", {"file": _pdf()}).status_code == 403
    assert not OfficialCopy.unscoped.filter(document=doc).exists()

    # draft -> Director told "Draft ready"
    with django_capture_on_commit_callbacks(execute=True):
        r = p.post(f"{QUEUE}{doc.pk}/draft/", {"file": SimpleUploadedFile("draft.docx", b"PK\x03\x04draft"),
                                               "note": "Please check the conditions"})
    assert r.status_code == 302
    job.refresh_from_db()
    assert job.state == job.DRAFT_READY and job.drafts.count() == 1
    assert Notification.objects.filter(recipient=world.director, title__startswith="Draft ready").exists()
    draft = job.drafts.first()
    assert d.get(draft.download_url()).status_code == 200
    assert staff_client(world.outsider).get(draft.download_url()).status_code in (403, 404)

    # Director sends it back with comments -> assignee told
    with django_capture_on_commit_callbacks(execute=True):
        d.post(f"{QUEUE}{doc.pk}/send-back/", {"comment": "Correct the address"})
    job.refresh_from_db()
    assert job.state == job.RETURNED and job.review_comment == "Correct the address"
    assert Notification.objects.filter(recipient=world.preparer, title__startswith="Draft sent back").exists()
    with django_capture_on_commit_callbacks(execute=True):
        p.post(f"{QUEUE}{doc.pk}/draft/", {"file": SimpleUploadedFile("draft2.pdf", b"%PDF-1.4 v2")})
    job.refresh_from_db()
    assert job.state == job.DRAFT_READY and job.drafts.count() == 2

    # the Director uploads the signed official copy -> institution notified, task closed
    with django_capture_on_commit_callbacks(execute=True):
        r = d.post(f"{QUEUE}{doc.pk}/upload/", {"file": _pdf(), "note": "Signed"})
    assert r.status_code == 302
    copy = OfficialCopy.unscoped.get(document=doc)
    job.refresh_from_db()
    assert job.state == job.DONE and job.completed_by == world.director
    assert Task.objects.get(pk=task.pk).state == Task.COMPLETED
    assert Notification.objects.filter(recipient=world.inst_user,
                                       title="Your official registration approval letter is ready").exists()
    assert doc not in ol.pending_documents()

    # portal: "Official copy ready – download"
    letters = pc.get("/letters/").content.decode()
    assert "Official copy ready – download" in letters
    page = pc.get(f"/letters/documents/{doc.pk}/").content.decode()
    assert 'data-official-status="ready"' in page and copy.portal_download_url() in page

    # queue empty again
    assert doc.reference not in d.get(QUEUE).content.decode()


def test_outsider_and_other_director_cannot_upload(world):
    doc = _letter(world.inst, world.director)
    for user in (world.outsider, world.other_director, world.preparer):
        r = staff_client(user).post(f"{QUEUE}{doc.pk}/upload/", {"file": _pdf()})
        assert r.status_code in (403, 404), user.email
    with pytest.raises(Exception):
        ol.upload_official(doc, actor=world.other_director, upload=_pdf())
    assert not OfficialCopy.unscoped.filter(document=doc).exists()
    # the assignment can only go to the handling department's staff
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        ol.assign(doc, actor=world.director, assignee=world.outsider)
    # the ES may upload anything
    ol.upload_official(doc, actor=world.es, upload=_pdf())
    assert OfficialCopy.unscoped.filter(document=doc).exists()


def test_department_names_setting_and_reminders(world, django_capture_on_commit_callbacks):
    import config
    from notifications.models import Notification

    doc = _letter(world.inst, world.director)
    status = ol.portal_status(doc)
    assert status["by"] == "NBTE" and not status["ready"]

    from unittest import mock

    with mock.patch.object(ol, "show_department_names", return_value=True):
        assert ol.portal_status(doc)["by"] == "NBTE (Polytechnic Programmes)"

    # not overdue yet -> nothing sent
    assert ol.send_reminders() == []
    IssuedDocument.unscoped.filter(pk=doc.pk).update(issued_at=timezone.now() - datetime.timedelta(days=ol.sla_days() + 1))
    sent = ol.send_reminders()
    assert {k for _pk, k in sent} == {"reminder", "escalation"}
    assert Notification.objects.filter(recipient=world.director, title__startswith="Official letter overdue").exists()
    assert Notification.objects.filter(recipient=world.es, title__startswith="Escalated").exists()
    assert ol.send_reminders() == []  # once only
    assert config.get(ol.SLA_KEY, 7) in (7, "7")
