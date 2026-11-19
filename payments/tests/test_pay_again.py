"""Pay again after a failed / reversed payment.

While a claim waits for the Director of Finance (proof uploaded, provider
report, or a checkout stuck in progress) the institution can say the money
failed or was reversed by the bank. The claim is withdrawn (kept and marked,
never deleted), the bill returns to awaiting_payment with every method, and
Finance is told. If the earlier money lands after all, the extra payment is
routed to refund — nothing is ever confirmed twice."""
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from qa_core.tests.phase2_fixtures import pdf_upload

pytestmark = pytest.mark.django_db

REASON = "The bank reversed the transfer two days later"


@pytest.fixture(autouse=True)
def _sim(settings):
    settings.QA_PAYMENTS_SIMULATE = True


@pytest.fixture
def paystack(monkeypatch):
    """Paystack with a fake network (verify answers from ``state``)."""
    from payments import providers
    from payments.providers.base import VerificationResult

    prov = providers.get("paystack")
    state = {"init_calls": 0, "verify": {}, "verify_error": None}

    def initiate(obligation, payer, amount, *, request=None, attempt=None, callback_url=""):
        state["init_calls"] += 1
        attempt.checkout_url = f"https://checkout.paystack.test/{attempt.provider_reference}"
        return attempt

    def verify(attempt):
        if state["verify_error"]:
            raise state["verify_error"]
        paid = state["verify"].get(attempt.provider_reference, False)
        return VerificationResult(paid=paid, status="success" if paid else "pending",
                                  amount=attempt.amount if paid else None, reference=attempt.provider_reference,
                                  currency="NGN")

    monkeypatch.setattr(prov, "is_simulated", lambda: False)
    monkeypatch.setattr(prov, "is_enabled", lambda: True)
    monkeypatch.setattr(prov, "initiate", initiate)
    monkeypatch.setattr(prov, "verify", verify)
    return state


def _ob(pk):
    from payments.models import PaymentObligation

    return PaymentObligation.unscoped.get(pk=pk)


def _open_item(ob):
    from payments.models import PaymentVerification

    return PaymentVerification.unscoped.get(obligation=ob, is_open=True)


def _pay_and_land(ob, user, provider):
    from payments import services

    attempt = services.start_online_payment(ob, provider, user=user, callback_url="https://x/cb")
    services.simulate_provider_callback(provider, attempt.provider_reference, "success")
    attempt.refresh_from_db()
    assert attempt.status == "provider_reported_paid"
    return attempt


def test_flow_has_the_transition_from_every_claimed_stage(bill):
    from payments.models import PaymentObligation
    from qa_core.models import FlowTransition

    srcs = set(FlowTransition.objects.filter(flow__code="payment", action_code="report_payment_failed")
               .values_list("from_stage__code", flat=True))
    assert srcs == set(PaymentObligation.CLAIMED)
    assert FlowTransition.objects.filter(action_code="report_payment_failed", requires_remark=True,
                                         to_stage__code="awaiting_payment").count() == len(srcs)


def test_migration_adds_the_transition_to_an_existing_flow(bill):
    """The data step adds the rows to a published flow that lacks them."""
    import importlib

    from django.apps import apps as global_apps

    from qa_core.models import FlowTransition

    FlowTransition.objects.filter(action_code="report_payment_failed").delete()
    mod = importlib.import_module("payments.migrations.0011_pay_again_after_failed_claim")
    mod.add_transitions(global_apps, None)
    mod.add_transitions(global_apps, None)   # idempotent
    assert FlowTransition.objects.filter(flow__code="payment", action_code="report_payment_failed").count() == 5


def test_pay_again_from_awaiting_finance_confirmation(bill, inst_user, p2):
    from payments import services
    from payments.models import PaymentVerification, ProofOfPayment

    services.submit_proof(bill, user=inst_user, upload=pdf_upload("teller.pdf"), amount_claimed=bill.amount,
                          bank_name="CBN", teller_number="T-001", paid_on=None)
    ob = _ob(bill.pk)
    assert ob.status == "awaiting_finance_confirmation"
    old_item = _open_item(ob)
    with pytest.raises(ValidationError):
        services.report_payment_failed(ob, user=inst_user, reason="")      # a reason is required

    services.report_payment_failed(ob, user=inst_user, reason=REASON)
    ob = _ob(bill.pk)
    assert ob.status == "awaiting_payment" and ob.chosen_method == ""
    assert "payment_reported_failed" in ob.flags
    old_item.refresh_from_db()
    assert not old_item.is_open and old_item.final_decision == ""
    assert old_item.withdrawn_by == inst_user and old_item.withdrawn_reason == REASON
    proof = ProofOfPayment.unscoped.get(obligation=ob)                     # kept, marked
    assert proof.withdrawn_at is not None and proof.withdrawn_reason == REASON
    # The withdrawn claim can no longer be confirmed.
    with pytest.raises(ValidationError):
        services.final_decision(old_item, user=p2.fin_director, decision="confirmed")
    # Any method again: pay with Remita this time; the Director confirms the new claim once.
    _pay_and_land(ob, inst_user, "remita")
    ob = _ob(bill.pk)
    new_item = _open_item(ob)
    assert new_item.pk != old_item.pk
    services.final_decision(new_item, user=p2.fin_director, decision="confirmed")
    ob = _ob(bill.pk)
    assert ob.status == "confirmed" and ob.amount_paid == ob.amount
    assert PaymentVerification.unscoped.filter(obligation=ob, final_decision="confirmed").count() == 1


def test_pay_again_is_not_offered_when_settled_or_unpaid(bill, inst_user):
    from payments import services

    assert not services.can_pay_again(bill)            # awaiting_payment: nothing to withdraw
    with pytest.raises(ValidationError):
        services.report_payment_failed(bill, user=inst_user, reason=REASON)


def test_reversed_gateway_payment_then_earlier_one_lands_after_confirmation(bill, inst_user, p2):
    """Paystack reported paid; the institution says it was reversed and pays
    by Remita. Then the earlier Paystack money lands after all: it is a
    duplicate routed to refund — never a second confirmation."""
    from payments import services
    from payments.models import PaymentAttempt, PaymentVerification, Refund

    first = _pay_and_land(bill, inst_user, "paystack")
    ob = _ob(bill.pk)
    assert ob.status == "awaiting_finance_confirmation"
    services.report_payment_failed(ob, user=inst_user, reason=REASON)
    first.refresh_from_db()
    assert first.status == "failed" and first.reported_failed_by_institution   # kept, marked
    ob = _ob(bill.pk)
    assert ob.status == "awaiting_payment"

    _pay_and_land(ob, inst_user, "remita")
    services.final_decision(_open_item(_ob(bill.pk)), user=p2.fin_director, decision="confirmed")
    ob = _ob(bill.pk)
    assert ob.status == "confirmed" and ob.amount_paid == ob.amount

    # The provider re-delivers the earlier payment as successful.
    services.simulate_provider_callback("paystack", first.provider_reference, "success")
    first.refresh_from_db()
    ob = _ob(bill.pk)
    assert first.status == "provider_reported_paid"
    assert ob.status == "confirmed" and ob.amount_paid == ob.amount          # not confirmed twice
    assert "duplicate" in ob.flags
    refund = Refund.unscoped.get(obligation=ob, source="duplicate")
    assert refund.amount == first.amount
    assert not PaymentVerification.unscoped.filter(obligation=ob, is_open=True).exists()
    assert "duplicate_refund_opened" in PaymentAttempt.unscoped.get(pk=first.pk).flags


def test_earlier_payment_lands_before_the_new_one_is_confirmed(bill, inst_user, p2, paystack):
    """Stuck checkout → pay again → second checkout paid and queued → the
    stuck one lands too. The Director sees both; confirming settles the bill
    once and the extra payment is opened as a duplicate refund."""
    from payments import services
    from payments.models import PaymentAttempt, PaymentVerification, Refund
    from payments.providers.base import VerificationResult

    stuck = services.start_online_payment(bill, "paystack", user=inst_user)
    assert _ob(bill.pk).status == "provider_processing"
    services.report_payment_failed(_ob(bill.pk), user=inst_user, reason="Checkout froze and never finished")
    second = services.start_online_payment(_ob(bill.pk), "paystack", user=inst_user)
    assert second.pk != stuck.pk
    paystack["verify"][second.provider_reference] = True
    services.record_provider_result(second, services.providers.get("paystack").verify(second), via="return")
    ob = _ob(bill.pk)
    item = _open_item(ob)
    assert item.attempt_id == second.pk

    services.record_provider_result(stuck, VerificationResult(paid=True, status="success", amount=stuck.amount,
                                                              reference=stuck.provider_reference, currency="NGN"),
                                    via="poll")
    ob = _ob(bill.pk)
    assert ob.status == "awaiting_finance_confirmation" and "possible_duplicate" in ob.flags
    assert _open_item(ob).attempt_id == second.pk                            # one item, both attempts on file
    assert PaymentAttempt.unscoped.filter(obligation=ob, status="provider_reported_paid").count() == 2

    services.final_decision(_open_item(ob), user=p2.fin_director, decision="confirmed")
    ob = _ob(bill.pk)
    assert ob.status == "confirmed" and ob.amount_paid == ob.amount
    assert PaymentVerification.unscoped.filter(obligation=ob, final_decision="confirmed").count() == 1
    refund = Refund.unscoped.get(obligation=ob, source="duplicate")
    assert refund.amount == stuck.amount and "duplicate" in ob.flags


def test_pay_again_from_a_stuck_checkout(bill, inst_user, paystack):
    from payments import services
    from payments.models import PaymentAttempt

    stuck = services.start_online_payment(bill, "paystack", user=inst_user)
    assert _ob(bill.pk).status == "provider_processing"
    services.report_payment_failed(_ob(bill.pk), user=inst_user, reason="Checkout froze and never finished")
    stuck.refresh_from_db()
    ob = _ob(bill.pk)
    assert ob.status == "awaiting_payment"
    assert stuck.status == "pending" and stuck.superseded           # still polled, never reused
    again = services.start_online_payment(ob, "paystack", user=inst_user)
    assert again.pk != stuck.pk and paystack["init_calls"] == 2
    assert PaymentAttempt.unscoped.filter(obligation=ob, provider="paystack").count() == 2


def test_stuck_checkout_that_was_paid_blocks_pay_again(bill, inst_user, paystack):
    from payments import services

    stuck = services.start_online_payment(bill, "paystack", user=inst_user)
    paystack["verify"][stuck.provider_reference] = True
    with pytest.raises(services.OnlinePaymentError) as err:
        services.report_payment_failed(_ob(bill.pk), user=inst_user, reason="Checkout froze and never finished")
    assert err.value.kind == "already_paid"
    stuck.refresh_from_db()
    assert stuck.status == "provider_reported_paid"                  # what was learnt is committed
    assert _ob(bill.pk).status == "awaiting_finance_confirmation"


def test_unreachable_provider_does_not_block_pay_again(bill, inst_user, paystack):
    from payments import providers, services

    stuck = services.start_online_payment(bill, "paystack", user=inst_user)
    paystack["verify_error"] = providers.ProviderUnreachable("timeout", timeout=True)
    services.report_payment_failed(_ob(bill.pk), user=inst_user, reason="Checkout froze and never finished")
    stuck.refresh_from_db()
    assert _ob(bill.pk).status == "awaiting_payment"
    assert stuck.status == "pending" and "verify_unreachable" in stuck.flags and stuck.superseded
    # ... nor the next checkout: only a provider that says "paid" blocks.
    again = services.start_online_payment(_ob(bill.pk), "paystack", user=inst_user)
    assert again.pk != stuck.pk


def test_finance_is_told_and_sees_both_attempts(bill, inst_user, p2, client_for):
    from payments import services
    from qa_core.models import TimelineEvent

    first = _pay_and_land(bill, inst_user, "paystack")
    services.report_payment_failed(_ob(bill.pk), user=inst_user, reason=REASON)
    assert TimelineEvent.unscoped.filter(event_code="payment.alert", internal_text__contains="withdrew").exists()
    _pay_and_land(_ob(bill.pk), inst_user, "remita")
    item = _open_item(_ob(bill.pk))
    html = client_for(p2.fin_director).get(f"/qa/finance/queue/{item.pk}/").content.decode()
    assert first.provider_reference in html and "reported this payment failed" in html


def test_portal_pay_again_button(bill, inst_user, client_for, portal_host):
    from payments import services

    services.submit_proof(bill, user=inst_user, upload=pdf_upload("teller.pdf"), amount_claimed=bill.amount,
                          bank_name="CBN", teller_number="T-001", paid_on=None)
    client = client_for(inst_user, portal_host)
    html = client.get(f"/payments/{bill.pk}/", HTTP_HOST=portal_host).content.decode()
    assert "My payment failed or was reversed" in html and 'name="pay_again"' in html
    r = client.post(f"/payments/{bill.pk}/method/", {"pay_again": "1", "reason": "no"}, HTTP_HOST=portal_host)
    assert r.status_code == 422 and _ob(bill.pk).status == "awaiting_finance_confirmation"
    r = client.post(f"/payments/{bill.pk}/method/", {"pay_again": "1", "reason": REASON}, HTTP_HOST=portal_host)
    assert r.status_code == 200 and _ob(bill.pk).status == "awaiting_payment"
    html = r.content.decode()
    assert "How will you pay?" in html and "You can pay again" in html
