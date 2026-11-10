"""Shared fixtures for the Phase 3 tests (QA requests, SSQs, queries).
Import with ``from qa_core.tests.phase3_fixtures import *`` after the Phase 1
and Phase 2 fixtures."""
import datetime

import pytest
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

PASSWORD = "Str0ng!Passw0rd#2026"


class NS:
    pass


@pytest.fixture
def department_mode():
    """Run the test with ``qa.privacy.mode = department`` (what the deployed
    system runs; a fresh install defaults to ``assignment`` — v2.1 X1). Tests
    that assert the department-member visibility rule set this explicitly."""
    set_config("qa.privacy.mode", "department")


def set_config(key, value):
    """Change a setting the way the settings screen does (save() clears the
    config cache; a queryset update would not)."""
    from config.models import ConfigEntry

    entry = ConfigEntry.objects.get(key=key, scope="global")
    entry.value = value
    entry.save()


@pytest.fixture
def p3(p2, make_user, make_institution, settings):
    """Approved institution AAA (DPAP) with an administrator, a chief
    executive, an academic-department sub-account and four programmes; a
    second institution BBB (VTSD) with its own administrator; the QA fee
    confirmed (₦50,000 + ₦150,000 per programme)."""
    from institutions.models import Programme, ProgrammeCategory, ProgrammeLevel

    for key, value in (("qa.fee.qa_request.base", "50000"), ("qa.fee.qa_request.per_programme", "150000"),
                       ("qa.fee.qa_request_processing.confirmed", "true")):
        set_config(key, value)
    ns = NS()
    ns.p2 = p2
    ns.inst = make_institution("AAA", "dpap", name="Alpha Polytechnic")
    ns.other = make_institution("BBB", "vtsd", name="Bravo Polytechnic")
    ns.admin = make_user("admin@aaa.local", role="inst-admin", user_type="institution", institution=ns.inst)
    ns.ceo = make_user("rector@aaa.local", role="inst-chief-executive", user_type="institution", institution=ns.inst)
    ns.dept = make_user("cs.dept@aaa.local", role="inst-academic-dept", user_type="institution", institution=ns.inst)
    ns.other_admin = make_user("admin@bbb.local", role="inst-admin", user_type="institution", institution=ns.other)
    nd = ProgrammeLevel.objects.get(code="nd")
    hnd = ProgrammeLevel.objects.get(code="hnd")
    comp = ProgrammeCategory.objects.get(name="Computing & Information Technology")
    eng = ProgrammeCategory.objects.get(name="Engineering Technology")
    today = timezone.localdate()

    def prog(inst, name, level, cat, status, days):
        return Programme.unscoped.create(institution=inst, name=name, level=level, category=cat, status=status,
                                         approved_streams=1, commencement_date=today.replace(year=today.year - 5),
                                         expiry_date=today + datetime.timedelta(days=days) if days is not None else None)

    ns.cs = prog(ns.inst, "Computer Science", nd, comp, "accredited", 120)
    ns.eee = prog(ns.inst, "Electrical/Electronic Engineering Technology", hnd, eng, "accredited", 150)
    ns.far = prog(ns.inst, "Mechanical Engineering Technology", nd, eng, "accredited", 3 * 365)
    ns.expired = prog(ns.inst, "Statistics", nd, comp, "expired", -30)
    ns.proposed = prog(ns.inst, "Cyber Security", nd, comp, "proposed", None)
    ns.bravo_prog = prog(ns.other, "Computer Science", nd, comp, "accredited", 100)
    return ns


def make_request(p3, programmes=None, type_code="reaccreditation", user=None):
    from accreditation import qa_services as svc
    from accreditation.models import QAType

    qa_type = QAType.objects.get(code=type_code)
    return svc.create_request(p3.inst, qa_type, programmes or [p3.cs, p3.eee], user=user or p3.admin)


def bill_of(req):
    from accreditation import qa_services as svc

    obs = [o for o in svc.open_obligations(req) if o.status not in ("cancelled",)]
    return obs[0] if obs else None


def waive_fee(p3, req):
    """Director of Finance waives the QA fee → fee confirmed → SSQs open."""
    from payments import services as pay

    from accreditation import qa_services as svc

    bill = bill_of(req)
    with transaction.atomic():
        pay.waive(bill, actor=p3.p2.fin_director, reason="Waived for the test")
    # Tests run inside one transaction, so the on-commit `payment_settled`
    # signal never fires by itself; deliver it (the receiver is idempotent).
    bill.refresh_from_db()
    svc.payment_settled(bill)
    req.refresh_from_db()
    return req


@pytest.fixture
def paid_request(p3, django_capture_on_commit_callbacks):
    """A Reaccreditation request for CS and EEE, submitted, fee waived: the
    SSQs are open."""
    from accreditation import qa_services as svc

    req = make_request(p3)
    svc.submit(req, user=p3.ceo, declaration=True)
    with django_capture_on_commit_callbacks(execute=True):
        waive_fee(p3, req)
    req.refresh_from_db()
    assert req.status == "ssq_in_progress"
    return req


def accepted_doc(inst, kind_code, status="accepted", user=None):
    from institutions.models import DocumentKind, InstitutionDocument

    doc = InstitutionDocument(institution=inst, kind=DocumentKind.objects.get(code=kind_code), status=status,
                              uploaded_by=user)
    doc.file = ContentFile(b"%PDF-1.4\n% " + kind_code.encode() + b"\n%%EOF\n", name=f"{kind_code}.pdf")
    if status == "accepted":
        doc.accepted_at = timezone.now()
    doc.save()
    return doc


def complete_ssq(ssq, user):
    """Fill every required answer of a Reaccreditation SSQ."""
    from accreditation import qa_services as svc

    values = {
        "programme_name": "Computer Science", "department_name": "Computing", "head_of_department": "Dr. A. Musa",
        "running_streams": "1",
        "enrolment": [{"session": "2025/26", "year_one": "40", "year_two": "38"}],
        "graduates_last_3": "110", "siwes": "All ND I students placed.",
        "academic_staff": [{"name": "A", "qualification": "MSc", "rank": "Lecturer I", "status": "Full-time"},
                           {"name": "B", "qualification": "BSc", "rank": "Lecturer III", "status": "Full-time"}],
        "nbte_curriculum": "yes", "classrooms": "4", "titles": "800", "weaknesses_addressed": "Labs refurbished.",
    }
    errors = svc.save_fields(ssq, values, user=user)
    assert not errors, errors
    ssq.refresh_from_db()
    assert not svc.submission_problems(ssq), svc.submission_problems(ssq)
    return ssq
