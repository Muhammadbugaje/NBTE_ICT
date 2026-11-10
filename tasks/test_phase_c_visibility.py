"""Phase C, C.1 (H2, H4, H10, I1, T-PERF-1, T-CONS-6): work.visibility."""
import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tasks.conftest import fresh, mk
from tasks.models import Task, TaskAssignee

pytestmark = pytest.mark.django_db
_ref = {"n": 0}


def mktask(creator, dept, *assignees, reviewer=None, division=None, state=Task.OPEN):
    _ref["n"] += 1
    t = Task.objects.create(reference=f"TSK-C1-{_ref['n']:04d}", title=f"t{_ref['n']}", created_by=creator,
                            owner_department=dept, reviewer=reviewer, division=division, state=state)
    for a in assignees:
        TaskAssignee.objects.create(task=t, user=a)
    return t


@pytest.fixture
def matrix(people, depts):
    p, ict, hr = people, depts["ict"], depts["hr"]
    return {
        "dir_own": mktask(p["director"], ict),
        "dep_own": mktask(p["deputy"], ict),
        "officer": mktask(p["director"], ict, p["officer"]),
        "head": mktask(p["deputy"], ict, p["head"]),
        "staff_self": mktask(p["staff"], ict),
        "div": mktask(p["director"], ict, division=p["division"]),
        "hr": mktask(p["hr_dir"], hr, p["hr_officer"]),
        "hr_review": mktask(p["hr_dir"], hr, reviewer=p["officer"]),
    }


EXPECTED = {
    "es": {"dir_own", "dep_own", "officer", "head", "staff_self", "div", "hr", "hr_review"},
    "director": {"dir_own", "dep_own", "officer", "head", "staff_self", "div"},
    "deputy": {"dep_own", "officer", "head", "staff_self"},
    "head": {"head", "officer", "staff_self", "div"},
    "officer": {"officer", "staff_self", "hr_review"},
    "officer2": {"staff_self"},
    "staff": {"staff_self"},
    "hr_dir": {"hr", "hr_review"},
    "hr_officer": {"hr"},
}


@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_visibility_matrix(people, matrix, who):
    from tasks.views import _visible_tasks_qs
    from work.visibility import visible

    names = {v.pk: k for k, v in matrix.items()}
    seen = {names[pk] for pk in _visible_tasks_qs(people[who]).values_list("pk", flat=True) if pk in names}
    assert seen == EXPECTED[who], who
    assert {names[pk] for pk in visible(people[who], Task).values_list("pk", flat=True)} == EXPECTED[who]


def test_h3_view_all_permission_sees_everything(people, matrix):
    from tasks.views import _visible_tasks_qs

    u = people["officer2"]
    u.user_permissions.add(Permission.objects.get(codename="view_all_tasks", content_type__app_label="tasks"))
    assert set(_visible_tasks_qs(fresh(u)).values_list("pk", flat=True)) >= {t.pk for t in matrix.values()}


def test_h8_parent_people_see_subtasks(people, depts):
    from tasks.views import _visible_tasks_qs

    parent = mktask(people["hr_dir"], depts["hr"], people["hr_officer"])
    child = mktask(people["director"], depts["ict"], people["officer"])
    child.parent = parent
    child.save()
    assert _visible_tasks_qs(people["hr_officer"]).filter(pk=child.pk).exists()


def test_assignable_users_rank_and_department(people):
    from work.visibility import assign_problem, assignable_users

    ids = lambda qs: set(qs.values_list("email", flat=True))  # noqa: E731
    assert ids(assignable_users(people["deputy"])) == {"head@t.local", "officer@t.local", "officer2@t.local",
                                                       "staff@t.local"}
    assert ids(assignable_users(people["officer"])) == {"staff@t.local"}
    assert ids(assignable_users(people["director"])) >= {"deputy@t.local", "head@t.local"}
    assert "hrofficer@t.local" not in ids(assignable_users(people["director"]))
    assert not assignable_users(people["director"], people["hr_dir"].department).exists()  # cross-dept -> request
    es_ids = ids(assignable_users(people["es"]))
    assert {"hrofficer@t.local", "officer@t.local", "director@t.local"} <= es_ids and "es@t.local" not in es_ids
    assert assign_problem(people["deputy"], people["director"]).startswith("You cannot assign")
    assert "another department" in assign_problem(people["director"], people["hr_officer"])
    assert assign_problem(people["es"], people["hr_officer"]) == ""
    assert assign_problem(people["officer"], people["officer2"])  # equal rank refused


def test_h4_service_refuses_cross_department_and_equal_rank(people, depts):
    from tasks import services

    t = services.create_task(created_by=people["director"], title="ICT work", owner_department=depts["ict"])
    with pytest.raises(ValidationError, match="another department"):
        services.add_assignee(t, people["director"], people["hr_officer"])
    services.add_assignee(t, people["director"], people["officer"])
    t2 = services.create_task(created_by=people["officer"], title="Peer work", owner_department=depts["ict"])
    with pytest.raises(ValidationError, match="equal to or higher"):
        services.add_assignee(t2, people["officer"], people["officer2"])


def test_t_perf_1_open_load_one_query(people, depts):
    from work.visibility import assignable_users, load_info

    for i in range(3):
        mktask(people["director"], depts["ict"], people["officer"])
    mktask(people["director"], depts["ict"], people["officer"], state=Task.COMPLETED)
    with CaptureQueriesContext(connection) as ctx:
        rows = list(assignable_users(people["director"]))
        infos = {u.email: load_info(u) for u in rows}
    assert len(ctx.captured_queries) <= 3  # the users query (+ org seat look-ups), never one per person
    assert infos["officer@t.local"]["open"] == 3 and infos["officer@t.local"]["label"] == "3 open"
    assert infos["officer2@t.local"]["open"] == 0


def test_eligible_assignees_json_shows_load(client, people, depts):
    client.force_login(people["deputy"])
    mktask(people["director"], depts["ict"], people["officer"])
    r = client.get("/tasks/api/eligible-assignees/")
    rows = {x["email"]: x for x in r.json()["results"]}
    assert "director@t.local" not in rows and rows["officer@t.local"]["used"] == 1
    assert rows["officer@t.local"]["load_label"] == "1 open"
