"""Phase 4 — SOP-03 internal workflow and Task Bounce (PHASES.md Phase 4;
Master Plan §5.4, §5.7, §8).

The 17 stages on the FlowDefinition engine; the parallel stage-1 gate (two ICT
officers for big cases, R-04); return with a mandatory remark to the exact
submitter; one task per stage (created on entry, closed by the QA action,
never completable from Tasks); reassignment re-validated against R-01, R-02
and R-20 with rollback; the ICT monitor's read-only access; SLA reminders;
the nightly reconciliation; Send Back To Audit; and the screens.
"""
import io

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command

from qa_core.tests.phase4_fixtures import (
    act, advance_to, staff_client, holder, open_link, open_stage_codes, second_case, set_config,
)

pytestmark = pytest.mark.django_db

LINEAR = ["generate_cost", "add_personnel", "confirm_personnel", "audit_personnel_cost", "confirm_audit_cost",
          "es_approve_cost", "add_visit_details", "confirm_visit_details", "es_approve_visit",
          "request_fund_release", "post_visit_report", "post_visit_recommendation", "cqac_recommendation",
          "cqac_chair_approval", "es_final_approval"]


def _profile(Staff, user, employee_no, grade):
    """Give ``user`` a staff profile on ``grade`` (a profile may already exist)."""
    profile = Staff.objects.filter(user=user).first()
    if profile is None:
        profile = Staff(employee_no=employee_no, first_name=user.first_name, last_name=user.last_name,
                        email=user.email, department=user.department, user=user)
    profile.grade_level = grade
    profile.save()
    return profile


def _stages(case):
    from accreditation import case_services as cs

    case.refresh_from_db()
    return cs.current_stages(case)


# ---------------------------------------------------------------------------
# The flow definition (no hard-coded state machine)
# ---------------------------------------------------------------------------
def test_qa_case_flow_is_seeded_on_the_engine(qa_seed):
    from qa_core.models import FlowDefinition, FlowTransition

    flow_def = FlowDefinition.objects.filter(code="qa_case", published_at__isnull=False).order_by("-version").first()
    assert flow_def is not None and flow_def.subject_model == "accreditation.QACase"
    numbers = sorted({s.sop_stage_no for s in flow_def.stages.all() if s.sop_stage_no})
    assert numbers == list(range(1, 18))
    gate = flow_def.stages.get(code="assign_supervising_staff")
    assert gate.kind == "parallel_gate"
    assert set(FlowTransition.objects.filter(from_stage=gate).values_list("to_stage__code", flat=True)) == {
        "assign_audit_officer", "assign_finance_officer", "assign_ict_officer"}
    # Every action stage has a return that needs a remark and goes back one node.
    for code in LINEAR:
        ret = FlowTransition.objects.get(from_stage__flow=flow_def, from_stage__code=code, action_code="return")
        assert ret.requires_remark
    assert flow_def.stages.get(code="programme_auto_update").kind == "system"


# ---------------------------------------------------------------------------
# Starting a case; stage 1 parallel gate
# ---------------------------------------------------------------------------
def test_case_starts_with_a_task_for_the_owning_director(p4):
    from tasks.models import Task

    case = p4.case
    assert case.status == "case_opened" and case.started_at is not None
    link = open_link(case, "case_opened")
    assert link is not None and holder(case, "case_opened") == p4.people.director
    task = Task.objects.get(pk=link.task_id)
    assert task.category.code == "qa-sop03" and task.created_by.email == "qa-system@nbte.local"
    assert task.state == Task.OPEN and link.due_at is not None


def test_stage_1_opens_three_parallel_branches_with_a_task_each(p4):
    from accreditation import case_services as cs

    case = p4.case
    with pytest.raises(ValidationError):  # no desk officer yet
        act(case, "case_opened")
    cs.set_desk_officer(case, user=p4.people.director, officer=p4.people.desk_officer)
    act(case, "case_opened")
    assert set(_stages(case)) == {"assign_audit_officer", "assign_finance_officer", "assign_ict_officer"}
    assert case.status == "assign_supervising_staff"
    assert open_stage_codes(case) == ["assign_audit_officer", "assign_finance_officer", "assign_ict_officer"]
    assert holder(case, "assign_audit_officer") == p4.people.audit_director
    assert holder(case, "assign_finance_officer") == p4.people.finance_director
    assert holder(case, "assign_ict_officer") == p4.people.ict_director
    # Branches finish in any order; the join waits for all three.
    cs.set_supervisor(case, user=p4.people.ict_director, kind="ict", officers=[p4.people.ict_officer])
    act(case, "assign_ict_officer")
    assert set(_stages(case)) == {"assign_audit_officer", "assign_finance_officer"}
    cs.set_supervisor(case, user=p4.people.audit_director, kind="audit", officers=[p4.people.audit_officer])
    act(case, "assign_audit_officer")
    cs.set_supervisor(case, user=p4.people.finance_director, kind="finance", officers=[p4.people.finance_officer])
    act(case, "assign_finance_officer")
    assert _stages(case) == ["generate_cost"]
    assert holder(case, "generate_cost") == p4.people.desk_officer
    assert open_stage_codes(case) == ["generate_cost"]


def test_big_cases_need_two_ict_officers_r04(qa_seed, settings):
    """The 5+ programme branch: with the threshold at 2, this 2-programme case
    needs two tracking ICT officers before the ICT branch can be approved."""
    from accreditation import case_demo, case_services as cs

    set_config("qa.assignment.second_ict_officer_threshold", "2")
    inst = case_demo.ensure_institution(tag=False)
    people, _ = case_demo.ensure_accounts(force_change=False, tag=False)
    case = case_demo.create_case(people, inst)
    assert case.requires_second_ict_officer and cs.ict_required(case) == 2
    cs.set_desk_officer(case, user=people.director, officer=people.desk_officer)
    cs.act(case, user=people.director, stage_code="case_opened", action="approve")
    with pytest.raises(ValidationError):
        cs.set_supervisor(case, user=people.ict_director, kind="ict", officers=[people.ict_officer])
    with pytest.raises(ValidationError):  # the prerequisite blocks approve too
        cs.act(case, user=people.ict_director, stage_code="assign_ict_officer", action="approve")
    cs.set_supervisor(case, user=people.ict_director, kind="ict", officers=[people.ict_officer, people.ict_officer2])
    cs.act(case, user=people.ict_director, stage_code="assign_ict_officer", action="approve")
    case.refresh_from_db()
    assert case.tracking_ict_officers.count() == 2
    assert "assign_ict_officer" not in cs.current_stages(case)


def test_small_cases_need_one_ict_officer(p4):
    from accreditation import case_services as cs

    assert not p4.case.requires_second_ict_officer and cs.ict_required(p4.case) == 1


# ---------------------------------------------------------------------------
# The 17-stage walk
# ---------------------------------------------------------------------------
def test_seventeen_stage_walk_with_approvals(p4):
    from accreditation import case_services as cs
    from accreditation.models import CostSheet, FundRelease, PersonnelList, VisitDetail
    from qa_core.models import QATaskLink
    from tasks.models import Task

    case = advance_to(p4, 2)
    expected_holder = {
        "generate_cost": p4.people.desk_officer, "add_personnel": p4.people.desk_officer,
        "confirm_personnel": p4.people.director, "audit_personnel_cost": p4.people.audit_officer,
        "confirm_audit_cost": p4.people.audit_director, "es_approve_cost": p4.people.es,
        "add_visit_details": p4.people.desk_officer, "confirm_visit_details": p4.people.director,
        "es_approve_visit": p4.people.es, "request_fund_release": p4.people.pay_officer,
        "post_visit_report": None,  # Phase 5: no case task — the report sub-flow (one per programme) runs it
        "post_visit_recommendation": p4.people.director,
        "cqac_recommendation": p4.people.cqac_member, "cqac_chair_approval": p4.people.cqac_chair,
        "es_final_approval": p4.people.es,
    }
    for number, code in enumerate(LINEAR, start=2):
        assert _stages(case) == [code], code
        assert case.status == code
        assert holder(case, code) == expected_holder[code], code
        link = open_link(case, code)
        case = advance_to(p4, number + 1, case)
        if code == "post_visit_report":  # Phase 5: advanced by the last Director endorsement
            assert link is None
            continue
        link.refresh_from_db()
        assert link.closed_at is not None and link.close_reason == "approved", code
        assert Task.objects.get(pk=link.task_id).state == Task.COMPLETED, code
    case.refresh_from_db()
    assert case.status == "closed" and case.completed_at is not None
    sheet = cs.current_sheet(case)
    assert sheet.status == "es_approved" and sheet.approved_by == p4.people.es and sheet.total > 0
    assert PersonnelList.unscoped.get(case=case).status == "approved"
    assert VisitDetail.unscoped.get(case=case).status == "es_approved"
    release = FundRelease.unscoped.get(case=case)
    assert release.status == "released" and release.amount == sheet.total and release.released_reference
    assert CostSheet.unscoped.filter(case=case).count() == 1
    from django.contrib.contenttypes.models import ContentType

    from accreditation.models import QACase

    links = QATaskLink.objects.filter(content_type=ContentType.objects.get_for_model(QACase), object_id=case.pk)
    # 18 stage tasks (stage 12 has none since Phase 5: the post-visit reports carry their own tasks)
    assert links.count() == 18 and not links.filter(closed_at__isnull=True).exists()


def test_seventeen_stage_walk_through_the_screens(p4):
    """The same walk, every action posted to the case screen by the task holder."""
    from accreditation import case_services as cs
    from accreditation.models import RateTable

    case, people = p4.case, p4.people
    url = f"/qa/cases/{case.pk}/"

    def post(user, path, data):
        r = staff_client(user).post(url + path, data, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
        assert r.status_code == 200, (path, r.content.decode()[:2000])
        return r

    post(people.director, "desk-officer/", {"officer": people.desk_officer.pk})
    post(people.director, "act/", {"stage": "case_opened", "action": "approve"})
    post(people.audit_director, "supervisor/audit/", {"officers": [people.audit_officer.pk]})
    post(people.audit_director, "act/", {"stage": "assign_audit_officer", "action": "approve"})
    post(people.finance_director, "supervisor/finance/", {"officers": [people.finance_officer.pk]})
    post(people.finance_director, "act/", {"stage": "assign_finance_officer", "action": "approve"})
    post(people.ict_director, "supervisor/ict/", {"officers": [people.ict_officer.pk]})
    post(people.ict_director, "act/", {"stage": "assign_ict_officer", "action": "approve"})
    assert _stages(case) == ["generate_cost"]
    rate = RateTable.objects.get(code="dta-officer", department__isnull=True)
    r = post(people.desk_officer, "cost/lines/", {"rate": rate.pk, "days": "3", "role_on_exercise": "Member"})
    assert "150,000" in r.content.decode()
    post(people.desk_officer, "act/", {"stage": "generate_cost", "action": "approve"})
    post(people.desk_officer, "personnel/", {"staff": people.director.staff_profile.pk, "duty": "team_lead"})
    post(people.desk_officer, "act/", {"stage": "add_personnel", "action": "approve"})
    post(people.director, "act/", {"stage": "confirm_personnel", "action": "approve"})
    line = cs.case_bundle(case)["lines"][0]
    post(people.audit_officer, f"cost/lines/{line.pk}/", {"op": "save", "days": "2", "note": "Two nights only"})
    post(people.audit_officer, "act/", {"stage": "audit_personnel_cost", "action": "approve"})
    post(people.audit_director, "act/", {"stage": "confirm_audit_cost", "action": "approve"})
    post(people.es, "act/", {"stage": "es_approve_cost", "action": "approve"})
    import datetime

    start = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    end = (datetime.date.today() + datetime.timedelta(days=32)).isoformat()
    post(people.desk_officer, "visit/", {"planned_start": start, "planned_end": end,
                                         "team_lead": people.director.staff_profile.pk, "notes": "By road"})
    post(people.desk_officer, "act/", {"stage": "add_visit_details", "action": "approve"})
    post(people.director, "act/", {"stage": "confirm_visit_details", "action": "approve"})
    post(people.es, "act/", {"stage": "es_approve_visit", "action": "approve"})
    post(people.pay_officer, "release/", {"reference": "PV/TEST/0001"})
    post(people.pay_officer, "act/", {"stage": "request_fund_release", "action": "approve"})
    # Phase 5: stage 12 is the report sub-flow (Team Lead → Division Head → Deputy
    # Director → Director per programme); stage 13 needs a recommendation per programme.
    from inspection import demo as inspection_demo
    from inspection import services as insp

    inspection_demo.complete_visit(case)
    inspection_demo.drive_reports(case)
    assert _stages(case) == ["post_visit_recommendation"]
    for report in insp.reports_of(case):
        post(holder(case, "post_visit_recommendation"), f"reports/{report.pk}/recommend/",
             {"recommendation": "accredit", "note": "Meets the minimum standard"})
    for code in LINEAR[11:]:
        post(holder(case, code), "act/", {"stage": code, "action": "approve"})
    case.refresh_from_db()
    assert case.status == "closed"
    sheet = cs.current_sheet(case)
    assert sheet.total == 100000 and cs.case_bundle(case)["lines"][0].audit_note == "Two nights only"


# ---------------------------------------------------------------------------
# Returns: remark required, back to the exact submitter, remark pinned
# ---------------------------------------------------------------------------
def test_return_needs_a_remark(p4):
    case = advance_to(p4, 3)
    with pytest.raises(ValidationError):
        act(case, "add_personnel", "return", remark="")
    assert _stages(case) == ["add_personnel"]


def test_return_from_stage_5_goes_to_stage_4_with_the_remark_pinned(p4):
    from tasks.models import Task

    case = advance_to(p4, 5)
    assert _stages(case) == ["audit_personnel_cost"]
    old = open_link(case, "audit_personnel_cost")
    act(case, "audit_personnel_cost", "return", remark="The driver is missing from the personnel list")
    assert _stages(case) == ["confirm_personnel"]
    old.refresh_from_db()
    assert old.closed_at is not None and old.close_reason == "returned"
    link = open_link(case, "confirm_personnel")
    assert holder(case, "confirm_personnel") == p4.people.director  # the one who confirmed stage 4
    task = Task.objects.get(pk=link.task_id)
    pinned = list(task.comments.filter(is_pinned=True))
    assert pinned and "The driver is missing from the personnel list" in pinned[0].body
    assert task.priority == "high"


def test_every_stage_returns_to_the_exact_submitter(p4):
    """Walk 2→16; at each stage return once (checking who gets it back and the
    pinned remark), have it re-approved, then approve the stage."""
    from accreditation import case_services as cs
    from tasks.models import Task

    case = advance_to(p4, 2)
    submitter = {}  # stage → who approved it last
    for i, code in enumerate(LINEAR):
        # Phase 5: stage 12 has no case task (its returns are per report, see
        # inspection/tests/test_reports.py) and stage 13 returns to the reports.
        if i >= 1 and code not in ("post_visit_report", "post_visit_recommendation"):
            previous = LINEAR[i - 1]
            remark = f"Please redo {previous}"
            act(case, code, "return", remark=remark)
            assert _stages(case) == [previous], code
            assert holder(case, previous) == submitter[previous], code
            task = Task.objects.get(pk=open_link(case, previous).task_id)
            assert any(remark in c.body for c in task.comments.filter(is_pinned=True)), code
            act(case, previous, "approve")
            assert _stages(case) == [code], code
        who = holder(case, code)
        submitter[code] = who
        advance_to(p4, LINEAR.index(code) + 3, case)
    case.refresh_from_db()
    assert case.status == "closed"
    assert not cs.case_links(case, open_only=True)


def test_return_from_stage_2_goes_back_to_the_director_and_resubmits(p4):
    from accreditation import case_services as cs

    case = advance_to(p4, 2)
    act(case, "generate_cost", "return", remark="Choose another desk officer, please")
    assert _stages(case) == ["case_opened"]
    assert holder(case, "case_opened") == p4.people.director
    act(case, "case_opened")  # supervisors are kept: straight back to stage 2
    assert _stages(case) == ["generate_cost"]
    assert cs.variables(case)["supervisors_ready"] == 1


def test_branch_return_goes_back_to_the_department(p4):
    from accreditation import case_services as cs

    case = advance_to(p4, 1)
    act(case, "assign_finance_officer", "return", remark="This case belongs to another division")
    assert _stages(case) == ["case_opened"]
    assert open_stage_codes(case) == ["case_opened"]  # the other branches' tasks were closed too
    assert cs.can_act(case, p4.people.director, "case_opened")[0]


# ---------------------------------------------------------------------------
# Task Bounce: tasks close with the QA action and cannot be finished in Tasks
# ---------------------------------------------------------------------------
def test_task_closes_when_the_qa_action_is_taken(p4):
    from tasks.models import Task

    case = advance_to(p4, 3)
    link = open_link(case, "add_personnel")
    task = Task.objects.get(pk=link.task_id)
    assert task.state in Task.ACTIVE_STATES
    advance_to(p4, 4, case)
    task.refresh_from_db()
    link.refresh_from_db()
    assert task.state == Task.COMPLETED and link.closed_at is not None
    assert open_link(case, "confirm_personnel").task_id != task.pk


def test_linked_task_cannot_be_completed_or_cancelled_from_tasks(p4):
    from tasks import services
    from tasks.models import Task

    case = advance_to(p4, 2)
    officer = p4.people.desk_officer
    task = Task.objects.get(pk=open_link(case, "generate_cost").task_id)
    services.accept_task(task, officer)
    task.refresh_from_db()
    with pytest.raises(PermissionDenied):
        services.mark_assignee_done(task, officer)
    with pytest.raises(PermissionDenied):
        services.cancel_task(task, p4.people.director, reason="Not needed any more")
    task.refresh_from_db()
    assert task.state not in Task.CLOSED_STATES
    # The screen offers "Open in QA" and hides "Mark done".
    r = staff_client(officer).get(f"/tasks/{task.pk}/")
    body = r.content.decode()
    assert r.status_code == 200 and "Open in QA" in body and f"/qa/cases/{case.pk}/?stage=generate_cost" in body
    assert "tasks/{}/mark-done".format(task.pk) not in body
    r = staff_client(officer).get("/tasks/assigned/")
    assert "Open in QA" in r.content.decode()


# ---------------------------------------------------------------------------
# Reassignment (§8 step 7): R-01, R-02, R-20, who may reassign; rollback
# ---------------------------------------------------------------------------
def test_reassign_to_an_overworked_officer_is_rejected_r01(p4):
    from accreditation import case_services as cs
    from qa_core.task_bridge import reassign_stage_task
    from tasks import services
    from tasks.models import TaskAssignee

    set_config("qa.resource_person.max_open_ssq_per_officer", "1")
    busy = p4.people.busy_officer
    other = second_case(p4)
    advance_to(p4, 2, other)
    reassign_stage_task(open_link(other, "generate_cost"), actor=p4.people.director,
                        from_user=p4.people.desk_officer, to_user=busy)
    assert cs.open_case_load(busy) == 1
    case = advance_to(p4, 2)
    link = open_link(case, "generate_cost")
    with pytest.raises(ValidationError) as exc:
        reassign_stage_task(link, actor=p4.people.director, from_user=p4.people.desk_officer, to_user=busy)
    assert "R-01" in " ".join(exc.value.messages)
    # The same through the Tasks app: the signal re-validates and rolls back.
    with pytest.raises(ValidationError):
        services.reassign(link.task, p4.people.director, p4.people.desk_officer, busy)
    assert TaskAssignee.objects.filter(task_id=link.task_id, user=p4.people.desk_officer).exists()
    assert not TaskAssignee.objects.filter(task_id=link.task_id, user=busy).exists()
    case.refresh_from_db()
    assert case.desk_officer == p4.people.desk_officer


def test_reassign_outside_the_grade_range_is_rejected_r20(p4, make_user):
    from qa_core.task_bridge import reassign_stage_task
    from staff.models import Staff

    senior = make_user("senior.officer@demo.nbte.local", role="officer", dept="dpap")
    _profile(Staff, senior, "T-GL14", "GL 14")
    case = advance_to(p4, 2)
    with pytest.raises(ValidationError) as exc:
        reassign_stage_task(open_link(case, "generate_cost"), actor=p4.people.director,
                            from_user=p4.people.desk_officer, to_user=senior)
    assert "R-20" in " ".join(exc.value.messages)


def test_reassign_a_review_to_a_member_of_the_visit_team_is_rejected_r02(p4):
    from qa_core.task_bridge import reassign_stage_task

    case = advance_to(p4, 5)  # the desk officer is on the visit team
    with pytest.raises(ValidationError) as exc:
        reassign_stage_task(open_link(case, "audit_personnel_cost"), actor=p4.people.es,
                            from_user=p4.people.audit_officer, to_user=p4.people.desk_officer)
    assert "R-02" in " ".join(exc.value.messages)


def test_valid_reassign_moves_the_task_and_the_case_field(p4, make_user):
    from accreditation import case_services as cs
    from qa_core.models import FlowEvent
    from qa_core.task_bridge import reassign_stage_task
    from staff.models import Staff

    new = make_user("second.officer@demo.nbte.local", role="officer", dept="dpap")
    _profile(Staff, new, "T-GL09", "9")
    case = advance_to(p4, 2)
    reassign_stage_task(open_link(case, "generate_cost"), actor=p4.people.director,
                        from_user=p4.people.desk_officer, to_user=new)
    case.refresh_from_db()
    assert holder(case, "generate_cost") == new and case.desk_officer == new
    assert cs.can_act(case, new, "generate_cost")[0] and not cs.can_act(case, p4.people.desk_officer,
                                                                        "generate_cost")[0]
    assert FlowEvent.objects.filter(action_code="reassigned", instance=cs.instance_of(case)).exists()


def test_who_may_reassign(p4):
    from qa_core.task_bridge import may_reassign

    case = advance_to(p4, 2)
    task = open_link(case, "generate_cost").task
    assert not may_reassign(p4.people.ict_monitor, task)[0]
    assert not may_reassign(p4.people.desk_officer, task)[0]  # the assignee returns instead
    assert may_reassign(p4.people.es, task)[0]
    assert not may_reassign(p4.people.audit_officer, task)[0]


def test_assignee_returning_the_task_flags_the_case(p4):
    from tasks import services
    from tasks.models import Task

    case = advance_to(p4, 2)
    task = Task.objects.get(pk=open_link(case, "generate_cost").task_id)
    services.return_to_assigner(task, p4.people.desk_officer, reason="On leave for two weeks")
    case.refresh_from_db()
    assert case.needs_reassignment


# ---------------------------------------------------------------------------
# The ICT monitor: sees everything, writes nothing (parametrised over POSTs)
# ---------------------------------------------------------------------------
POST_URLS = [
    ("act/", {"stage": "generate_cost", "action": "approve"}),
    ("start/", {}),
    ("desk-officer/", {"officer": "1"}),
    ("supervisor/audit/", {"officers": ["1"]}),
    ("supervisor/finance/", {"officers": ["1"]}),
    ("supervisor/ict/", {"officers": ["1"]}),
    ("cost/lines/", {"rate": "1", "days": "1"}),
    ("cost/lines/1/", {"op": "save", "days": "2"}),
    ("personnel/", {"staff": "1", "duty": "member"}),
    ("personnel/1/remove/", {}),
    ("visit/", {"planned_start": "2030-01-01", "planned_end": "2030-01-02"}),
    ("release/", {"reference": "PV/1"}),
    ("tasks/1/reassign/", {"to_user": "1"}),
]


@pytest.mark.parametrize("path,data", POST_URLS, ids=[p for p, _ in POST_URLS])
def test_ict_monitor_cannot_write(p4, path, data):
    from accreditation import case_services as cs

    case = advance_to(p4, 2)
    before = (_stages(case), len(cs.case_bundle(case)["lines"]), open_stage_codes(case))
    r = staff_client(p4.people.ict_monitor).post(f"/qa/cases/{case.pk}/{path}", data)
    assert r.status_code == 403
    assert (_stages(case), len(cs.case_bundle(case)["lines"]), open_stage_codes(case)) == before


def test_ict_monitor_can_view_every_tab_and_the_drift_report(p4):
    case = advance_to(p4, 2)
    c = staff_client(p4.people.ict_monitor)
    for tab in ("overview", "cost", "personnel", "visit", "funds", "timeline", "tasks"):
        r = c.get(f"/qa/cases/{case.pk}/?tab={tab}")
        assert r.status_code == 200 and "Read-only" in r.content.decode(), tab
    assert c.get("/qa/cases/").status_code == 200
    assert c.get("/qa/cases/drift/").status_code == 200
    assert c.post("/qa/cases/drift/run/").status_code == 403


def test_monitor_cannot_act_through_the_service_either(p4):
    from accreditation import case_services as cs

    case = advance_to(p4, 2)
    ok, why = cs.can_act(case, p4.people.ict_monitor, "generate_cost")
    assert not ok and "R-13" in why


# ---------------------------------------------------------------------------
# Scope and permissions on the screens
# ---------------------------------------------------------------------------
def test_out_of_scope_staff_get_404(p4, make_user):
    outsider = make_user("vtsd.officer@nbte.local", role="officer", dept="vtsd")
    c = staff_client(outsider)
    assert c.get(f"/qa/cases/{p4.case.pk}/").status_code == 404
    assert c.post(f"/qa/cases/{p4.case.pk}/act/", {"stage": "case_opened", "action": "approve"}).status_code == 404


def test_only_the_task_holder_can_act(p4):
    case = advance_to(p4, 2)
    r = staff_client(p4.people.director).post(f"/qa/cases/{case.pk}/act/", {"stage": "generate_cost",
                                                                          "action": "approve"})
    assert r.status_code == 403 and _stages(case) == ["generate_cost"]


def test_cost_sheet_editor_rules(p4):
    from accreditation import case_services as cs
    from accreditation.models import RateTable

    case = advance_to(p4, 2)
    rate = RateTable.objects.get(code="dta-director", department__isnull=True)
    assert cs.cost_editor(case, p4.people.desk_officer) == "department"
    assert cs.cost_editor(case, p4.people.audit_officer) is None
    with pytest.raises(PermissionDenied):
        cs.add_cost_line(case, user=p4.people.es, rate=rate, days="2")
    line = cs.add_cost_line(case, user=p4.people.desk_officer, rate=rate, days="2")
    assert line.amount == 150000
    case = advance_to(p4, 5, case)
    assert cs.cost_editor(case, p4.people.audit_officer) == "audit"
    line = cs.case_bundle(case)["lines"][0]
    with pytest.raises(ValidationError):  # audit changes need a note
        cs.update_cost_line(case, line, user=p4.people.audit_officer, days="1")
    cs.update_cost_line(case, line, user=p4.people.audit_officer, days="1", note="One night")
    line.refresh_from_db()
    assert line.edited_by_audit and line.audit_note == "One night"


def test_team_lead_must_hold_a_team_lead_role(p4):
    from accreditation import case_services as cs

    case = advance_to(p4, 3)
    with pytest.raises(ValidationError):
        cs.add_personnel(case, user=p4.people.desk_officer, staff=p4.people.desk_officer.staff_profile,
                         duty="team_lead")


def test_only_the_pay_officer_releases_funds(p4):
    from accreditation import case_services as cs

    case = advance_to(p4, 11)
    assert _stages(case) == ["request_fund_release"]
    release = cs.current_release(case)
    assert release.status == "es_approved" and release.amount == cs.current_sheet(case).total
    with pytest.raises(ValidationError):
        cs.act(case, user=p4.people.pay_officer, stage_code="request_fund_release", action="approve")
    with pytest.raises(PermissionDenied):
        cs.record_release(case, user=p4.people.es, reference="PV/ES/1")


# ---------------------------------------------------------------------------
# SLA reminders at the configured percentages, and breach
# ---------------------------------------------------------------------------
def test_sla_reminders_at_the_configured_percentages(p4):
    import datetime

    from django.utils import timezone

    from qa_core.task_bridge import send_stage_reminders

    set_config("qa.stage.reminder_at_percent", "[50, 90]")
    case = advance_to(p4, 2)
    link = open_link(case, "generate_cost")
    start = timezone.now()
    link.opened_at, link.due_at = start, start + datetime.timedelta(hours=100)
    link.save()
    assert send_stage_reminders(now=start + datetime.timedelta(hours=40)) == []
    assert send_stage_reminders(now=start + datetime.timedelta(hours=55)) == [(link.pk, 50)]
    assert send_stage_reminders(now=start + datetime.timedelta(hours=60)) == []  # once each
    assert send_stage_reminders(now=start + datetime.timedelta(hours=91)) == [(link.pk, 90)]
    assert send_stage_reminders(now=start + datetime.timedelta(hours=101)) == [(link.pk, "breach")]
    assert send_stage_reminders(now=start + datetime.timedelta(hours=120)) == []
    link.refresh_from_db()
    assert link.reminders_sent == [50, 90] and link.breach_notified_at is not None


def test_reminder_percentages_follow_the_setting(p4):
    import datetime

    from django.utils import timezone

    from qa_core.task_bridge import send_stage_reminders

    set_config("qa.stage.reminder_at_percent", "[25]")
    case = advance_to(p4, 2)
    link = open_link(case, "generate_cost")
    start = timezone.now()
    link.opened_at, link.due_at = start, start + datetime.timedelta(hours=100)
    link.save()
    assert send_stage_reminders(now=start + datetime.timedelta(hours=30)) == [(link.pk, 25)]


def test_reminder_command_runs(p4):
    out = io.StringIO()
    call_command("qa_stage_reminders", stdout=out)
    assert "reminder" in out.getvalue().lower()


# ---------------------------------------------------------------------------
# Nightly reconciliation repairs drift
# ---------------------------------------------------------------------------
def test_reconciliation_repairs_an_orphaned_task(p4):
    from accreditation import case_services as cs
    from qa_core.models import FlowInstance, TaskLinkDriftReport
    from qa_core.task_bridge import reconcile
    from tasks.models import Task

    case = advance_to(p4, 2)
    orphan = open_link(case, "generate_cost")
    # Drift: the instance moves on without the bridge seeing it.
    FlowInstance.objects.filter(pk=cs.instance_of(case).pk).update(current_stages=["add_personnel"])
    dry = reconcile(dry_run=True)
    assert dry.closed == 1 and dry.opened == 1
    orphan.refresh_from_db()
    assert orphan.closed_at is None  # a dry run changes nothing
    report = reconcile()
    assert report.closed == 1 and report.opened == 1 and not report.dry_run
    orphan.refresh_from_db()
    assert orphan.closed_at is not None and orphan.close_reason == "reconciled"
    assert Task.objects.get(pk=orphan.task_id).state == Task.COMPLETED
    assert open_stage_codes(case) == ["add_personnel"]
    assert reconcile().closed == 0
    assert TaskLinkDriftReport.objects.count() == 3


def test_reconcile_command(p4):
    out = io.StringIO()
    call_command("qa_reconcile_tasks", "--dry-run", stdout=out)
    assert "checked" in out.getvalue()


# ---------------------------------------------------------------------------
# Timeline matches the task history
# ---------------------------------------------------------------------------
def test_timeline_matches_the_task_history(p4):
    from accreditation import case_services as cs
    from qa_core import flow

    case = advance_to(p4, 6)
    act(case, "confirm_audit_cost", "return", remark="Recheck transport")
    events = [e for e in flow.history(cs.instance_of(case)) if e.action_code in ("approve", "return", "resubmit")]
    closed = [l for l in cs.case_links(case) if l.closed_at is not None]
    assert len(events) == len(closed)
    assert sorted(e.from_stage for e in events) == sorted(l.stage_code for l in closed)
    r = staff_client(p4.people.es).get(f"/qa/cases/{case.pk}/?tab=timeline")
    assert "Recheck transport" in r.content.decode()


# ---------------------------------------------------------------------------
# Send Back To Audit (two-officer action, now live)
# ---------------------------------------------------------------------------
def test_send_back_to_audit_service(p4):
    from accreditation import case_services as cs

    case = advance_to(p4, 5)
    with pytest.raises(ValidationError):
        cs.send_back_to_audit(case, actor=p4.people.es, remark="Too early")
    case = advance_to(p4, 8, case)
    assert _stages(case) == ["add_visit_details"]
    cs.send_back_to_audit(case, actor=p4.people.es, remark="Transport rates changed")
    assert _stages(case) == ["audit_personnel_cost"]
    assert holder(case, "audit_personnel_cost") == p4.people.audit_officer
    assert open_stage_codes(case) == ["audit_personnel_cost"]
    assert cs.current_sheet(case).status == "submitted"


def test_send_back_to_audit_action_handler_is_live(p4):
    from qa_core.actions import get_handler

    handler = get_handler("accreditation.send_back_to_audit")
    assert handler.available_for(p4.inst)[0] is False
    case = advance_to(p4, 8)
    assert handler.available_for(p4.inst)[0] is True
    payload = {"case_id": case.pk, "reason": "Recheck the DTA lines"}
    handler.validate(p4.inst, payload)
    handler.apply(p4.inst, payload, p4.people.es)
    assert _stages(case) == ["audit_personnel_cost"]


# ---------------------------------------------------------------------------
# Screens: board, queue, settings
# ---------------------------------------------------------------------------
def test_case_board_and_queues(p4):
    case = advance_to(p4, 7)
    c = staff_client(p4.people.es)
    r = c.get("/qa/cases/")
    assert r.status_code == 200 and case.reference in r.content.decode()
    r = c.get("/qa/cases/?mine=1", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="case-board")
    assert r.status_code == 200 and case.reference in r.content.decode()
    r = c.get("/qa/cases/my-stages/?kind=cost")
    assert r.status_code == 200 and case.reference in r.content.decode()
    r = staff_client(p4.people.pay_officer).get("/qa/cases/my-stages/?kind=funds")
    assert r.status_code == 200
    r = c.get(f"/qa/cases/{case.pk}/", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 200 and "<html" not in r.content.decode()


def test_rate_table_settings(p4, make_user):
    from accreditation.models import RateTable

    rate = RateTable.objects.get(code="transport-road", department__isnull=True)
    fd = staff_client(make_user("qa.fin.director@nbte.local", role="qa-finance-director", dept="finance"))
    assert fd.get("/qa/settings/rates/").status_code == 200
    r = fd.post(f"/qa/settings/rates/{rate.pk}/", {"op": "save", "amount": "52000"})
    assert r.status_code == 200
    rate.refresh_from_db()
    assert rate.amount == 52000
    officer = staff_client(p4.people.desk_officer)
    assert officer.post(f"/qa/settings/rates/{rate.pk}/", {"op": "save", "amount": "1"}).status_code in (403, 422)
    rate.refresh_from_db()
    assert rate.amount == 52000


# ---------------------------------------------------------------------------
# The demo seed (idempotent)
# ---------------------------------------------------------------------------
def test_seed_phase4_demo_is_idempotent(qa_seed, settings):
    from accreditation.models import QACase

    settings.DEBUG = True
    out = io.StringIO()
    call_command("seed_phase4_demo", "--skip-startup", "--stage", "3", stdout=out)
    text = out.getvalue()
    assert "qa.audit_officer@demo.nbte.local" in text and "qa.es@demo.nbte.local" in text
    assert "stage 3" in text
    out2 = io.StringIO()
    call_command("seed_phase4_demo", "--skip-startup", "--stage", "3", stdout=out2)
    assert "already existed" in out2.getvalue() and "(unchanged)" in out2.getvalue()
    assert QACase.unscoped.count() == 1


# ---------------------------------------------------------------------------
# Pinned actors: a case keeps its directors / ES once chosen
# ---------------------------------------------------------------------------
def test_stages_go_to_the_pinned_people_even_with_other_directors(p4, make_user):
    """Another ES and another DPAP Director exist (created first would win a
    plain lookup); the case keeps the people pinned on it."""
    from accreditation import case_services as cs

    make_user("other.es@nbte.local", role="executive-secretary")
    make_user("other.director@nbte.local", role="director", dept="dpap")
    case = advance_to(p4, 7)
    assert holder(case, "es_approve_cost") == p4.people.es
    act(case, "es_approve_cost", "return", remark="Recheck")
    act(case, "confirm_audit_cost")
    assert holder(case, "es_approve_cost") == p4.people.es
    assert case.stage_actors["es"] == p4.people.es.pk
    assert cs.pinned_user(case, cs.instance_of(case).flow_version.stages.get(code="confirm_personnel")) == \
        p4.people.director


def test_reassigning_a_director_stage_pins_the_new_holder(p4, make_user):
    from accreditation import case_services as cs
    from qa_core.task_bridge import reassign_stage_task

    deputy = make_user("deputy.dpap@nbte.local", role="deputy-director", dept="dpap")
    case = advance_to(p4, 4)
    reassign_stage_task(open_link(case, "confirm_personnel"), actor=p4.people.es,
                        from_user=p4.people.director, to_user=deputy)
    case.refresh_from_db()
    assert holder(case, "confirm_personnel") == deputy
    assert case.stage_actors["owning_department_director"] == deputy.pk
    assert cs.can_act(case, deputy, "confirm_personnel")[0]
