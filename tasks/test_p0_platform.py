"""Phase 0 tests for tasks, memos and tickets pickers.

* H18 / T-SEC-6: internal task notes only for the people involved (web + API).
* H22, M9 / T-SEC-2: staff pickers never list institution users or resource persons.
* N2: the task timeline fragment redirects a plain request to the task page.
"""
import pytest
from django.urls import reverse

from api.tests.conftest import BASE
from api.tests.work_fixtures import api_as, boss, outsider, work_seed, worker  # noqa: F401
from qa_core.tests.conftest import client_for, make_institution, make_user  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def deputy(work_seed, make_user):
    """Head-level role in the task's department, lower rank than the creator:
    can open the task, is not involved, so no internal notes."""
    return make_user("deputy.ict@t.local", role="deputy-director", dept="ict")


@pytest.fixture
def externals(work_seed, make_user):
    from institutions.models import Institution
    from staff.models import Department

    inst = Institution.unscoped.create(code="EXT", name="Ext Polytechnic", status="approved",
                                       handling_department=Department.objects.get(code="ict"))
    return [make_user("inst.user@ext.local", user_type="institution", institution=inst),
            make_user("rp.user@ext.local", user_type="resource_person")]


@pytest.fixture
def task(boss, worker):
    from staff.models import Department
    from tasks import services

    t = services.create_task(created_by=boss, title="Rack the new switch", assignees=[worker],
                             owner_department=Department.objects.get(code="ict"))
    services.open_task(t, boss)
    services.add_comment(t, boss, "Public note for everyone")
    services.add_comment(t, boss, "INTERNAL-SECRET budget is tight", internal=True)
    t.refresh_from_db()
    return t


# --------------------------------------------------------------------------
# H18: internal notes
# --------------------------------------------------------------------------
def test_h18_can_see_internal_matrix(task, boss, worker, deputy, make_user, work_seed):
    from tasks import services

    es = make_user("es@t.local", role="executive-secretary", dept="finance")
    reviewer = make_user("rev@t.local", role="officer", dept="hr")
    task.reviewer = reviewer
    task.save(update_fields=["reviewer"])
    assert services.can_see_internal(task, boss)          # creator
    assert services.can_see_internal(task, worker)        # assignee
    assert services.can_see_internal(task, reviewer)      # reviewer
    assert services.can_see_internal(task, es)            # ES
    assert not services.can_see_internal(task, deputy)    # lower rank than the creator
    senior = make_user("senior@t.local", role="executive-secretary", dept="ict")
    assert services.can_see_internal(task, senior)


def test_h18_internal_notes_hidden_from_uninvolved_web(task, boss, worker, deputy, client_for):
    url = reverse("tasks:detail", args=[task.pk])
    page = client_for(deputy).get(url)
    assert page.status_code == 200
    body = page.content.decode()
    assert "Public note for everyone" in body
    assert "INTERNAL-SECRET" not in body
    assert 'name="is_internal"' not in body  # no internal-note composer either
    # Timeline fragment and pinned list follow the same rule.
    frag = client_for(deputy).get(reverse("tasks:timeline_partial", args=[task.pk]), HTTP_HX_REQUEST="true")
    assert frag.status_code == 200 and "INTERNAL-SECRET" not in frag.content.decode()
    for who in (boss, worker):
        body = client_for(who).get(url).content.decode()
        assert "INTERNAL-SECRET" in body and 'name="is_internal"' in body


def test_h18_internal_notes_hidden_from_uninvolved_api(task, boss, worker, deputy):
    data = api_as(deputy).get(BASE + f"tasks/{task.pk}/").json()
    bodies = [c["body"] for c in data["comments"]]
    assert "Public note for everyone" in bodies
    assert not any("INTERNAL-SECRET" in b for b in bodies)
    assert not any("INTERNAL-SECRET" in (a["message"] or "") for a in data["activity"])
    involved = api_as(worker).get(BASE + f"tasks/{task.pk}/").json()
    assert any("INTERNAL-SECRET" in c["body"] for c in involved["comments"])


def test_h18_uninvolved_cannot_post_internal_note(task, deputy, client_for):
    from django.core.exceptions import PermissionDenied

    from tasks import services

    with pytest.raises(PermissionDenied):
        services.add_comment(task, deputy, "sneaky", internal=True)
    r = api_as(deputy).post(BASE + f"tasks/{task.pk}/comments/", {"body": "sneaky", "is_internal": True},
                            format="json")
    assert r.status_code == 403
    # An ordinary comment still works.
    services.add_comment(task, deputy, "Ordinary comment")


def test_n2_task_timeline_plain_get_redirects(task, boss, client_for):
    url = reverse("tasks:timeline_partial", args=[task.pk])
    r = client_for(boss).get(url)
    assert r.status_code == 302 and r["Location"] == reverse("tasks:detail", args=[task.pk])


# --------------------------------------------------------------------------
# H22, M9: staff pickers
# --------------------------------------------------------------------------
def test_staff_pickers_exclude_external_users(boss, worker, externals):
    from accounts.selectors import staff_users
    from messaging.forms import MemoForm
    from tasks import services
    from tasks.forms import TaskCreateForm
    from tickets.forms import AssignTicketForm

    ext_ids = {u.pk for u in externals}
    assert not ext_ids & set(staff_users().values_list("pk", flat=True))
    form = TaskCreateForm(user=boss)
    for name in ("reviewer", "assignees"):
        if name in form.fields:
            assert not ext_ids & set(form.fields[name].queryset.values_list("pk", flat=True)), name
    assert not ext_ids & set(services.assignable_users_for(boss).values_list("pk", flat=True))
    memo = MemoForm(user=boss)
    for name in ("target_user", "recipients"):
        assert not ext_ids & set(memo.fields[name].queryset.values_list("pk", flat=True)), name
    assert not ext_ids & set(AssignTicketForm().fields["assignee"].queryset.values_list("pk", flat=True))


def test_staff_pickers_exclude_external_users_api(boss, externals):
    ext_emails = {u.email for u in externals}
    people = api_as(boss).get(BASE + "tasks/people/").json()
    rows = people.get("results", people) if isinstance(people, dict) else people
    assert not ext_emails & {str(r) for r in rows} and "ext.local" not in str(people)
    memo_people = api_as(boss).get(BASE + "memos/people/")
    assert memo_people.status_code == 200 and "ext.local" not in memo_people.content.decode()


def test_h22_services_refuse_external_assignee_and_reviewer(boss, externals):
    from django.core.exceptions import ValidationError

    from tasks import services

    inst_user = externals[0]
    with pytest.raises(ValidationError):
        services.create_task(created_by=boss, title="Bad reviewer", reviewer=inst_user)
    t = services.create_task(created_by=boss, title="Good task")
    with pytest.raises(ValidationError):
        services.add_assignee(t, boss, inst_user)


def test_m9_memo_audience_is_staff_only(boss, worker, externals):
    """An "all staff" memo never reaches institution users or RPs; memo
    approvers come from staff only."""
    from types import SimpleNamespace

    from messaging.models import Memo
    from messaging.routing import _active_users, resolve_memo_audience

    ext = {u.pk for u in externals}
    assert not ext & set(_active_users().values_list("pk", flat=True))
    memo = SimpleNamespace(sender_id=boss.pk, visibility=Memo.ALL_STAFF, ALL_STAFF=Memo.ALL_STAFF,
                           SPECIFIC_USER=Memo.SPECIFIC_USER, ALL_DIRECTORS=Memo.ALL_DIRECTORS,
                           DEPARTMENT=Memo.DEPARTMENT, ROLE=Memo.ROLE, INDIVIDUALS=Memo.INDIVIDUALS)
    audience = set(resolve_memo_audience(memo).values_list("pk", flat=True))
    assert worker.pk in audience and not ext & audience
