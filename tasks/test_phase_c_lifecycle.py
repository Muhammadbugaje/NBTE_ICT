"""Phase C, C.2-C.4: life cycle, subtasks, bridge, PDF, divisions, counts."""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from notifications.models import Notification
from tasks import division as div
from tasks import services
from tasks.models import Task, TaskAssignee, TaskTimeEntry

pytestmark = pytest.mark.django_db


def opened(creator, *assignees, dept=None, review=True, **kw):
    t = services.create_task(created_by=creator, title="Phase C work", owner_department=dept,
                             requires_review=review, assignees=list(assignees), **kw)
    services.open_task(t, creator)
    t.refresh_from_db()
    return t


def notes_for(user):
    return Notification.objects.filter(recipient=user)


# ---------------------------------------------------------------- C.2
def test_h7_only_creator_edits(people, depts):
    t = opened(people["deputy"], people["officer"], dept=depts["ict"])
    director = people["director"]  # higher rank, but not the creator
    assert services.can_edit_task(t, people["deputy"])
    assert not services.can_edit_task(t, director) and not services.can_cancel_task(t, director)
    with pytest.raises(PermissionDenied):
        services.add_assignee(t, director, people["officer2"])
    with pytest.raises(PermissionDenied):
        services.cancel_task(t, director)
    with pytest.raises(PermissionDenied):
        services.remove_assignee(t, director, people["officer"])
    # The ES keeps an audited override.
    services.add_assignee(t, people["es"], people["officer2"])
    services.cancel_task(t, people["es"], reason="ES override test")
    assert Task.objects.get(pk=t.pk).state == Task.CANCELLED
    flags = services.available_actions(t, director)
    assert not flags["can_edit"] and not flags["can_cancel"]


def test_h12_accept_starts_timer(people, depts):
    t = opened(people["deputy"], people["officer"], dept=depts["ict"])
    services.accept_and_start(t, people["officer"])
    row = t.assignee_rows.get(user=people["officer"])
    assert row.state == TaskAssignee.IN_PROGRESS and row.accepted_at
    entry = TaskTimeEntry.objects.get(task=t, user=people["officer"])
    assert entry.ended_at is None
    services.mark_assignee_done(t, people["officer"])
    entry.refresh_from_db()
    assert entry.ended_at is not None  # H13: mark done stops and saves it


def test_h14_return_one_part(people, depts):
    t = opened(people["deputy"], people["officer"], people["officer2"], dept=depts["ict"])
    for u in (people["officer"], people["officer2"]):
        services.accept_and_start(t, u)
        services.mark_assignee_done(t, u)
    t.refresh_from_db()
    assert t.state == Task.AWAITING_REVIEW
    before2 = notes_for(people["officer2"]).count()
    with pytest.raises(PermissionDenied):
        services.return_assignee_part(t, people["officer2"], people["officer"], "not mine to return")
    services.return_assignee_part(t, people["deputy"], people["officer"], "Recheck the Kano figures")
    t.refresh_from_db()
    r1, r2 = t.assignee_rows.get(user=people["officer"]), t.assignee_rows.get(user=people["officer2"])
    assert r1.state == TaskAssignee.IN_PROGRESS and r1.review_state == TaskAssignee.REVIEW_RETURNED
    assert r2.state == TaskAssignee.DONE and t.state == Task.IN_PROGRESS
    assert notes_for(people["officer"]).filter(title__startswith="Your part was returned").exists()
    assert notes_for(people["officer2"]).count() == before2  # only that person is told
    services.start_work(t, people["officer"])  # timer restarts
    assert TaskTimeEntry.objects.filter(task=t, user=people["officer"], ended_at__isnull=True).exists()


def test_h15_confirm_per_person_then_complete(people, depts):
    t = opened(people["deputy"], people["officer"], people["officer2"], dept=depts["ict"])
    for u in (people["officer"], people["officer2"]):
        services.accept_and_start(t, u)
        services.mark_assignee_done(t, u)
    services.confirm_assignee_part(t, people["deputy"], people["officer"])
    t.refresh_from_db()
    assert t.state == Task.AWAITING_REVIEW
    services.confirm_assignee_part(t, people["deputy"], people["officer2"])
    t.refresh_from_db()
    assert t.state == Task.COMPLETED


def test_h15_confirm_all_completes_and_h16_everyone_notified(people, depts):
    t = opened(people["deputy"], people["officer"], people["officer2"], dept=depts["ict"],
               reviewer=people["director"])
    for u in (people["officer"], people["officer2"]):
        services.accept_and_start(t, u)
        services.mark_assignee_done(t, u)
    services.confirm_all_parts(t, people["deputy"])
    t.refresh_from_db()
    assert t.state == Task.COMPLETED
    assert set(t.assignee_rows.values_list("review_state", flat=True)) == {TaskAssignee.REVIEW_CONFIRMED}
    for u in (people["officer"], people["officer2"], people["director"]):
        assert notes_for(u).filter(title__startswith="Task completed").exists(), u.email


def test_h5_load_badges_in_detail(client, people, depts):
    t = opened(people["deputy"], people["officer"], dept=depts["ict"])
    client.force_login(people["deputy"])
    html = client.get(f"/tasks/{t.pk}/").content.decode()
    assert "1 open" in html and "tsk-load" in html


# ---------------------------------------------------------------- C.3
def test_h9_single_empty_subtask_form(client, people, depts):
    t = opened(people["deputy"], people["officer"], dept=depts["ict"])
    client.force_login(people["deputy"])
    html = client.get(f"/tasks/{t.pk}/").content.decode()
    visible, _, template = html.partition("<template data-empty-form>")
    assert visible.count('class="tsk-subform-row"') == 1
    assert 'name="form-TOTAL_FORMS" value="1"' in html
    assert "__prefix__" in template and "Add another subtask" in html
    # H20 / H21: the reviewer is the styled picker, no plain select on the page for it.
    assert 'data-mode="reviewer"' in html and '<select name="reviewer"' not in html


def test_h8_assignee_can_split_with_lower_rank_only(people, depts):
    t = opened(people["deputy"], people["head"], dept=depts["ict"])
    kids = services.break_into_subtasks(t, people["head"], [{"title": "Part A", "assignee": people["officer"]}])
    assert kids[0].assignee_rows.filter(user=people["officer"]).exists()
    with pytest.raises(ValidationError):
        services.break_into_subtasks(t, people["head"], [{"title": "Part B", "assignee": people["deputy"]}])
    with pytest.raises(PermissionDenied):
        services.break_into_subtasks(t, people["officer2"], [{"title": "Not mine"}])
    from tasks.views import _visible_tasks_qs
    assert _visible_tasks_qs(people["deputy"]).filter(pk=kids[0].pk).exists()  # the initiator sees it


@pytest.fixture
def hr_request_type(depts):
    from workflow.models import ApprovalChain, ApprovalStep, RequestCategory, RequestType

    chain = ApprovalChain.objects.create(code="hr-t", name="HR (t)")
    ApprovalStep.objects.create(chain=chain, position=1, name="HR director",
                                approver_kind="receiving_director")
    cat = RequestCategory.objects.create(code="general", name="General")
    return RequestType.objects.create(name="Work from a task", code="task-handoff-hr", category=cat,
                                      handling_department=depts["hr"], approval_chain=chain)


def test_h11_cross_department_subtask_creates_request(people, depts, hr_request_type):
    from workflow.models import Request

    t = opened(people["deputy"], people["officer"], dept=depts["ict"])
    kid = services.break_into_subtasks(t, people["deputy"], [{"title": "HR records", "department": depts["hr"]}])[0]
    kid.refresh_from_db()
    req = kid.linked_request
    assert req is not None and req.receiving_department == depts["hr"]
    assert req.metadata["bridged_task_id"] == kid.pk
    assert not kid.assignee_rows.exists()
    # The outcome flows back.
    Request.objects.filter(pk=req.pk).update(status=Request.COMPLETED)
    req.refresh_from_db()
    req.save()
    kid.refresh_from_db()
    assert kid.state == Task.COMPLETED


def test_h19_closed_task_pdf_and_no_comments(client, people, depts):
    t = opened(people["deputy"], people["officer"], dept=depts["ict"], review=False)
    services.accept_and_start(t, people["officer"])
    services.mark_assignee_done(t, people["officer"])
    t.refresh_from_db()
    assert t.state == Task.COMPLETED
    with pytest.raises(ValidationError):
        services.add_comment(t, people["officer"], "late note")
    client.force_login(people["officer"])
    html = client.get(f"/tasks/{t.pk}/").content.decode()
    assert "Download report (PDF)" in html and 'action="/tasks/%d/comment/"' % t.pk not in html
    assert 'name="file"' not in html
    r = client.get(f"/tasks/{t.pk}/report.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    client.force_login(people["hr_officer"])
    assert client.get(f"/tasks/{t.pk}/report.pdf").status_code == 404


# ---------------------------------------------------------------- C.4
def test_h23_route_accept_adds_members(people, depts):
    t = services.create_task(created_by=people["director"], title="Group job", owner_department=depts["ict"])
    d = people["division"]
    div.route_to_division(t, people["director"], d)
    t.refresh_from_db()
    assert t.division == d and t.division_state == Task.DIV_PENDING and not t.assignee_rows.exists()
    assert notes_for(people["head"]).exists()
    with pytest.raises(PermissionDenied):
        div.accept_division_task(t, people["officer"])
    added = div.accept_division_task(t, people["head"])
    assert {u.pk for u in added} == {people["officer"].pk, people["officer2"].pk}
    # H24: the head completes the group task.
    div.complete_division_task(t, people["head"])
    t.refresh_from_db()
    assert t.state == Task.COMPLETED and t.is_group


def test_h23_named_members_only(people, depts):
    t = services.create_task(created_by=people["director"], title="Named", owner_department=depts["ict"])
    div.route_to_division(t, people["director"], people["division"], members=[people["officer2"]])
    added = div.accept_division_task(t, people["head"])
    assert [u.pk for u in added] == [people["officer2"].pk]


def test_b8_division_capacity_enforced(people, depts):
    import org

    d = people["division"]  # max_open_tasks = 3
    for i in range(3):
        t = services.create_task(created_by=people["director"], title=f"Group {i}", owner_department=depts["ict"])
        div.route_to_division(t, people["director"], d)
    assert not org.division_has_capacity(d)
    t = services.create_task(created_by=people["director"], title="One too many", owner_department=depts["ict"])
    with pytest.raises(ValidationError, match="3/3"):
        div.route_to_division(t, people["director"], d)


def test_h17_counts_match_lists(client, people, depts):
    from tasks import performance

    ind = opened(people["director"], people["officer"], dept=depts["ict"], review=False)
    services.accept_and_start(ind, people["officer"])
    services.mark_assignee_done(ind, people["officer"])
    opened(people["director"], people["officer"], dept=depts["ict"])
    g = services.create_task(created_by=people["director"], title="Group", owner_department=depts["ict"])
    div.route_to_division(g, people["director"], people["division"])
    div.accept_division_task(g, people["head"])

    data = performance.build(people["director"])
    row = next(r for r in data["people"] if r["user"].pk == people["officer"].pk)
    assert (row["open_individual"], row["open_group"], row["done_individual"]) == (1, 1, 1)
    drow = next(r for r in data["divisions"] if r["division"].pk == people["division"].pk)
    assert drow["open"] == 1 and drow["capacity_used"] == 1

    client.force_login(people["director"])
    since = data["month_start"].date().isoformat()

    def count(qs):
        return client.get(f"/tasks/?{qs}").context["total"]
    oid = people["officer"].pk
    assert count(f"assignee={oid}&kind=individual&state=active") == 1
    assert count(f"assignee={oid}&kind=group&state=active") == 1
    assert count(f"assignee={oid}&kind=individual&state=completed&since={since}") == 1
    assert count(f"division={people['division'].pk}&state=active") == 1
    r = client.get("/tasks/performance/")
    assert r.status_code == 200
    assert client.get("/tasks/performance/?format=csv")["Content-Type"].startswith("text/csv")
    client.force_login(people["officer"])
    assert client.get("/tasks/performance/").status_code == 403


def test_division_queue_page(client, people, depts):
    t = services.create_task(created_by=people["director"], title="Queue me", owner_department=depts["ict"])
    div.route_to_division(t, people["director"], people["division"])
    client.force_login(people["head"])
    html = client.get("/tasks/divisions/").content.decode()
    assert "Queue me" in html and "Accept and add team" in html and "1/3 open" in html


def test_api_phase_c_actions(people, depts):
    from rest_framework.test import APIClient

    from api.tests.conftest import BASE, bearer

    t = opened(people["deputy"], people["officer"], dept=depts["ict"])
    c = bearer(APIClient(HTTP_HOST="localhost"), people["officer"])
    r = c.post(BASE + f"tasks/{t.pk}/accept-start/")
    assert r.status_code == 200 and r.json()["running_timer"] is not None
    c.post(BASE + f"tasks/{t.pk}/mark-done/", {"note": ""}, format="json")
    r = c.post(BASE + f"tasks/{t.pk}/confirm-part/", {"user": people["officer"].pk}, format="json")
    assert r.status_code == 403  # only the creator / reviewer
    c2 = bearer(APIClient(HTTP_HOST="localhost"), people["deputy"])
    r = c2.post(BASE + f"tasks/{t.pk}/return-part/", {"user": people["officer"].pk, "reason": "Redo the totals"},
                format="json")
    assert r.status_code == 200
    assert r.json()["assignees"][0]["review_state"] == "returned"
    # visibility mirrored: another department's officer gets 404.
    c3 = bearer(APIClient(HTTP_HOST="localhost"), people["hr_officer"])
    assert c3.get(BASE + f"tasks/{t.pk}/").status_code == 404
