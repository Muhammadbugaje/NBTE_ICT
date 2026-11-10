"""v2.1 X9–X11 — complaint routing to the handling department (Master Plan §13.21).

Category → handling department; only the director of that department, the
assigned officer, the ES / super admin and the read-only watchers (the
director overseeing the institution, its ICT supervisor) see a complaint;
the director assigns one of his own staff (task + time-boxed grant); the
officer submits for review; only the director resolves or returns; the
institution confirms or reopens (back to the same officer).
"""
import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from api.tests.conftest import BASE, bearer, staff_api  # noqa: F401  (fixture)
from complaints import services
from complaints.models import Complaint, ComplaintLayer

pytestmark = pytest.mark.django_db
QA = BASE + "qa/"


def _raise(user, code="payment-issues", subject="Payment not confirmed after a week"):
    return services.raise_complaint(user, layer=ComplaintLayer.objects.get(code=code), subject=subject,
                                    body="We paid and uploaded the teller a week ago; still not confirmed.")


def _ids(user):
    return set(services.visible_to(user).values_list("pk", flat=True))


def _notified(user):
    from notifications.models import Notification
    return list(Notification.objects.filter(recipient=user).values_list("title", flat=True))


@pytest.fixture
def ict_supervisor(world, make_user):
    """An ICT officer supervising institution A (open oversight_ict assignment, X2)."""
    from assignments import work

    sup = make_user("sup@ict.local", role="officer", dept="ict")
    work.assign(world["a"], "oversight_ict", sup, assigned_by=None, accept_now=True, notify_assignee=False)
    return sup


def _assign(c, director, officer):
    return services.act(c, "assign", director, officer=officer)


def _to_review(world, c):
    _assign(c, world["fin_director"], world["fin_officer"])
    return services.act(c, "submit_for_review", world["fin_officer"],
                        remark="The teller matches the statement; confirm the payment.")


# =========================================================================== X9: category → department
def test_seeded_flow_v2_and_handling_departments(world):
    from qa_core import flow
    from qa_core.models import FlowDefinition

    assert flow.current_version("complaint").version == 2
    assert FlowDefinition.objects.filter(code="complaint", version=1).exists()  # v1 kept for open complaints
    layers = {lay.code: getattr(lay.handling_department, "code", None) for lay in ComplaintLayer.objects.all()}
    assert layers["payment-issues"] == "finance"
    assert layers["portal-access"] == "ict" and layers["workflow-delays"] == "ict"
    assert layers["ssq-submission"] is None  # the institution's handling department (routing rule)


def test_category_maps_to_department_and_director_only(world, ict_supervisor):
    c = _raise(world["a_user"])
    assert services.is_directed(c) and c.status == "received"
    assert c.current_department.code == "finance"
    stage = c.open_stage()
    assert stage.assigned_to == world["fin_director"]          # the director, not a Finance officer
    assert c.current_assignee == world["fin_director"]
    # Layer default without a routing rule: the layer's handling department wins over the fallback.
    from complaints.models import RoutingRule
    from staff.models import Department
    lay = ComplaintLayer.objects.get(code="others")
    lay.handling_department = Department.objects.get(code="finance")
    lay.save()
    assert not RoutingRule.objects.filter(layer=lay).exists()
    other = _raise(world["a_user"], "others", subject="Something about a receipt")
    assert other.current_department.code == "finance"
    # SSQ: the institution's handling department (A is handled by DPAP).
    ssq = _raise(world["a_user"], "ssq-submission", subject="SSQ upload fails")
    assert ssq.current_department.code == "dpap" and ssq.current_assignee == world["dpap_director"]


def test_uninvolved_staff_get_404_and_are_not_notified(world, client_for, ict_supervisor):
    c = _raise(world["a_user"])
    assert _notified(world["fin_director"])                    # the handling director is told
    for key in ("fin_officer", "fin_deputy", "vtsd_officer", "dpap_officer", "ict_officer"):
        user = world[key]
        assert c.pk not in _ids(user), key
        assert services.get_visible(user, c.pk) is None
        assert not [t for t in _notified(user) if c.reference in t], key
        r = client_for(user).get(f"/qa/complaints/{c.pk}/")
        assert r.status_code == 404, key
        r = client_for(user).post(f"/qa/complaints/{c.pk}/act/assign/", {"officer": user.pk})
        assert r.status_code == 404, key
    # Oversight-board holders no longer see every complaint (only ES, super admin, Director of ICT).
    assert services.sees_everything(world["es"])
    assert c.pk in _ids(world["es"])


def test_overseeing_director_and_ict_supervisor_read_only(world, client_for, ict_supervisor):
    c = _raise(world["a_user"])
    for watcher in (world["dpap_director"], ict_supervisor):   # A is a DPAP institution
        assert c.pk in _ids(watcher)
        ok, why = services.can_act(watcher, c)
        assert not ok and "read" in why
        assert services.handler_actions(watcher, c) == []
        rights = services.detail_rights(watcher, c)
        assert rights["read_only"] and rights["watch_reason"]
        with pytest.raises(PermissionDenied):
            services.act(c, "acknowledge", watcher)
        with pytest.raises(PermissionDenied):
            _assign(c, watcher, world["fin_officer"])
        r = client_for(watcher).get(f"/qa/complaints/{c.pk}/")
        assert r.status_code == 200
        assert rights["watch_reason"] in r.content.decode()
    assert [t for t in _notified(world["dpap_director"]) if c.reference in t]  # watcher summary
    # Settings switch the watchers off.
    from config.models import ConfigEntry
    from django.core.cache import cache
    ConfigEntry.objects.filter(key="qa.complaints.watch_ict_supervisor").update(value="false")
    ConfigEntry.objects.filter(key="qa.complaints.watch_overseeing_director").update(value="false")
    cache.clear()
    assert c.pk not in _ids(ict_supervisor) and c.pk not in _ids(world["dpap_director"])


# =========================================================================== X10: director assigns
def test_director_assigns_own_staff_with_task_and_grant(world, make_user, client_for):
    from assignments.models import AccessGrant, WorkAssignment
    from core.models import AuditLogEntry

    c = _raise(world["a_user"])
    # Forged POST naming someone of another department: refused by the server.
    r = client_for(world["fin_director"]).post(f"/qa/complaints/{c.pk}/act/assign/",
                                               {"officer": world["dpap_officer"].pk})
    assert r.status_code == 422
    with pytest.raises(PermissionDenied):
        _assign(c, world["fin_director"], world["dpap_officer"])
    with pytest.raises(ValidationError):
        services.act(c, "assign", world["fin_director"])        # no officer
    # Another Finance staff member (not the director) cannot assign.
    with pytest.raises(PermissionDenied):
        _assign(c, world["fin_deputy"], world["fin_officer"])
    c.refresh_from_db()
    assert c.status == "received" and c.assigned_officer is None

    r = client_for(world["fin_director"]).post(f"/qa/complaints/{c.pk}/act/assign/",
                                               {"officer": world["fin_officer"].pk})
    assert r.status_code in (200, 302)
    c.refresh_from_db()
    assert c.status == "in_progress" and c.assigned_officer == world["fin_officer"]
    assert c.open_stage().assigned_to == world["fin_officer"]
    wa = WorkAssignment.objects.for_subject(c).get(kind="complaint_task", status__in=WorkAssignment.OPEN)
    assert wa.assignee == world["fin_officer"] and wa.assigned_by == world["fin_director"]
    assert AccessGrant.objects.live(world["fin_officer"]).filter(source_id=str(wa.pk)).exists()
    assert AuditLogEntry.objects.filter(model_label="complaints.Complaint", object_pk=str(c.pk),
                                        changes__step="complaint_assigned").exists()
    # The officer now sees it; other Finance staff still do not.
    assert c.pk in _ids(world["fin_officer"]) and c.pk not in _ids(world["fin_deputy"])
    assert [t for t in _notified(world["fin_officer"]) if c.reference in t]


def test_es_can_assign_anyone(world):
    c = _raise(world["a_user"])
    services.act(c, "assign", world["es"], officer=world["vtsd_officer"])
    c.refresh_from_db()
    assert c.assigned_officer == world["vtsd_officer"] and c.status == "in_progress"
    assert c.pk in _ids(world["vtsd_officer"])
    assert services.can_act(world["vtsd_officer"], c)[0]


# =========================================================================== X11: review, resolve, return
def test_officer_submits_director_returns_then_resolves(world):
    from assignments.models import AccessGrant, WorkAssignment

    c = _raise(world["a_user"])
    _assign(c, world["fin_director"], world["fin_officer"])
    c.refresh_from_db()
    # Officer steps: the director cannot submit for review; the officer cannot resolve.
    assert [t.action_code for t in services.handler_actions(world["fin_officer"], c)] == \
        ["submit_for_review", "request_info"]
    assert "submit_for_review" not in [t.action_code for t in services.handler_actions(world["fin_director"], c)]
    with pytest.raises(PermissionDenied):
        services.act(c, "submit_for_review", world["fin_director"], remark="I will do it myself")
    with pytest.raises(ValidationError):
        services.act(c, "submit_for_review", world["fin_officer"], remark="")
    services.act(c, "submit_for_review", world["fin_officer"], remark="Teller matches; confirm it.")
    c.refresh_from_db()
    assert c.status == "director_review" and c.submitted_for_review_at
    assert c.open_stage().assigned_to == world["fin_director"]
    assert [t for t in _notified(world["fin_director"]) if "review" in t.lower()]
    with pytest.raises(PermissionDenied):
        services.act(c, "resolve", world["fin_officer"], remark="Done")
    # Return needs a remark and goes back to the same officer.
    with pytest.raises(ValidationError):
        services.act(c, "return_to_officer", world["fin_director"], remark="")
    services.act(c, "return_to_officer", world["fin_director"], remark="Attach the bank statement line.")
    c.refresh_from_db()
    assert c.status == "in_progress" and c.open_stage().assigned_to == world["fin_officer"]
    assert c.submitted_for_review_at is None
    services.act(c, "submit_for_review", world["fin_officer"], remark="Statement line attached.")
    services.act(c, "resolve", world["fin_director"], remark="Payment confirmed; receipt issued.")
    c.refresh_from_db()
    assert c.status == "resolved" and c.resolution.startswith("Payment confirmed")
    # The work is complete and its grants end with it (X5).
    assert not WorkAssignment.objects.for_subject(c).filter(status__in=WorkAssignment.OPEN).exists()
    assert not AccessGrant.objects.live(world["fin_officer"]).filter(
        source_ct__model="workassignment").exists()
    assert [t for t in _notified(world["a_user"]) if "resolved" in t.lower()]


def test_institution_confirms_or_reopens_to_the_same_officer(world):
    from assignments.models import WorkAssignment

    c = _raise(world["a_user"])
    _to_review(world, c)
    services.act(c, "resolve", world["fin_director"], remark="Confirmed.")
    c.refresh_from_db()
    assert [t.action_code for t in services.raiser_actions(world["a_user"], c)] == ["confirm", "reopen"]
    services.act(c, "reopen", world["a_user"], remark="Still shows unpaid.")
    c.refresh_from_db()
    assert c.status == "in_progress" and c.assigned_officer == world["fin_officer"]
    assert c.open_stage().assigned_to == world["fin_officer"]
    assert WorkAssignment.objects.for_subject(c).filter(assignee=world["fin_officer"],
                                                        status__in=WorkAssignment.OPEN).exists()
    services.act(c, "submit_for_review", world["fin_officer"], remark="Now confirmed in the ledger.")
    services.act(c, "resolve", world["fin_director"], remark="Confirmed again.")
    services.act(c, "confirm", world["a_user"])
    c.refresh_from_db()
    assert c.status == "closed" and c.closed_at


def test_director_closes_duplicate_and_refers(world, make_user):
    c = _raise(world["a_user"])
    with pytest.raises(PermissionDenied):
        services.act(c, "close", world["fin_officer"], remark="Duplicate")
    services.act(c, "close", world["fin_director"], remark="Duplicate of an earlier complaint.")
    c.refresh_from_db()
    assert c.status == "closed"

    from staff.models import Department
    d = _raise(world["a_user"], subject="Wrong amount on the bill")
    _assign(d, world["fin_director"], world["fin_officer"])
    services.act(d, "refer", world["fin_director"], remark="DPAP owns this fee.",
                 refer_department=Department.objects.get(code="dpap"))
    d.refresh_from_db()
    assert d.status == "referred" and d.current_department.code == "dpap" and d.assigned_officer is None
    assert d.open_stage().assigned_to == world["dpap_director"]
    services.act(d, "assign", world["dpap_director"], officer=world["dpap_officer"])
    d.refresh_from_db()
    assert d.status == "in_progress" and d.assigned_officer == world["dpap_officer"]
    assert d.current_department.code == "dpap"
    assert d.pk not in _ids(world["fin_officer"])


def test_open_v1_complaints_stay_on_v1(world):
    from qa_core.models import FlowDefinition

    FlowDefinition.objects.filter(code="complaint", version=2).update(is_active=False)
    old = _raise(world["a_user"])
    FlowDefinition.objects.filter(code="complaint", version=2).update(is_active=True)
    assert not services.is_directed(old)
    from qa_core import flow
    assert flow.instance_for(old).flow_version.version == 1
    new = _raise(world["a_user"], subject="A second payment problem")
    assert services.is_directed(new)


# =========================================================================== API matches the website
def test_api_matches_the_website(world, staff_api, ict_supervisor):  # noqa: F811
    c = _raise(world["a_user"])

    def get(user, path):
        return bearer(staff_api, user).get(QA + path)

    def post(user, path, data):
        return bearer(staff_api, user).post(QA + path, data, format="json")

    # Uninvolved staff: 404, not in the list.
    assert get(world["fin_officer"], f"complaints/{c.pk}/").status_code == 404
    rows = get(world["fin_officer"], "complaints/?tab=all").json()["results"]
    assert c.pk not in [r["id"] for r in rows]
    # Watcher: read-only.
    detail = get(ict_supervisor, f"complaints/{c.pk}/").json()
    assert detail["rights"]["read_only"] is True and detail["rights"]["actions"] == []
    # Director: assign offered with his own staff only.
    detail = get(world["fin_director"], f"complaints/{c.pk}/").json()
    codes = [a["code"] for a in detail["rights"]["actions"]]
    assert "assign" in codes
    officer_ids = {p["id"] for p in detail["rights"]["officers"]}
    assert world["fin_officer"].pk in officer_ids and world["dpap_officer"].pk not in officer_ids
    r = post(world["fin_director"], f"complaints/{c.pk}/act/", {"action": "assign",
                                                                "officer": world["dpap_officer"].pk})
    assert r.status_code == 403
    r = post(world["fin_director"], f"complaints/{c.pk}/act/", {"action": "assign",
                                                                "officer": world["fin_officer"].pk})
    assert r.status_code == 200, r.content
    assert r.json()["complaint"]["assigned_officer"]["id"] == world["fin_officer"].pk
    r = post(world["fin_officer"], f"complaints/{c.pk}/act/", {"action": "submit_for_review",
                                                               "remark": "Ready: confirm the payment."})
    assert r.status_code == 200, r.content
    r = post(world["fin_officer"], f"complaints/{c.pk}/act/", {"action": "resolve", "remark": "Done here"})
    assert r.status_code == 400   # not one of his actions
    r = post(world["fin_director"], f"complaints/{c.pk}/act/", {"action": "resolve",
                                                                "remark": "Payment confirmed."})
    assert r.status_code == 200, r.content
    assert Complaint.unscoped.get(pk=c.pk).status == "resolved"
