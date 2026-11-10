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
        # Phase B (B2): the guard is the Finance director seat, not a role.
        assert t.required_role_setting == "qa.payment.finance_department"
        assert t.required_permission == "payments.final_confirm_payment"
    # Evidence never confirms: no move out of these stages goes to confirmed.
    for stage in ("provider_reported_paid", "proof_submitted", "provider_processing", "awaiting_payment"):
        targets = set(FlowTransition.objects.filter(flow=payment, from_stage__code=stage)
                      .values_list("to_stage__code", flat=True))
        assert "confirmed" not in targets, stage


def _finance_director(make_user, email="dof@t.local", **kw):
    """The director of the Finance department (seat filled automatically when
    the seat is empty and the person gets the director role)."""
    import org

    user = make_user(email, role="director", dept="finance", **kw)
    assert org.is_director(user, "finance")
    return user


def test_b2_finance_director_confirms_payment(payment, make_user):
    t = FlowTransition.objects.get(flow=payment, from_stage__code="awaiting_finance_confirmation",
                                   action_code="final_confirm")
    director = _finance_director(make_user)
    flow.check(t, director)


def test_b2_other_director_cannot(payment, make_user):
    """Phase B (B2): any other director — even a superuser, even holding the
    permission — is refused; only the Finance seat decides."""
    t = FlowTransition.objects.get(flow=payment, from_stage__code="awaiting_finance_confirmation",
                                   action_code="final_confirm")
    finance_officer = make_user("fo@t.local", role="qa-finance-officer", dept="finance")
    other_director = make_user("dict@t.local", role="director", dept="ict", superuser=True)
    _finance_director(make_user)
    second_finance_director_role = make_user("dof2@t.local", role="director", dept="finance", superuser=True)
    for actor in (finance_officer, other_director, second_finance_director_role):
        with pytest.raises(flow.TransitionRefused, match="director of the department|permission"):
            flow.check(t, actor)


def test_final_confirm_needs_the_permission_too(payment, make_user):
    from django.contrib.auth.models import Permission, Group

    t = FlowTransition.objects.get(flow=payment, from_stage__code="awaiting_finance_confirmation",
                                   action_code="final_confirm")
    perm = Permission.objects.get(codename="final_confirm_payment", content_type__app_label="payments")
    from accounts.models import Role
    Role.objects.get(code="director").permissions.remove(perm)
    for group in Group.objects.filter(permissions=perm):
        group.permissions.remove(perm)
    director_no_perm = _finance_director(make_user)
    with pytest.raises(flow.TransitionRefused, match="payments.final_confirm_payment"):
        flow.check(t, director_no_perm)


def test_finance_department_is_a_setting(payment, make_user):
    t = FlowTransition.objects.get(flow=payment, from_stage__code="awaiting_finance_confirmation",
                                   action_code="final_confirm")
    actor = make_user("x@t.local", role="director", dept="ict", superuser=True)
    with pytest.raises(flow.TransitionRefused):
        flow.check(t, actor)
    ConfigEntry.objects.filter(key="qa.payment.finance_department").update(value="ict")
    cache.clear()
    flow.check(t, actor)


def test_reject_needs_remark(payment, make_user):
    t = FlowTransition.objects.get(flow=payment, from_stage__code="awaiting_finance_confirmation",
                                   action_code="final_reject")
    actor = _finance_director(make_user, "d@t.local", superuser=True)
    with pytest.raises(ValidationError):
        flow.check(t, actor, remark="")
    flow.check(t, actor, remark="Teller number does not match")


def test_engine_end_to_end_on_a_stand_in_subject(payment, make_user):
    """Walk a manual payment from issue to confirmation."""
    subject = ConfigEntry.objects.get(key="qa.payment.finance_department")  # any row works as a subject
    institution_side = make_user("i@t.local", superuser=True)
    director_of_finance = _finance_director(make_user, superuser=True)
    inst = flow.start("payment", subject, actor=institution_side)
    assert inst.current_stages == ["issued"]
    for action in ("publish_slip", "submit_proof", "enter_finance_queue"):
        inst = flow.transition(inst, action, institution_side)
    assert inst.current_stages == ["awaiting_finance_confirmation"]
    with pytest.raises(flow.TransitionRefused):
        flow.transition(inst, "final_confirm", institution_side)  # superuser, not the Finance director
    inst = flow.transition(inst, "final_confirm", director_of_finance, remark="Matches statement")
    assert inst.current_stages == ["confirmed"] and inst.outcome == "confirmed" and inst.completed_at
    events = list(FlowEvent.objects.filter(instance=inst).values_list("action_code", flat=True))
    assert events == ["start", "publish_slip", "submit_proof", "enter_finance_queue", "final_confirm"]
    with pytest.raises(PermissionError):
        e = FlowEvent.objects.filter(instance=inst).first()
        e.remark = "rewrite history"
        e.save()


def test_transitions_needing_a_second_officer_cannot_be_taken_directly(payment, make_user):
    subject = ConfigEntry.objects.get(key="qa.payment.finance_department")
    actor = make_user("a@t.local", superuser=True)
    inst = flow.start("payment", subject, actor=actor)
    inst = flow.transition(inst, "publish_slip", actor)
    with pytest.raises(ValidationError, match="second officer"):
        flow.transition(inst, "waive", actor, remark="Hardship case")
