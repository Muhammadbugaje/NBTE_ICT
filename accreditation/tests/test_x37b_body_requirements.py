"""v2.1 R.6 (X37b): professional-body requirements on the SSQ review.

A mandatory requirement that is not met forces a return unless the reviewer
overrides it with a remark; a requirement that needs the body's letter keeps
the programme in ``awaiting_body_letter``.
"""
import pytest
from django.core.exceptions import ValidationError

pytestmark = pytest.mark.django_db


class NS:
    pass


@pytest.fixture
def w(qa_seed, db):
    from accounts.models import User
    from accreditation.models import QARequest, QARequestProgramme, QAType, SSQ, SSQTemplate
    from institutions.models import (BodyRequirement, Institution, Programme, ProgrammeLevel, ProfessionalBody)
    from staff.models import Department

    ns = NS()
    ns.dept = Department.objects.get_or_create(code="dpap", defaults={"name": "DPAP"})[0]
    ns.inst = Institution.unscoped.create(code="ALP", name="Alpha Polytechnic", handling_department=ns.dept,
                                          status="approved")
    ns.body = ProfessionalBody.objects.create(name="Pharmacy Council of Nigeria", acronym="PCN")
    ns.level = ProgrammeLevel.objects.get(code="nd")
    ns.programme = Programme.unscoped.create(institution=ns.inst, name="Pharmacy Technician", level=ns.level,
                                             professional_body=ns.body, status="proposed")
    ns.reviewer = User.objects.create(email="reviewer@nbte.local", first_name="Rev", last_name="Iewer",
                                      user_type="staff", department=ns.dept, is_superuser=True,
                                      must_change_password=False)
    qa_type = QAType.objects.create(code="x37b", name="Accreditation")
    template = SSQTemplate.objects.create(qa_type=qa_type, name="SSQ")
    req = QARequest.unscoped.create(reference="T/X37B/1", institution=ns.inst, qa_type=qa_type,
                                    status="in_workflow")
    row = QARequestProgramme.unscoped.create(qa_request=req, programme=ns.programme)
    ns.ssq = SSQ.unscoped.create(qa_request_programme=row, template=template, status="submitted")
    ns.requirement = BodyRequirement.objects.create(body=ns.body, code="premises", text="Premises registered",
                                                    kind="document", is_mandatory=True)
    return ns


def _check(w, result, remark=""):
    from accreditation import qa_services

    return qa_services.save_requirement_checks(
        w.ssq, user=w.reviewer, results={w.requirement.pk: {"result": result, "remark": remark}})


def test_requirements_are_loaded_for_the_programme_body(w):
    from accreditation import qa_services
    from institutions.models import BodyRequirement, ProgrammeLevel

    assert qa_services.requirements_for(w.ssq) == [w.requirement]
    other_level = ProgrammeLevel.objects.get(code="hnd")
    w.requirement.levels.add(other_level)
    assert qa_services.requirements_for(w.ssq) == []  # requirement is HND-only now
    w.requirement.levels.set([w.level])
    assert qa_services.requirements_for(w.ssq) == [w.requirement]
    BodyRequirement.objects.create(body=w.body, code="off", text="Inactive", is_active=False)
    assert qa_services.requirements_for(w.ssq) == [w.requirement]


def test_mandatory_not_met_needs_a_remark_to_accept(w):
    from accreditation import qa_services

    _check(w, "not_met", "No premises certificate on file.")
    with pytest.raises(ValidationError) as exc:
        qa_services.accept_ssq(w.ssq, user=w.reviewer)
    assert "Premises registered" in str(exc.value)
    accepted = qa_services.accept_ssq(w.ssq, user=w.reviewer, remark="Accepted with a query.")
    assert accepted.status == "accepted"


def test_met_or_not_applicable_accepts_without_a_remark(w):
    from accreditation import qa_services

    _check(w, "met")
    assert qa_services.accept_ssq(w.ssq, user=w.reviewer).status == "accepted"


def test_requires_body_letter_keeps_the_programme_waiting(w):
    from accreditation import qa_services

    w.requirement.requires_body_letter = True
    w.requirement.save(update_fields=["requires_body_letter"])
    _check(w, "partly", "Letter pending.")
    qa_services.accept_ssq(w.ssq, user=w.reviewer, remark="Waiting for PCN.")
    w.programme.refresh_from_db()
    assert w.programme.awaiting_body_letter is True


def test_only_a_reviewer_may_record_results(w):
    from accounts.models import User
    from accreditation import qa_services
    from django.core.exceptions import PermissionDenied

    other = User.objects.create(email="other@nbte.local", first_name="O", last_name="T", user_type="staff",
                                department=w.dept, must_change_password=False)
    with pytest.raises(PermissionDenied):
        qa_services.save_requirement_checks(w.ssq, user=other,
                                            results={w.requirement.pk: {"result": "met"}})
