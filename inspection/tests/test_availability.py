"""Phase 5 — the availability finder (§9 "Available for the next visit") and
the double-booking guard.

Every filter is tested on its own (each demo resource person illustrates one
rule), then the ranking, then two officers booking the same person at once.
"""
import datetime
import threading

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from qa_core.tests.phase3_fixtures import set_config
from qa_core.tests.phase5_fixtures import need

pytestmark = pytest.mark.django_db


def _names(cands):
    return {c.name for c in cands if c.ok}


def _why(cands, name):
    c = next(c for c in cands if c.name == name)
    return {p.rule for p in c.problems}


def test_finder_lists_only_eligible_people(p5):
    from inspection import rules

    cands = rules.find(need(p5), kind="resource_person", include_ineligible=True)
    eligible = _names(cands)
    assert "Engr. Prof. Yakubu Danjuma" in eligible
    for key in ("rp_nominated", "rp_coi", "rp_lowrank", "rp_health", "rp_capped"):
        assert p5.rps[key].full_name not in eligible, key
    only = rules.find(need(p5), kind="resource_person")
    assert all(c.ok for c in only)


def test_filter_status(p5):
    from inspection import rules

    cands = rules.find(need(p5), kind="resource_person", include_ineligible=True)
    assert "status" in _why(cands, "Mrs. Halima Sule")


def test_filter_discipline_and_secondary_setting(p5):
    from inspection import rules

    cands = rules.find(need(p5), kind="resource_person", include_ineligible=True)  # EEE → engineering
    assert "discipline" in _why(cands, "Dr. Ifeoma Eze")
    assert "discipline" in _why(cands, "Dr. Zainab Abdullahi")
    # Computer Science (science) — Yakubu has it only as a secondary discipline.
    cs = rules.find(need(p5, row=p5.cs), kind="resource_person", include_ineligible=True)
    assert "discipline" in _why(cs, "Engr. Prof. Yakubu Danjuma")
    set_config("qa.matching.allow_secondary_discipline", "true")
    cs = rules.find(need(p5, row=p5.cs), kind="resource_person", include_ineligible=True)
    assert "discipline" not in _why(cs, "Engr. Prof. Yakubu Danjuma")


def test_filter_double_booking_with_the_travel_buffer(p5, make_institution):
    from accreditation import case_demo
    from inspection import rules

    rp = p5.rps["rp_eng"]
    rules.book(rp, need(p5, offset=30, days=3), actor=p5.people.desk_officer)  # days 30–32
    set_config("qa.resource_person.max_active_engagements", "5")
    # Same programme (R-11 satisfied), dates 34–35: inside the 2-day buffer → double-booked.
    assert "double_booking" in {p.rule for p in rules.assignment_problems(rp, need(p5, offset=34, days=2))}
    # 36–37: outside the buffer.
    assert "double_booking" not in {p.rule for p in rules.assignment_problems(rp, need(p5, offset=36, days=2))}
    set_config("qa.matching.travel_buffer_days", "5")
    assert "double_booking" in {p.rule for p in rules.assignment_problems(rp, need(p5, offset=36, days=2))}


def test_filter_one_programme_at_a_time_r11(p5):
    from inspection import rules

    rp = p5.rps["rp_eng"]
    set_config("qa.matching.allow_secondary_discipline", "true")
    rules.book(rp, need(p5, row=p5.eee, offset=30), actor=p5.people.desk_officer)
    problems = rules.assignment_problems(rp, need(p5, row=p5.cs, offset=120))
    assert "one_programme" in {p.rule for p in problems}


def test_filter_conflict_of_interest(p5):
    from inspection import rules

    cands = rules.find(need(p5), kind="resource_person", include_ineligible=True)
    assert "conflict_of_interest" in _why(cands, "Mr. Emeka Nwosu")


def test_filter_cooldown(p5):
    from inspection import rules
    from inspection.models import ServiceRecord

    rp = p5.rps["rp_eng"]
    import django.utils.timezone as tz

    served = tz.localdate() - datetime.timedelta(days=200)
    ServiceRecord.unscoped.create(person_type="resource_person", person_id=rp.pk, person_name=rp.full_name,
                                  resource_person=rp, institution=p5.inst, duty="resource_inspection",
                                  served_from=served, served_to=served, visit_count=1, source="backfill")
    assert "cooldown" in {p.rule for p in rules.assignment_problems(rp, need(p5))}
    set_config("qa.matching.same_institution_cooldown_months", "3")
    assert "cooldown" not in {p.rule for p in rules.assignment_problems(rp, need(p5))}


def test_filter_annual_cap(p5):
    from inspection import rules

    cands = rules.find(need(p5), kind="resource_person", include_ineligible=True)
    assert "annual_cap" in _why(cands, "Prof. Bashir Musa")


def test_filter_reviewer_is_not_a_visitor_r02(p5):
    from accreditation.models import SSQ
    from inspection import rules
    from inspection.models import SSQReviewAssignment

    qp = p5.qps["qa_officer2"]
    ssq = SSQ.unscoped.filter(qa_request_programme=p5.eee).first()
    SSQReviewAssignment.unscoped.create(ssq=ssq, reviewer=qp.staff.user)
    problems = rules.assignment_problems(qp, need(p5, duty="physical_verification"))
    assert "reviewer_not_visitor" in {p.rule for p in problems}
    set_config("qa.workload.reviewer_cannot_verify", "false")
    problems = rules.assignment_problems(qp, need(p5, duty="physical_verification"))
    assert "reviewer_not_visitor" not in {p.rule for p in problems}


def test_ranking_fewest_visits_first(p5):
    from inspection import rules
    from inspection.models import ServiceRecord
    import django.utils.timezone as tz

    # Both computing assessors are eligible for CS; give Zainab two visits this year elsewhere.
    zainab = p5.rps["rp_sci"]
    day = tz.localdate() - datetime.timedelta(days=40)
    for _ in range(2):
        ServiceRecord.unscoped.create(person_type="resource_person", person_id=zainab.pk, person_name=zainab.full_name,
                                      resource_person=zainab, institution=None, duty="resource_inspection",
                                      served_from=day, served_to=day, visit_count=1, source="backfill")
    ranked = [c.name for c in rules.find(need(p5, row=p5.cs), kind="resource_person")]
    assert ranked.index("Dr. Chukwuma Obi") < ranked.index("Dr. Zainab Abdullahi")


def test_finder_screen_lists_eligible_people_with_filters(p5, department_mode):
    # The finder's rows resolve through the viewer's institution scope; the
    # deployed department privacy mode (dev DB) shows them to DPAP. A fresh
    # install defaults to assignment mode (v2.1 X1).
    from qa_core.tests.phase4_fixtures import staff_client

    c = staff_client(p5.people.desk_officer)
    url = (f"/qa/inspection/finder/?case={p5.case.pk}&row={p5.eee.pk}&duty=resource_inspection"
           f"&start={need(p5).start.isoformat()}&end={need(p5).end.isoformat()}")
    r = c.get(url)
    body = r.content.decode()
    assert r.status_code == 200
    assert "Engr. Prof. Yakubu Danjuma" in body and "Prof. Bashir Musa" not in body
    r = c.get(url + "&show=all")
    assert "Prof. Bashir Musa" in r.content.decode() and "max_visits_per_year" in r.content.decode()


# ---------------------------------------------------------------------------
# Double booking: two officers at once — only one wins
# ---------------------------------------------------------------------------
def test_two_officers_with_the_same_view_only_one_wins(p5):
    """Both officers loaded the finder (same booking version); both press
    Map. The first save bumps the version; the second is refused."""
    from inspection import rules
    from inspection.models import QAMapping

    rp = p5.rps["rp_eng"]
    seen = rp.booking_version
    rules.book(rp, need(p5), actor=p5.people.desk_officer, expected_version=seen)
    with pytest.raises(rules.BookingConflict):
        rules.book(rp, need(p5, offset=200), actor=p5.people.busy_officer, expected_version=seen)
    assert QAMapping.unscoped.filter(resource_person=rp).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_double_booking_threads_only_one_wins(qa_seed, settings):
    """Two threads book the same person for the same dates at the same moment."""
    from accreditation import case_demo
    from accreditation.models import QARequestProgramme
    from inspection import demo, rules
    from inspection.models import QAMapping

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    demo.ensure_designations()
    inst = case_demo.ensure_institution(tag=False)
    people, _ = case_demo.ensure_accounts(force_change=False, tag=False)
    case = case_demo.create_case(people, inst)
    rps = demo.ensure_resource_persons(people, inst, tag=False)
    row = QARequestProgramme.unscoped.get(qa_request=case.qa_request,
                                          programme__name="Electrical/Electronic Engineering Technology")
    rp = rps["rp_eng"]
    import django.utils.timezone as tz

    start = tz.localdate() + datetime.timedelta(days=30)
    the_need = rules.Need(case, row, "resource_inspection", start, start + datetime.timedelta(days=2))
    barrier = threading.Barrier(2)
    outcomes = []

    def officer(actor):
        try:
            barrier.wait(timeout=10)
            rules.book(rp, the_need, actor=actor)
            outcomes.append("won")
        except Exception as exc:  # refused: a rule, the version check or the database lock
            outcomes.append(type(exc).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=officer, args=(u,)) for u in (people.desk_officer, people.busy_officer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert outcomes.count("won") == 1, outcomes
    assert QAMapping.unscoped.filter(resource_person=rp).count() == 1


def test_confirmation_rechecks_under_the_lock(p5):
    """A proposed mapping whose person became unavailable meanwhile is not
    activated at confirmation."""
    from inspection import rules
    from qa_core.tests.phase3_fixtures import set_config as cfg

    rp = p5.rps["rp_eng"]
    mapping = rules.book(rp, need(p5), actor=p5.people.desk_officer)
    cfg("qa.resource_person.max_visits_per_year", "0")
    with pytest.raises(ValidationError):
        rules.activate(mapping)
