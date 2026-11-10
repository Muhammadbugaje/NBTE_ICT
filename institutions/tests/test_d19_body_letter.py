"""D-19: a programme whose body must issue a letter waits in
``approved_pending_body_letter``; the uploaded letter releases it."""
import datetime

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

pytestmark = pytest.mark.django_db


class NS:
    pass


class _Decision:
    """The small part of ``accreditation.Decision`` that ``_apply_one`` reads."""

    def __init__(self, programme, approval_type, **extra):
        self.programme_id = programme.pk
        self.approval_type = approval_type
        self.approval_type_id = approval_type.pk
        self.outcome = "approve"
        self.approved_streams = 1
        self.commencement_date = datetime.date(2026, 10, 1)
        self.weaknesses = []
        self.pk = 999
        self.applied_at = None
        self.case = NS()
        self.case.reference = "T/D19/1"
        for key, value in extra.items():
            setattr(self, key, value)

    def save(self, **kwargs):
        pass


@pytest.fixture
def w(qa_seed, db):
    from accreditation.models import ApprovalType
    from institutions.models import Institution, Programme, ProgrammeLevel, ProfessionalBody
    from staff.models import Department

    ns = NS()
    dept = Department.objects.get_or_create(code="dpap", defaults={"name": "DPAP"})[0]
    ns.inst = Institution.unscoped.create(code="D19", name="D19 Polytechnic", handling_department=dept,
                                          status="approved")
    ns.body = ProfessionalBody.objects.create(name="Pharmacy Council of Nigeria", acronym="PCN",
                                              requires_letter_before_commencement=True)
    ns.programme = Programme.unscoped.create(institution=ns.inst, name="Pharmacy Technician",
                                             level=ProgrammeLevel.objects.get(code="nd"),
                                             professional_body=ns.body, status="proposed",
                                             awaiting_body_letter=True)
    ns.approval = ApprovalType.objects.create(code="atc", name="Approval to commence", duration_months=24,
                                              programme_status="approval_to_commence")
    return ns


def test_waiting_programme_gets_the_pending_status(w):
    from accreditation.decisions import _apply_one

    _apply_one(_Decision(w.programme, w.approval), actor=None)
    w.programme.refresh_from_db()
    assert w.programme.status == "approved_pending_body_letter"
    assert w.programme.approval_type_id == w.approval.pk


def test_setting_off_applies_the_approval_immediately(w):
    from accreditation.decisions import _apply_one
    from config.models import ConfigEntry

    ConfigEntry.objects.filter(key="qa.pb.wait_for_body_letter").update(value="false")
    from django.core.cache import cache

    cache.clear()
    _apply_one(_Decision(w.programme, w.approval), actor=None)
    w.programme.refresh_from_db()
    assert w.programme.status == "approval_to_commence"


def test_body_letter_releases_the_programme(w):
    from accreditation.decisions import _apply_one
    from institutions import pb_portal

    _apply_one(_Decision(w.programme, w.approval), actor=None)
    w.programme.refresh_from_db()
    assert w.programme.status == "approved_pending_body_letter"
    upload = SimpleUploadedFile("pcn-letter.pdf", b"%PDF-1.4\n%%EOF\n", content_type="application/pdf")
    pb_portal.upload_letter(w.programme, body=w.body, user=None, upload=upload)
    w.programme.refresh_from_db()
    assert w.programme.awaiting_body_letter is False
    assert w.programme.status == "approval_to_commence"
