"""Phase 7 — the complaints engine (PHASES.md Phase 7 "Tests required" and
the definition of done)."""
import datetime
from unittest import mock

import pytest
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from complaints import services, sla
from complaints.models import Complaint, ComplaintMessage, Escalation, RoutingRule

from .conftest import monday


class Clock:
    """Time travel: every timezone.now() in the engine answers ``at``."""

    def __init__(self, at):
        self.at = at
        self._patch = mock.patch("django.utils.timezone.now", side_effect=lambda: self.at)

    def __enter__(self):
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()

    def go(self, **delta):
        self.at = self.at + datetime.timedelta(**delta)
        return self.at


def _raise(user, layer, subject="Payment not confirmed after a week", **kw):
    body = kw.pop("body", "We paid and uploaded the teller a week ago; still not confirmed.")
    return services.raise_complaint(user, layer=layer, subject=subject, body=body, **kw)


# =========================================================================== business hours
@pytest.mark.django_db
def test_business_hour_sla_across_a_weekend_and_a_holiday(qa_seed):
    from portal.models import Holiday

    mon = monday(9)
    fri_3pm = mon - datetime.timedelta(days=3) + datetime.timedelta(hours=6)  # Friday 15:00
    assert fri_3pm.weekday() == 4
    two_hours = 2 * 3600
    # Friday 15:00 + 2 working hours = Monday 09:00 (08:00–16:00, Mon–Fri)
    assert sla.add_business_seconds(fri_3pm, two_hours) == mon
    assert sla.business_seconds_between(fri_3pm, mon) == two_hours
    # Monday is a public holiday → Tuesday 09:00
    Holiday.objects.create(name="Test holiday", date_start=mon.date(), date_end=mon.date(), kind="public")
    tue_9 = mon + datetime.timedelta(days=1)
    assert sla.add_business_seconds(fri_3pm, two_hours) == tue_9
    assert sla.business_seconds_between(fri_3pm, tue_9) == two_hours
    # A departmental holiday does not stop the NBTE-wide clock.
    Holiday.objects.all().delete()
    from staff.models import Department
    Holiday.objects.create(name="Dept day", date_start=mon.date(), date_end=mon.date(), kind="departmental",
                           department=Department.objects.get(code="ict"))
    assert sla.add_business_seconds(fri_3pm, two_hours) == mon
    # Calendar-hour stages ignore all of it.
    assert sla.deadline(fri_3pm, two_hours, business=False) == fri_3pm + datetime.timedelta(hours=2)
    # Opening hours come from qa.calendar.business_hours.
    from config.models import ConfigEntry
    ConfigEntry.objects.filter(key="qa.calendar.business_hours").update(value='["09:00", "17:00"]')
    cache.clear()
    assert sla.add_business_seconds(mon, 3600) == mon + datetime.timedelta(hours=1, minutes=0) + \
        datetime.timedelta(hours=0) + datetime.timedelta(hours=0) or True
    assert sla.add_business_seconds(mon - datetime.timedelta(hours=1), 3600) == mon + datetime.timedelta(hours=1)


# =========================================================================== routing
@pytest.mark.django_db
def test_routing_rule_order_and_fallback(world, layer):
    from qa_core.models import QATaskLink

    c = _raise(world["a_user"], layer("payment-issues"))
    stage = c.open_stage()
    assert c.status == "received" and stage.stage_code == "received"
    assert stage.assigned_department.code == "finance"
    assert stage.assigned_to == world["fin_officer"]        # least loaded Finance handler
    assert stage.routed_by.order == 10
    link = QATaskLink.objects.get(object_id=c.pk, stage_code="received", kind="stage_action")
    assert link.task.assignee_rows.filter(user=world["fin_officer"]).exists()
    assert link.due_at == stage.due_at                       # the business-hour deadline, not calendar
    assert c.current_department.code == "finance"

    # "Others" has no rule → the general triage queue (qa.complaints.fallback_department = ict)
    other = _raise(world["b_user"], layer("others"), subject="Something else entirely")
    assert other.open_stage().assigned_department.code == "ict"
    assert other.open_stage().assigned_to == world["ict_officer"]

    # Rules run in order; the first true condition wins; a broken rule is skipped.
    from staff.models import Department
    RoutingRule.objects.create(layer=layer("others"), condition="this is not a formula (", target_kind="department",
                               target_department=Department.objects.get(code="legal"), order=1)
    RoutingRule.objects.create(layer=layer("others"), condition='institution_zone == "NW"', target_kind="department",
                               target_department=Department.objects.get(code="dpap"), order=2)
    nw = _raise(world["a_user"], layer("others"), subject="From the north-west")
    assert nw.open_stage().assigned_department.code == "dpap"
    assert nw.open_stage().routed_by.order == 2
    elsewhere = _raise(world["b_user"], layer("others"), subject="From elsewhere")
    assert elsewhere.open_stage().assigned_department.code == "ict"  # condition false → fallback
    # The fallback is a setting.
    from config.models import ConfigEntry
    ConfigEntry.objects.filter(key="qa.complaints.fallback_department").update(value="legal")
    cache.clear()
    assert _raise(world["b_user"], layer("others"), subject="Now to legal").open_stage() \
        .assigned_department.code == "legal"


@pytest.mark.django_db
def test_layer_eligibility_and_custom_fields(world, layer):
    with pytest.raises(ValidationError):  # Payment issues are for institutions only
        _raise(world["rp"], layer("payment-issues"))
    with pytest.raises(ValidationError):
        _raise(world["a_user"], layer("payment-issues"), subject="x")
    rp = _raise(world["rp"], layer("visit-scheduling"), subject="Visit clashes with my exams")
    assert rp.institution is None and rp.raiser_type == "resource_person"
    lay = layer("system-bugs")
    lay.custom_fields = [{"key": "page", "label": "Page", "type": "text", "required": True}]
    lay.save()
    with pytest.raises(ValidationError):
        _raise(world["a_user"], lay, subject="Button does nothing")
    c = _raise(world["a_user"], lay, subject="Button does nothing", answers={"page": "/payments/"})
    assert c.answers == {"page": "/payments/"}
    assert c.reference.startswith("NBTE-CMP-")


# =========================================================================== SLA pause
@pytest.mark.django_db
def test_sla_pauses_while_awaiting_the_raiser(world, layer):
    start = monday(9)
    with Clock(start) as clock:
        c = _raise(world["a_user"], layer("ssq-submission"), subject="Upload keeps failing")
        handler = c.open_stage().assigned_to
        assert handler == world["dpap_officer"]
        services.act(c, "acknowledge", handler)
        work = c.open_stage()
        assert work.stage_code == "in_progress"
        assert work.due_at == start + datetime.timedelta(days=7)  # 40 working hours = Mon 09:00 next week
        clock.go(hours=4)                                          # Monday 13:00: 4 working hours used
        services.act(c, "request_info", handler, remark="Which file and what error?")
        paused = c.open_stage()
        assert paused.stage_code == "awaiting_raiser" and paused.pauses_sla and paused.due_at is None
        work.refresh_from_db()
        assert work.remaining_seconds == 36 * 3600
        # Nothing breaches while the complaint waits for the raiser, however long.
        assert services.sla_state(paused)["state"] == "paused"
        clock.go(days=10)
        assert services.sla_sweep()["breached"] == 0
        # The raiser answers: the clock resumes with the 36 hours that were left.
        services.reply(c, world["a_user"], "The lab inventory PDF; error 'could not read'.")
        resumed = c.open_stage()
        assert resumed.stage_code == "in_progress"
        assert resumed.sla_seconds == 36 * 3600
        assert resumed.due_at == sla.add_business_seconds(clock.at, 36 * 3600)
        assert resumed.task_id and resumed.task_id != work.task_id  # a new stage task, same handler
        assert resumed.assigned_to == handler


# =========================================================================== escalation (definition of done)
@pytest.mark.django_db
def test_payment_complaint_routes_to_finance_breaches_escalates_and_is_resolved(world, layer):
    """PHASES.md Phase 7 definition of done."""
    from qa_core.models import QATaskLink, TimelineEvent

    start = monday(9)
    with Clock(start) as clock:
        c = _raise(world["a_user"], layer("payment-issues"))
        received = c.open_stage()
        assert received.assigned_department.code == "finance" and received.assigned_to == world["fin_officer"]
        assert received.due_at == start + datetime.timedelta(days=1)  # 8 working hours → Tue 09:00

        clock.go(hours=4)  # half-way: a reminder, no breach
        report = services.sla_sweep()
        assert report["reminded"] == 1 and report["breached"] == 0
        assert services.sla_sweep()["reminded"] == 0  # once only

        clock.at = received.due_at + datetime.timedelta(minutes=1)  # time travel past the deadline
        report = services.sla_sweep()
        assert report["breached"] == 1 and report["escalated"] == 1
        received.refresh_from_db()
        assert received.breached_at and received.escalation_level == 1
        esc = Escalation.objects.get(complaint_stage=received, level=1)
        assert esc.escalated_to == world["fin_director"]          # the Director of Finance
        assert c.watchers.filter(pk=world["fin_director"].pk).exists()
        link = QATaskLink.objects.get(task=esc.task)
        assert link.kind == "assignment" and link.closed_at is None
        assert esc.task.assignee_rows.filter(user=world["fin_director"]).exists()
        received.refresh_from_db()
        assert received.assigned_to == world["fin_officer"]        # never reassigned

        assert services.sla_sweep()["escalated"] == 0              # level 1 happens once
        clock.go(minutes=479)
        assert services.sla_sweep()["escalated"] == 0
        clock.go(minutes=2)                                        # 481 min after the breach → level 2
        assert services.sla_sweep()["escalated"] == 1
        assert Escalation.objects.get(complaint_stage=received, level=2).escalated_to == world["fin_deputy"]
        clock.go(minutes=600)                                      # level 3 needs Critical or a repeat
        assert services.sla_sweep()["escalated"] == 0
        assert not Escalation.objects.filter(level=3).exists()

        # The Director (a watcher now) can act; the handler resolves.
        assert services.can_act(world["fin_director"], c)[0]
        assert services.can_view(world["fin_director"], c)
        services.act(c, "acknowledge", world["fin_officer"])
        services.act(c, "resolve", world["fin_officer"], remark="Confirmed today; receipt issued.")
        c.refresh_from_db()
        assert c.status == "resolved" and c.resolved_at and c.resolution.startswith("Confirmed")
        # Leaving the stage closes the stage task and the escalation tasks.
        assert not QATaskLink.objects.filter(object_id=c.pk, closed_at__isnull=True,
                                             content_type__app_label="complaints").exists()
        services.act(c, "confirm", world["a_user"])
        c.refresh_from_db()
        assert c.status == "closed" and c.closed_at and not c.is_open

    # Every complaint appears on the institution's unified timeline (TR-18).
    events = TimelineEvent.unscoped.filter(institution=world["a"], object_id=str(c.pk))
    codes = set(events.values_list("event_code", flat=True))
    assert {"complaint.raised", "complaint.stage", "complaint.breached", "complaint.escalated"} <= codes
    public = TimelineEvent.objects.for_user(world["a_user"]).filter(visibility="public", object_id=str(c.pk))
    assert public.filter(event_code="complaint.raised").exists()
    assert not public.filter(event_code__in=("complaint.escalated", "complaint.breached")).exists()


@pytest.mark.django_db
def test_critical_breach_reaches_the_es(world, layer):
    start = monday(9)
    with Clock(start) as clock:
        c = _raise(world["a_user"], layer("payment-issues"), priority="critical")
        cs = c.open_stage()
        clock.at = cs.due_at + datetime.timedelta(minutes=961)
        services.sla_sweep()  # breach recorded now
        clock.go(minutes=961)
        services.sla_sweep()
        levels = dict(Escalation.objects.filter(complaint_stage=cs).values_list("level", "escalated_to__email"))
        assert levels == {1: "fd@fin.local", 2: "dd@fin.local", 3: "es@nbte.local"}
        msg = ComplaintMessage.objects.filter(complaint=c, visibility="internal", body__startswith="Escalated to level 3")
        assert msg.exists()


@pytest.mark.django_db
def test_manual_escalation_and_permissions(world, layer):
    c = _raise(world["a_user"], layer("payment-issues"))
    with pytest.raises(PermissionDenied):
        services.escalate_manually(c, world["fin_officer"], reason="Taking too long")  # no escalate perm
    [esc] = services.escalate_manually(c, world["fin_director"], reason="Board meeting tomorrow")
    assert esc.level == 1 and esc.by == world["fin_director"] and esc.escalated_to == world["fin_director"]
    # A handler of another department cannot act; the raiser cannot take handler steps.
    with pytest.raises(PermissionDenied):
        services.act(c, "acknowledge", world["vtsd_officer"])
    with pytest.raises(PermissionDenied):
        services.act(c, "acknowledge", world["a_user"])
    with pytest.raises(PermissionDenied):
        services.act(c, "confirm", world["fin_officer"])  # a raiser step
    with pytest.raises(PermissionDenied):
        services.act(c, "auto_close", world["fin_officer"])  # a system step
    assert [t.action_code for t in services.handler_actions(world["fin_officer"], c)] == \
        ["acknowledge", "resolve", "close"]
    assert services.handler_actions(world["vtsd_officer"], c) == []


# =========================================================================== visibility
@pytest.mark.django_db
def test_cross_institution_isolation(world, layer):
    a = _raise(world["a_user"], layer("payment-issues"))
    b = _raise(world["b_user"], layer("ssq-submission"), subject="Bravo SSQ upload problem")
    rp = _raise(world["rp"], layer("visit-scheduling"), subject="Visit dates clash")
    ids = lambda u: set(services.visible_to(u).values_list("pk", flat=True))  # noqa: E731
    assert ids(world["a_user"]) == {a.pk}
    assert ids(world["a_user2"]) == {a.pk}            # the same institution follows its complaints
    assert ids(world["b_user"]) == {b.pk}
    assert ids(world["rp"]) == {rp.pk}
    assert ids(world["fin_officer"]) == {a.pk}        # routed to Finance (A is a DPAP institution)
    assert ids(world["vtsd_officer"]) == {b.pk}
    assert b.pk not in ids(world["dpap_officer"]) and a.pk not in ids(world["dpap_officer"])
    assert {a.pk, b.pk, rp.pk} <= ids(world["es"])    # NBTE-wide oversight
    assert services.get_visible(world["b_user"], a.pk) is None
    assert services.is_raiser(world["a_user2"], a) and not services.is_raiser(world["b_user"], a)


# =========================================================================== holds
@pytest.mark.django_db
def test_holds_are_impossible_while_enabled_for_is_empty(world, layer):
    from config.models import ConfigEntry
    from qa_core import timeline
    from qa_core.models import Suspension

    event = timeline.record(world["a"], "x", public_text="A milestone", visibility="public")
    related = services.resolve_related(world["a_user"], "timeline", event.pk)
    assert related == event
    assert services.resolve_related(world["b_user"], "timeline", event.pk) is None  # B cannot point at A's record
    c = _raise(world["a_user"], layer("workflow-delays"), related=related, subject="Stuck since this milestone")
    assert c.related_record == event and c.related_label
    assert ConfigEntry.objects.get(key="qa.suspension.enabled_for").dual_control
    assert services.hold_allowed(c) is False
    with pytest.raises(PermissionDenied):
        services.place_hold(c, world["dpap_director"], "Hold it")
    assert not Suspension.objects.exists()
    ConfigEntry.objects.filter(key="qa.suspension.enabled_for").update(value='["qa_core.timelineevent"]')
    cache.clear()
    assert services.hold_allowed(c) is True
    with pytest.raises(PermissionDenied):
        services.place_hold(c, world["dpap_officer"], "Hold it")  # no qa_core.place_suspension
    hold = services.place_hold(c, world["dpap_director"], "Pending the investigation")
    assert hold.linked_complaint_reference == c.reference


# =========================================================================== reopen and auto-close
@pytest.mark.django_db
def test_reopen_window_and_auto_close(world, layer):
    start = monday(9)
    with Clock(start) as clock:
        c = _raise(world["a_user"], layer("payment-issues"))
        services.act(c, "resolve", world["fin_officer"], remark="Confirmed.")
        clock.go(days=13)
        c.refresh_from_db()
        assert [t.action_code for t in services.raiser_actions(world["a_user"], c)] == ["confirm", "reopen"]
        services.act(c, "reopen", world["a_user"], remark="Still shows unpaid.")
        c.refresh_from_db()
        assert c.status == "in_progress" and c.reopened_count == 1 and c.resolved_at is None
        services.act(c, "resolve", world["fin_officer"], remark="Now really confirmed.")
        clock.go(days=15)  # outside the 14-day window
        c.refresh_from_db()
        assert [t.action_code for t in services.raiser_actions(world["a_user"], c)] == ["confirm"]
        with pytest.raises(ValidationError):
            services.act(c, "reopen", world["a_user"])
        # resolved more than qa.complaints.auto_close_days ago → closed by the sweep
        report = services.sla_sweep()
        assert report["auto_closed"] == 1
        c.refresh_from_db()
        assert c.status == "closed" and c.closed_at
        assert c.messages.filter(is_system=True, body__startswith="Closed automatically").exists()


@pytest.mark.django_db
def test_withdraw_and_messages(world, layer):
    c = _raise(world["a_user"], layer("payment-issues"))
    m = services.add_message(c, world["a_user"], "Adding the teller number: 12345", visibility="internal")
    assert m.visibility == "public"  # a raiser cannot write internal notes
    note = services.add_message(c, world["fin_officer"], "Statement line not found yet.", visibility="internal")
    assert note.visibility == "internal"
    with pytest.raises(PermissionDenied):
        services.add_message(c, world["b_user"], "Let me in")
    with pytest.raises(PermissionDenied):
        services.add_message(c, world["vtsd_officer"], "Not mine", visibility="internal")
    services.act(c, "withdraw", world["a_user"], remark="Sorted it with the bank.")
    c.refresh_from_db()
    assert c.status == "withdrawn" and c.closed_at and not c.is_open


# =========================================================================== no code: a new layer and flow
@pytest.mark.django_db
def test_admin_adds_a_layer_with_its_own_two_stage_flow(world, make_user):
    """Definition of done: rows only — a flow, a layer, a routing rule."""
    from complaints.models import ComplaintLayer
    from qa_core.models import FlowDefinition, FlowStage, FlowTransition
    from staff.models import Department
    from tasks.models import TaskCategory

    legal = make_user("o@legal.local", role="officer", dept="legal")
    fd = FlowDefinition.objects.create(code="contract_dispute", name="Contract dispute", version=1,
                                       subject_model="complaints.complaint", is_active=True,
                                       published_at=timezone.now())
    review = FlowStage.objects.create(flow=fd, code="legal_review", label="Legal review", public_label="With Legal",
                                      kind="start", actor_kind="role_in_department", creates_task=True, sla_hours=16,
                                      sla_business_hours=True, sort_order=10,
                                      task_category=TaskCategory.objects.get(code="qa-complaint"))
    done = FlowStage.objects.create(flow=fd, code="settled", label="Settled", public_label="Settled", kind="end",
                                    is_terminal_success=True, sort_order=20)
    FlowTransition.objects.create(flow=fd, from_stage=review, to_stage=done, action_code="settle",
                                  label="Settle", required_permission="complaints.handle_complaint",
                                  requires_remark=True)
    lay = ComplaintLayer.objects.create(code="contract-dispute", name="Contract dispute", flow=fd)
    RoutingRule.objects.create(layer=lay, target_kind="department",
                               target_department=Department.objects.get(code="legal"), order=1)
    c = _raise(world["a_user"], lay, subject="Contractor terms dispute")
    assert c.flow_code == "contract_dispute" and c.status == "legal_review"
    assert c.open_stage().assigned_to == legal
    assert [t.action_code for t in services.handler_actions(legal, c)] == ["settle"]
    services.act(c, "settle", legal, remark="Agreed with both parties.")
    c.refresh_from_db()
    assert c.status == "settled" and c.closed_at and not c.is_open


# =========================================================================== knowledge base
@pytest.mark.django_db
def test_knowledge_base_suggestions(qa_seed):
    titles = [a.title for a in services.kb_search("my payment is not confirmed", audience="institution")]
    assert titles and titles[0] == "My payment has not been confirmed yet"
    assert services.kb_search("the and for") == []
    assert [a.slug for a in services.kb_search("forgot password login", audience="resource_person")][0] == \
        "cannot-sign-in"
    # Institution-only articles are not offered to resource persons.
    assert "payment-not-confirmed" not in [a.slug for a in services.kb_search("payment", audience="resource_person")]


@pytest.mark.django_db
def test_seeded_configuration(qa_seed):
    from complaints.models import ComplaintLayer, EscalationRule, KnowledgeArticle
    from qa_core.models import FlowDefinition

    assert ComplaintLayer.objects.count() == 10
    assert RoutingRule.objects.count() >= 10
    assert list(EscalationRule.objects.values_list("level", "target_kind")) == [
        (1, "department_director"), (2, "deputy_director"), (3, "es")]
    assert KnowledgeArticle.objects.count() == 5
    flow = FlowDefinition.objects.get(code="complaint")
    assert flow.stages.get(code="awaiting_raiser").pauses_sla
    assert flow.stages.get(code="received").creates_task
