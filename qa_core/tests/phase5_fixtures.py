"""Shared fixtures for the Phase 5 tests (inspection and resource persons).

The demo case is built like ``seed_phase5_demo`` builds it: Phase 4's real
case, the Phase 5 people (Team Lead, Division Head, Deputy Director),
resource persons illustrating each §9 rule, QA personnel.
"""
import datetime
from types import SimpleNamespace

import pytest
from django.utils import timezone

__all__ = ["p5", "p5_visit", "need", "visit_dates"]


def visit_dates(offset=30, days=3):
    start = timezone.localdate() + datetime.timedelta(days=offset)
    return start, start + datetime.timedelta(days=days - 1)


@pytest.fixture
def p5(qa_seed, settings):
    from accreditation import case_demo
    from inspection import demo

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    demo.ensure_designations()
    # v2.1 pins qa.resource_person.min_rank = director for the live system, but
    # the demo cast's QA personnel are deputy directors (rank 80); with the
    # director minimum they would be ineligible for every rule/finder test.
    # Pin a scale where the cast works.
    from qa_core.tests.phase3_fixtures import set_config

    set_config("qa.resource_person.min_rank", "deputy-director")
    inst = case_demo.ensure_institution(tag=False)
    people, creds = case_demo.ensure_accounts(force_change=False, tag=False)
    case = case_demo.create_case(people, inst)
    rps = demo.ensure_resource_persons(people, inst, tag=False)
    qps = demo.ensure_qa_personnel(people)
    demo.backfill_capped(rps["rp_capped"], tag=False)
    from accreditation.models import QARequestProgramme

    rows = {row.programme.name: row for row in QARequestProgramme.unscoped.filter(
        qa_request=case.qa_request, status="active").select_related("programme__category__discipline")}
    return SimpleNamespace(case=case, people=people, inst=inst, rps=rps, qps=qps, rows=rows,
                           eee=rows["Electrical/Electronic Engineering Technology"], cs=rows["Computer Science"])


def need(p5, row=None, duty="resource_inspection", offset=30, days=3, case=None):
    from inspection import rules

    start, end = visit_dates(offset, days)
    return rules.Need(case or p5.case, row or p5.eee, duty, start, end)


@pytest.fixture
def p5_visit(p5):
    """The p5 case driven like ``seed_phase5_demo`` drives it: visit dates
    (stage 8), the two assessors mapped and confirmed, the ES approval
    (inspection created), funds released → stage 12. The visit itself is not
    yet recorded (tests record it, with or without absentees)."""
    from accreditation import case_demo
    from accreditation import case_services as cs
    from inspection import demo, services

    with cs.pinned_actors(case_demo.demo_pins(p5.people)):
        case = case_demo.advance(p5.case, p5.people, 9)
        p5.mappings = demo.map_demo_people(case, p5.people, p5.rps)
        case = case_demo.advance(case, p5.people, 12)
    p5.case = case
    p5.inspection = services.live_inspection(case)
    return p5
