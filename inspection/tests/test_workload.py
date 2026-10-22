"""Phase 8 — fair workload limits per rank (qa.workload.limits).

Each limit is tested on its own: per-rank SSQ reviews at once and per year,
visits per year, the same institution within N months, open tasks, staff
before resource persons, the fair ranking, the visit team and task
reassignment save paths, and the settings table editor.
"""
import datetime
import json

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from qa_core.tests.phase3_fixtures import set_config
from qa_core.tests.phase5_fixtures import need

pytestmark = pytest.mark.django_db


def limits(*rows):
    set_config("qa.workload.limits", json.dumps({"rows": [{"key": "default"}, *rows]}))


def rules_of(person, n):
    from inspection import rules

    return {p.rule: p for p in rules.assignment_problems(person, n)}


def visit(rp, inst=None, days_ago=40, n=1, kind="resource_person", ssq=0):
    from inspection.models import ServiceRecord

    day = timezone.localdate() - datetime.timedelta(days=days_ago)
    for _ in range(n):
        ServiceRecord.unscoped.create(person_type=kind, person_id=rp.pk, person_name=str(rp),
                                      resource_person=rp if kind == "resource_person" else None,
                                      qa_personnel=rp if kind == "qa_personnel" else None,
                                      institution=inst, duty="ssq_review" if ssq else "resource_inspection",
                                      served_from=day, served_to=day, visit_count=0 if ssq else 1,
                                      ssq_count=1 if ssq else 0, source="backfill")


def test_untouched_table_keeps_the_general_rules(p5):
    from inspection import workload

    rp = p5.rps["rp_eng"]
    assert workload.limit(rp, "max_visits_per_year").value == 6
    assert workload.limit(rp, "max_visits_per_year").setting == "qa.resource_person.max_visits_per_year"
    assert workload.limit(rp, "max_open_tasks").value is None


def test_visits_per_year_per_rank(p5):
    rp = p5.rps["rp_eng"]  # rank chief-lecturer
    visit(rp)
    assert "annual_cap" not in rules_of(rp, need(p5))
    limits({"key": "chief-lecturer", "max_visits_per_year": 1})
    problem = rules_of(rp, need(p5))["annual_cap"]
    assert "qa.workload.limits" in problem.setting and "cap is 1" in problem.message
    # A different rank is not affected.
    limits({"key": "principal-lecturer", "max_visits_per_year": 1})
    assert "annual_cap" not in rules_of(rp, need(p5))


def test_default_row_overrides_the_general_setting(p5):
    rp = p5.rps["rp_eng"]
    visit(rp, n=2)
    set_config("qa.workload.limits", json.dumps({"rows": [{"key": "default", "max_visits_per_year": 2}]}))
    assert "annual_cap" in rules_of(rp, need(p5))


def test_open_ssq_reviews_per_rank(p5):
    from accreditation.models import SSQ
    from inspection.models import SSQReviewAssignment

    qp = p5.qps["qa_officer2"]
    ssq = SSQ.unscoped.filter(qa_request_programme=p5.eee).first()
    SSQReviewAssignment.unscoped.create(ssq=ssq, reviewer=qp.staff.user)
    assert "open_ssq" not in rules_of(qp, need(p5, duty="ssq_review"))
    limits({"key": "qa_personnel", "max_open_ssq": 1})
    problem = rules_of(qp, need(p5, duty="ssq_review"))["open_ssq"]
    assert "limit is 1" in problem.message


def test_ssq_reviews_per_year(p5):
    rp = p5.rps["rp_sci"]
    visit(rp, ssq=1, n=2)
    limits({"key": "resource_person", "max_ssq_per_year": 2})
    problem = rules_of(rp, need(p5, row=p5.cs, duty="ssq_review"))["ssq_year_cap"]
    assert "cap is 2" in problem.message
    # Visits are not SSQ reviews: the visit duty is unaffected.
    assert "ssq_year_cap" not in rules_of(rp, need(p5, row=p5.cs))


def test_same_institution_n_times_within_months(p5):
    rp = p5.rps["rp_eng"]
    visit(rp, inst=p5.inst, days_ago=100)
    assert "cooldown" in rules_of(rp, need(p5))  # general rule: once in 24 months
    limits({"key": "chief-lecturer", "max_same_institution": 2, "same_institution_months": 12})
    assert "cooldown" not in rules_of(rp, need(p5))
    visit(rp, inst=p5.inst, days_ago=50)
    problem = rules_of(rp, need(p5))["cooldown"]
    assert "12 months" in problem.message and "2 times" in problem.message
    # Older than the window: not counted.
    limits({"key": "chief-lecturer", "max_same_institution": 2, "same_institution_months": 1})
    assert "cooldown" not in rules_of(rp, need(p5))


def test_max_open_tasks(p5):
    from tasks.models import Task, TaskAssignee

    qp = p5.qps["qa_officer2"]
    user = qp.staff.user
    task = Task.objects.create(title="Something else", reference="T-WL-X", created_by=user, state=Task.OPEN)
    TaskAssignee.objects.create(task=task, user=user)
    assert "open_tasks" not in rules_of(qp, need(p5))
    limits({"key": "qa_personnel", "max_open_tasks": 1})
    assert "open_tasks" in rules_of(qp, need(p5))
    Task.objects.filter(pk=task.pk).update(state=Task.COMPLETED)
    assert "open_tasks" not in rules_of(qp, need(p5))


def test_task_reassignment_respects_open_tasks(p5):
    from accreditation import case_services as cs
    from qa_core.models import FlowStage
    from tasks.models import Task, TaskAssignee

    user = p5.qps["qa_officer2"].staff.user
    for i in range(2):
        t = Task.objects.create(title=f"T{i}", reference=f"T-WL-{i}", created_by=user, state=Task.OPEN)
        TaskAssignee.objects.create(task=t, user=user)
    set_config("qa.workload.limits", json.dumps({"rows": [{"key": "default", "max_open_tasks": 2}]}))
    stage = FlowStage.objects.filter(code="es_approve_cost").first()
    with pytest.raises(ValidationError) as exc:
        cs.validate_assignee(p5.case, stage, user)
    assert "open task" in str(exc.value)


def test_visit_team_respects_visits_per_year(p5):
    from accreditation import case_services as cs
    from inspection import workload

    qp = p5.qps["qa_officer2"]
    visit(qp, kind="qa_personnel", n=2)
    limits({"key": "qa_personnel", "max_visits_per_year": 2})
    assert workload.problems_for(qp.staff.user, duty="physical_verification")
    with pytest.raises(ValidationError):
        cs._check_visit_load(p5.case, qp.staff)


def test_finder_ranks_fewest_assignments_then_longest_since_served(p5):
    from inspection import rules

    zainab, obi = p5.rps["rp_sci"], p5.rps["rp_sci2"]
    visit(zainab, ssq=1)  # one SSQ review this year counts as an assignment
    ranked = [c.name for c in rules.find(need(p5, row=p5.cs), kind="resource_person")]
    assert ranked.index(obi.full_name) < ranked.index(zainab.full_name)
    visit(obi, ssq=1, days_ago=5)  # equal counts: the one who served longer ago goes first
    ranked = [c.name for c in rules.find(need(p5, row=p5.cs), kind="resource_person")]
    assert ranked.index(zainab.full_name) < ranked.index(obi.full_name)


def test_staff_first_and_resource_persons_only_when_no_staff(p5):
    from inspection import rules

    cands = rules.find(need(p5), kind="all")
    kinds = [c.kind for c in cands]
    assert "qa_personnel" in kinds and "resource_person" in kinds
    assert kinds.index("qa_personnel") < kinds.index("resource_person")
    assert all("open_ssq" in c.load and "visits_year" in c.load for c in cands)
    set_config("qa.workload.resource_persons_only_when_no_staff", "true")
    cands = rules.find(need(p5), kind="all", include_ineligible=True)
    yakubu = next(c for c in cands if c.name == "Engr. Prof. Yakubu Danjuma")
    assert not yakubu.ok and yakubu.problems[0].rule == "staff_first"
    with pytest.raises(ValidationError):
        rules.check_assignment(p5.rps["rp_eng"], need(p5))


def test_lists_and_finder_show_the_load(p5):
    from qa_core.tests.phase4_fixtures import staff_client

    c = staff_client(p5.people.desk_officer)
    n = need(p5)
    body = c.get(f"/qa/inspection/finder/?case={p5.case.pk}&row={p5.eee.pk}&duty=resource_inspection"
                 f"&start={n.start.isoformat()}&end={n.end.isoformat()}").content.decode()
    assert "Current load" in body and "Visits 0/6" in body
    body = c.get("/qa/inspection/personnel/").content.decode()
    assert "Current load" in body and "Tasks" in body
    body = c.get("/qa/inspection/resource-persons/").content.decode()
    assert "Current load" in body


def test_settings_table_editor(p5, make_user):
    from config.models import ConfigEntry
    from qa_core.tests.phase4_fixtures import staff_client

    admin = make_user("wl.admin@t.local", role="officer", dept="ict", groups=["QA Settings Admin"])
    c = staff_client(admin)
    r = c.get("/qa/settings/workload/")
    assert r.status_code == 200 and "Workload limits" in r.content.decode()
    # Add a row (not saved), then save it.
    r = c.post("/qa/settings/workload/", {"op": "add", "key": ["default"], "label": [""],
                                          **{f: [""] for f in ("max_open_ssq", "max_ssq_per_year",
                                                               "max_visits_per_year", "max_same_institution",
                                                               "same_institution_months", "max_open_tasks")}},
               HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 200 and r.content.decode().count('name="key"') == 2
    data = {"op": "save", "key": ["default", "director"], "label": ["", ""], "max_open_ssq": ["", "2"],
            "max_ssq_per_year": ["", "10"], "max_visits_per_year": ["5", "3"], "max_same_institution": ["", ""],
            "same_institution_months": ["", ""], "max_open_tasks": ["", ""],
            "qa.workload.staff_first": "1", "reason": "share visits"}
    r = c.post("/qa/settings/workload/", data, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 200, r.content.decode()[:500]
    stored = json.loads(ConfigEntry.objects.get(key="qa.workload.limits").value)
    assert stored["rows"][0] == {"key": "default", "max_visits_per_year": 5}
    assert stored["rows"][1] == {"key": "director", "max_open_ssq": 2, "max_ssq_per_year": 10,
                                 "max_visits_per_year": 3}
    # Bad input is refused with a message, nothing stored.
    bad = dict(data, max_open_ssq=["", "two"])
    r = c.post("/qa/settings/workload/", bad, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert r.status_code == 422 and "whole number" in r.content.decode()


def test_settings_editor_is_read_only_without_permission(p5):
    from qa_core.tests.phase4_fixtures import staff_client

    c = staff_client(p5.people.desk_officer)
    r = c.get("/qa/settings/workload/")
    if r.status_code == 200:
        assert "Save limits" not in r.content.decode()
        r = c.post("/qa/settings/workload/", {"op": "save", "key": ["default"], "max_visits_per_year": ["1"]})
        assert r.status_code in (403, 422)
    else:
        assert r.status_code in (403, 404)
