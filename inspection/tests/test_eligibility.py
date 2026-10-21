"""Phase 5 — eligibility (Master Plan §9 "Eligibility rules", v1.1).

The seven rules of the §9 table, each enforced at nomination and at
assignment time, server-side, with no override path (not even a superuser).
"""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from qa_core.tests.phase3_fixtures import set_config
from qa_core.tests.phase5_fixtures import need

pytestmark = pytest.mark.django_db


def _rules(problems):
    return {p.rule for p in problems}


# ---------------------------------------------------------------------------
# Rule 1 — minimum seniority (qa.resource_person.min_rank)
# ---------------------------------------------------------------------------
def test_rule1_min_rank_blocks_staff_below_the_role(p5):
    from inspection import rules

    officer_staff = p5.people.desk_officer.staff_profile
    assert "min_rank" in _rules(rules.staff_problems(officer_staff))
    assert not rules.staff_problems(p5.people.team_lead.staff_profile)
    set_config("qa.resource_person.min_rank", "officer")  # the only lever: the setting
    assert "min_rank" not in _rules(rules.staff_problems(officer_staff))


def test_rule1_refused_at_qa_personnel_registration(p5):
    from qa_core import actions

    with pytest.raises(ValidationError) as exc:
        actions.initiate("inspection.edit_qa_personnel", actor=p5.people.desk_officer, target=None,
                         payload={"staff_id": p5.people.busy_officer.staff_profile.pk,
                                  "discipline_ids": [p5.eee.programme.category.discipline_id]},
                         reason="Register for QA duty")
    assert "qa.resource_person.min_rank" in " ".join(exc.value.messages)


def test_rule1_refused_at_nomination_of_an_nbte_staff_resource_person(p5):
    from inspection import services

    with pytest.raises(ValidationError) as exc:
        services.nominate({"full_name": "Busy Officer", "email": "busy.rp@nbte.local", "source": "nbte_staff",
                           "staff": p5.people.busy_officer.staff_profile.pk,
                           "disciplines": [p5.eee.programme.category.discipline_id]}, actor=p5.people.desk_officer)
    assert "min_rank" in str(exc.value) or "rank" in str(exc.value)


# ---------------------------------------------------------------------------
# Rule 2 — allowed posts / ranks (qa.resource_person.required_designations)
# ---------------------------------------------------------------------------
def test_rule2_external_rank_must_be_listed_at_nomination(p5):
    from inspection import services

    data = {"full_name": "Mr. New Lecturer", "email": "new.lecturer@demo.nbte.local", "source": "academia",
            "rank": "lecturer-ii", "disciplines": [p5.eee.programme.category.discipline_id]}
    with pytest.raises(ValidationError) as exc:
        services.nominate(data, actor=p5.people.desk_officer)
    assert "rank" in exc.value.message_dict
    data["rank"] = "chief-lecturer"
    rp = services.nominate(data, actor=p5.people.desk_officer)
    assert rp.status == "nominated"


def test_rule2_staff_designation_must_be_listed(p5):
    from inspection import rules

    staff = p5.people.division_head.staff_profile  # no designation on record
    assert "designation" in _rules(rules.staff_problems(staff))


def test_falling_below_later_blocks_new_work_and_shows_the_badge(p5):
    from inspection import rules

    low = p5.rps["rp_lowrank"]  # active, but rank lecturer-i is not in the list
    assert rules.no_longer_eligible(low)
    assert "designation" in _rules(rules.assignment_problems(low, need(p5)))
    assert not rules.no_longer_eligible(p5.rps["rp_eng"])


# ---------------------------------------------------------------------------
# Rule 3 — concurrent load (qa.resource_person.max_active_engagements, R-11)
# ---------------------------------------------------------------------------
def test_rule3_concurrent_load(p5):
    from inspection import rules

    set_config("qa.resource_person.max_active_engagements", "1")
    rp = p5.rps["rp_eng"]
    rules.book(rp, need(p5, offset=30), actor=p5.people.desk_officer)
    problems = rules.assignment_problems(rp, need(p5, offset=90))  # same programme, far apart in time
    assert "one_programme" in _rules(problems)
    assert any(p.setting == "qa.resource_person.max_active_engagements" for p in problems)


# ---------------------------------------------------------------------------
# Rule 4 — open SSQ reviews (qa.resource_person.max_open_ssq_per_officer, R-01)
# ---------------------------------------------------------------------------
def test_rule4_open_ssq_reviews(p5):
    from accreditation.models import SSQ
    from inspection import rules
    from inspection.models import SSQReviewAssignment

    set_config("qa.resource_person.max_open_ssq_per_officer", "1")
    qp = p5.qps["qa_officer2"]
    ssq = SSQ.unscoped.filter(qa_request_programme=p5.cs).first()
    SSQReviewAssignment.unscoped.create(ssq=ssq, reviewer=qp.staff.user)
    problems = rules.assignment_problems(qp, need(p5, row=p5.cs, duty="ssq_review"))
    assert "open_ssq" in _rules(problems)


# ---------------------------------------------------------------------------
# Rule 5 — annual visits (qa.resource_person.max_visits_per_year)
# ---------------------------------------------------------------------------
def test_rule5_annual_cap(p5):
    from inspection import rules

    capped = p5.rps["rp_capped"]
    assert "annual_cap" in _rules(rules.assignment_problems(capped, need(p5)))
    set_config("qa.resource_person.max_visits_per_year", "10")
    assert "annual_cap" not in _rules(rules.assignment_problems(capped, need(p5)))


# ---------------------------------------------------------------------------
# Rule 6 — Institutional Admin's department (qa.institution.admin_required_department)
# ---------------------------------------------------------------------------
def test_rule6_institutional_admin_department(p5):
    from institutions import services

    set_config("qa.institution.admin_required_department", "Human Resources")
    base = {"first_name": "Ade", "last_name": "Admin", "email": "ade.admin@p4demo.local", "role": "inst-admin",
            "grade_level": 13}
    with pytest.raises(ValidationError) as exc:
        services.invite_user(p5.inst, {**base, "institution_department": "Bursary"}, actor=p5.people.institution_admin)
    assert "institution_department" in exc.value.message_dict
    user = services.invite_user(p5.inst, {**base, "institution_department": "human resources"},
                                actor=p5.people.institution_admin)
    assert user.institution_membership.institution_department == "human resources"


def test_rule6_at_add_tvet(p5, lookups, add_tvet_payload):
    from qa_core import actions

    set_config("qa.institution.admin_required_department", "Human Resources")
    payload = add_tvet_payload()
    with pytest.raises(ValidationError):
        actions.initiate("institutions.add_tvet", actor=p5.people.desk_officer, payload=payload, reason="New TVET")


# ---------------------------------------------------------------------------
# Rule 7 — the Chief Executive is the actual head (qa.institution.chief_executive_required_position)
# ---------------------------------------------------------------------------
def test_rule7_chief_executive_must_be_the_head(p5):
    from institutions import services
    from institutions.models import InstitutionContact

    InstitutionContact.unscoped.create(institution=p5.inst, kind="head_of_institution", name="Dr. Musa Rector",
                                       value="rector@p4demo.local")
    base = {"role": "inst-chief-executive", "grade_level": None}
    with pytest.raises(ValidationError):
        services.invite_user(p5.inst, {**base, "first_name": "Someone", "last_name": "Else",
                                       "email": "else@p4demo.local"}, actor=p5.people.institution_admin)
    user = services.invite_user(p5.inst, {**base, "first_name": "Musa", "last_name": "Rector",
                                          "email": "rector@p4demo.local"}, actor=p5.people.institution_admin)
    assert user.pk
    set_config("qa.institution.chief_executive_required_position", "false")
    services.invite_user(p5.inst, {**base, "first_name": "Deputy", "last_name": "Rector",
                                   "email": "deputy@p4demo.local"}, actor=p5.people.institution_admin)


# ---------------------------------------------------------------------------
# No override
# ---------------------------------------------------------------------------
def test_no_override_even_for_a_superuser(p5, make_user):
    from inspection import rules

    root = make_user("root@nbte.local", superuser=True)
    with pytest.raises(ValidationError):
        rules.book(p5.rps["rp_lowrank"], need(p5), actor=root)
    with pytest.raises(ValidationError):
        rules.book(p5.rps["rp_capped"], need(p5), actor=root)


def test_no_override_permission_exists():
    from django.contrib.auth.models import Permission

    assert not Permission.objects.filter(codename__icontains="override_eligib").exists()


# ---------------------------------------------------------------------------
# Vetting
# ---------------------------------------------------------------------------
def test_vetting_flow(p5):
    from inspection import services

    rp = p5.rps["rp_nominated"]
    with pytest.raises(PermissionDenied):  # the nominator cannot vet
        services.vet(rp, actor=p5.people.desk_officer, approve=True)
    with pytest.raises(ValidationError):  # rejecting needs a reason
        services.vet(rp, actor=p5.people.director, approve=False)
    rp = services.vet(rp, actor=p5.people.director, approve=True, remark="CV and promotion letter checked")
    assert rp.status == "active" and rp.vetted_by == p5.people.director


def test_vetting_needs_a_cv(p5):
    from inspection import services

    rp = services.nominate({"full_name": "Prof. No CV", "email": "nocv@demo.nbte.local", "source": "academia",
                            "rank": "chief-lecturer", "disciplines": [p5.eee.programme.category.discipline_id]},
                           actor=p5.people.desk_officer)
    with pytest.raises(ValidationError):
        services.vet(rp, actor=p5.people.director, approve=True)


def test_nightly_reconciliation_lists_people_no_longer_eligible(p5):
    from qa_core.task_bridge import reconcile

    report = reconcile(dry_run=True)
    lapsed = [d for d in report.details if d["kind"] == "no longer eligible"]
    assert any(p5.rps["rp_lowrank"].full_name in d["subject"] for d in lapsed)
    assert not any(p5.rps["rp_eng"].full_name in d["subject"] for d in lapsed)


def test_rule1_and_rule2_refused_at_assignment_for_qa_personnel(p5):
    """A QA officer registered earlier whose rank / designation no longer
    qualifies cannot be mapped (existing work continues)."""
    from inspection import rules
    from inspection.models import QAPersonnel

    staff = p5.people.desk_officer.staff_profile  # an officer: below qa.resource_person.min_rank
    qp = QAPersonnel.objects.create(staff=staff, is_verification_officer=True, registered_by=p5.people.director)
    qp.disciplines.set([p5.eee.programme.category.discipline])
    problems = {p.rule for p in rules.assignment_problems(qp, need(p5, duty="physical_verification"))}
    assert {"min_rank", "designation"} <= problems
    assert rules.no_longer_eligible(qp)
    with pytest.raises(ValidationError):
        rules.book(qp, need(p5, duty="physical_verification"), actor=p5.people.director)
