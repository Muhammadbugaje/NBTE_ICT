"""Flow-engine fixture for the seeded payment flow (v1.1, Amendment 1):
only `final_confirm` reaches `confirmed`, and it needs the final-approver role."""
import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError

from config.models import ConfigEntry
from qa_core import flow
from qa_core.models import FlowDefinition, FlowEvent, FlowTransition


@pytest.fixture
def payment(qa_seed):
    return FlowDefinition.objects.get(code="payment")


def test_payment_vocabulary_is_fixed(payment):
    codes = set(payment.stages.values_list("code", flat=True))
    assert {"provider_reported_paid", "awaiting_finance_confirmation", "finance_confirmation_overdue",
            "proof_submitted", "confirmed", "rejected"} <= codes
    overdue = payment.stages.get(code="finance_confirmation_overdue")
    assert overdue.public_label == "Payment Pending Confirmation"
    assert payment.is_published and payment.version == 1


def test_only_final_confirm_reaches_confirmed(payment):
    into = FlowTransition.objects.filter(flow=payment, to_stage__code="confirmed")
    assert set(into.values_list("action_code", flat=True)) == {"final_confirm"}
    assert set(into.values_list("from_stage__code", flat=True)) == {
        "awaiting_finance_confirmation", "finance_confirmation_overdue"}
    for t in into:
        assert t.required_role_setting == "qa.payment.final_approver_role"
        assert t.required_permission == "payments.final_confirm_payment"
    # Evidence never confirms: no move out of these stages goes to confirmed.
    for stage in ("provider_reported_paid", "proof_submitted", "provider_processing", "awaiting_payment"):
        targets = set(FlowTransition.objects.filter(flow=payment, from_stage__code=stage)
                      .values_list("to_stage__code", flat=True))
        assert "confirmed" not in targets, stage


def test_final_confirm_rejects_actor_without_final_approver_role(payment, make_user):
    t = FlowTransition.objects.get(flow=payment, from_stage__code="awaiting_finance_confirmation",
                                   action_code="final_confirm")
    finance_officer = make_user("fo@t.local", role="qa-finance-officer", dept="finance")
    root_wrong_role = make_user("root@t.local", superuser=True, role="director")
    for actor in (finance_officer, root_wrong_role):
        with pytest.raises(flow.TransitionRefused, match="qa-finance-director|permission"):
            flow.check(t, actor)
    # Holding the role is not enough without the permission. Phase 2 grants the
    # permission to the role, so take it away here to prove the check.
    from django.contrib.auth.models import Permission

    from accounts.models import Role

    perm = Permission.objects.filter(codename="final_confirm_payment", content_type__app_label="payments").first()
    if perm is not None:
        role = Role.objects.get(code="qa-finance-director")
        role.permissions.remove(perm)
        for group in role.groups.all():
            group.permissions.remove(perm)
    director_no_perm = make_user("dof@t.local", role="qa-finance-director", dept="finance")
    with pytest.raises(flow.TransitionRefused, match="payments.final_confirm_payment"):
        flow.check(t, director_no_perm)
    # Role + permission: allowed.
    director_ok = make_user("dof2@t.local", role="qa-finance-director", dept="finance", superuser=True)
    flow.check(t, director_ok)


def test_final_approver_role_is_a_setting(payment, make_user):
    t = FlowTransition.objects.get(flow=payment, from_stage__code="awaiting_finance_confirmation",
                                   action_code="final_confirm")
    actor = make_user("x@t.local", role="director", superuser=True)
    with pytest.raises(flow.TransitionRefused):
        flow.check(t, actor)
    ConfigEntry.objects.filter(key="qa.payment.final_approver_role").update(value="director")
    cache.clear()
    flow.check(t, actor)


def test_reject_needs_remark(payment, make_user):
    t = FlowTransition.objects.get(flow=payment, from_stage__code="awaiting_finance_confirmation",
                                   action_code="final_reject")
    actor = make_user("d@t.local", role="qa-finance-director", superuser=True)
    with pytest.raises(ValidationError):
        flow.check(t, actor, remark="")
    flow.check(t, actor, remark="Teller number does not match")


def test_engine_end_to_end_on_a_stand_in_subject(payment, make_user):
    """Walk a manual payment from issue to confirmation."""
    subject = ConfigEntry.objects.get(key="qa.payment.final_approver_role")  # any row works as a subject
    institution_side = make_user("i@t.local", superuser=True)
    director_of_finance = make_user("dof@t.local", role="qa-finance-director", superuser=True)
    inst = flow.start("payment", subject, actor=institution_side)
    assert inst.current_stages == ["issued"]
    for action in ("publish_slip", "submit_proof", "enter_finance_queue"):
        inst = flow.transition(inst, action, institution_side)
    assert inst.current_stages == ["awaiting_finance_confirmation"]
    with pytest.raises(flow.TransitionRefused):
        flow.transition(inst, "final_confirm", institution_side)  # superuser, wrong role
    inst = flow.transition(inst, "final_confirm", director_of_finance, remark="Matches statement")
    assert inst.current_stages == ["confirmed"] and inst.outcome == "confirmed" and inst.completed_at
    events = list(FlowEvent.objects.filter(instance=inst).values_list("action_code", flat=True))
    assert events == ["start", "publish_slip", "submit_proof", "enter_finance_queue", "final_confirm"]
    with pytest.raises(PermissionError):
        e = FlowEvent.objects.filter(instance=inst).first()
        e.remark = "rewrite history"
        e.save()


def test_transitions_needing_a_second_officer_cannot_be_taken_directly(payment, make_user):
    subject = ConfigEntry.objects.get(key="qa.payment.final_approver_role")
    actor = make_user("a@t.local", superuser=True)
    inst = flow.start("payment", subject, actor=actor)
    inst = flow.transition(inst, "publish_slip", actor)
    with pytest.raises(ValidationError, match="second officer"):
        flow.transition(inst, "waive", actor, remark="Hardship case")
