"""Shared fixtures for the Phase 4 tests (SOP-03 cases and the task bridge).
Import with ``from qa_core.tests.phase4_fixtures import *`` after the
Phase 1–3 fixtures.

The case is built exactly as ``manage.py seed_phase4_demo`` builds it: a real
QA request (fee waived, SSQs submitted) → QA case → SOP-03 flow → tasks.
"""
import datetime
from types import SimpleNamespace

import pytest
from django.test import Client
from django.utils import timezone

from qa_core.tests.phase3_fixtures import set_config

__all__ = ["p4", "set_config", "staff_client", "act", "holder", "open_link", "open_stage_codes", "second_case",
           "advance_to"]


def staff_client(user):
    c = Client(HTTP_HOST="localhost")
    c.force_login(user)
    return c


@pytest.fixture
def p4(qa_seed, settings):
    from accreditation import case_demo

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    inst = case_demo.ensure_institution(tag=False)
    people, creds = case_demo.ensure_accounts(force_change=False, tag=False)
    case = case_demo.create_case(people, inst)
    return SimpleNamespace(case=case, people=people, inst=inst, creds=creds)


def open_link(case, stage_code):
    from accreditation import case_services as cs

    return cs.open_link(case, stage_code)


def holder(case, stage_code):
    from accreditation import case_services as cs

    people = cs.holders(case, stage_code)
    return people[0] if people else None


def open_stage_codes(case):
    from accreditation import case_services as cs

    return sorted(link.stage_code for link in cs.case_links(case, open_only=True))


def act(case, stage, action="approve", remark="", user=None):
    """Approve / return through the service as the stage's task holder."""
    from accreditation import case_services as cs

    user = user or holder(case, stage)
    return cs.act(case, user=user, stage_code=stage, action=action, remark=remark)


def advance_to(p4, number, case=None):
    from accreditation import case_demo

    return case_demo.advance(case or p4.case, p4.people, number)


def second_case(p4, code="KD2"):
    """A second institution with one programme and its own started case."""
    from accreditation import case_demo
    from institutions.models import Institution, Programme, ProgrammeCategory, ProgrammeLevel
    from qa_core.departments import qa_department

    inst = Institution.unscoped.create(code=code, name=f"{code} Demo Polytechnic", status="approved",
                                       handling_department=qa_department("dpap"))
    today = timezone.localdate()
    Programme.unscoped.create(institution=inst, name="Statistics", level=ProgrammeLevel.objects.get(code="nd"),
                              category=ProgrammeCategory.objects.get(name="Computing & Information Technology"),
                              status="accredited", approved_streams=1,
                              commencement_date=today.replace(year=today.year - 4),
                              expiry_date=today + datetime.timedelta(days=100))
    from accounts.models import Role, User

    admin = User(email=f"admin@{code.lower()}.local", first_name="Admin", last_name=code, user_type="institution",
                 institution=inst, role=Role.objects.get(code="inst-admin"))
    admin.set_password("x-Test-pass-2026!")
    admin.save()
    people = type(p4.people)(p4.people)
    people["institution_admin"] = admin
    return case_demo.create_case(people, inst)
