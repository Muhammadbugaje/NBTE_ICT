"""v2.2 payment hardening: idempotent online attempts, plain-language
offline errors, never charging twice, the double-entry ledger and the
nightly reconciliation."""
from decimal import Decimal

import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sim(settings):
    settings.QA_PAYMENTS_SIMULATE = True


@pytest.fixture
def paystack(monkeypatch):
    """Paystack with real keys (not the simulator) and a fake network."""
    from payments import providers
    from payments.providers.base import VerificationResult

    prov = providers.get("paystack")
    state = {"init_calls": 0, "verify": {}, "init_error": None, "verify_error": None}

    def initiate(obligation, payer, amount, *, request=None, attempt=None, callback_url=""):
        state["init_calls"] += 1
        if state["init_error"]:
            raise state["init_error"]
        attempt.checkout_url = f"https://checkout.paystack.test/{attempt.provider_reference}"
        return attempt

    def verify(attempt):
        if state["verify_error"]:
            raise state["verify_error"]
        paid = state["verify"].get(attempt.provider_reference, False)
        return VerificationResult(paid=paid, status="success" if paid else "abandoned",
                                  amount=attempt.amount if paid else None, reference=attempt.provider_reference,
                                  currency="NGN")

    monkeypatch.setattr(prov, "is_simulated", lambda: False)
    monkeypatch.setattr(prov, "is_enabled", lambda: True)
    monkeypatch.setattr(prov, "initiate", initiate)
    monkeypatch.setattr(prov, "verify", verify)
    return state


def test_offline_start_is_kept_and_explained(bill, inst_user, paystack):
    from payments import providers, services
    from payments.models import PaymentAttempt

    paystack["init_error"] = providers.ProviderUnreachable("no route", timeout=True)
    with pytest.raises(services.OnlinePaymentError) as err:
        services.start_online_payment(bill, "paystack", user=inst_user)
    assert err.value.kind == "unreachable"
    assert "internet connection" in " ".join(err.value.messages)
    failed = PaymentAttempt.unscoped.get(obligation=bill, provider="paystack")
    assert failed.status == "failed" and "init_unreachable" in failed.flags   # committed, not rolled back
    paystack["init_error"] = None
    attempt = services.start_online_payment(bill, "paystack", user=inst_user)
    assert attempt.provider_reference != failed.provider_reference          # new idempotency key


def test_double_click_reuses_the_same_checkout(bill, inst_user, paystack):
    from payments import services

    a1 = services.start_online_payment(bill, "paystack", user=inst_user)
    a2 = services.start_online_payment(bill, "paystack", user=inst_user)
    assert a1.pk == a2.pk and paystack["init_calls"] == 1


def test_earlier_paid_attempt_stops_a_second_charge(bill, inst_user, paystack):
    from payments import services
    from payments.models import PaymentAttempt

    a1 = services.start_online_payment(bill, "paystack", user=inst_user)
    PaymentAttempt.unscoped.filter(pk=a1.pk).update(checkout_url="")   # not reusable: forces the check
    paystack["verify"][a1.provider_reference] = True
    with pytest.raises(services.OnlinePaymentError) as err:
        services.start_online_payment(bill, "paystack", user=inst_user)
    assert err.value.kind == "already_paid"
    assert PaymentAttempt.unscoped.get(pk=a1.pk).status == "provider_reported_paid"
    assert paystack["init_calls"] == 1


def test_provider_unreachable_while_checking_earlier_attempt_asks_to_wait(bill, inst_user, paystack):
    from payments import providers, services
    from payments.models import PaymentAttempt

    a1 = services.start_online_payment(bill, "paystack", user=inst_user)
    PaymentAttempt.unscoped.filter(pk=a1.pk).update(checkout_url="")
    paystack["verify_error"] = providers.ProviderUnreachable("timeout", timeout=True)
    with pytest.raises(services.OnlinePaymentError) as err:
        services.start_online_payment(bill, "paystack", user=inst_user)
    assert err.value.kind == "unreachable" and "paying twice" in " ".join(err.value.messages)
    a1 = PaymentAttempt.unscoped.get(pk=a1.pk)
    assert a1.status == "pending" and "verify_unreachable" in a1.flags   # pending, never failed


def test_http_json_maps_network_failures(monkeypatch):
    import urllib.error

    from payments.providers import base

    def boom(*a, **k):
        raise urllib.error.URLError(OSError("Network is unreachable"))

    monkeypatch.setattr(base.urllib.request, "urlopen", boom)
    with pytest.raises(base.ProviderUnreachable):
        base.http_json("GET", "https://api.paystack.co/x")
    with pytest.raises(base.ProviderConfigError):
        base.http_json("GET", "not a url")


def test_ledger_posts_once_and_balances(bill):
    from payments import ledger
    from payments.models import LedgerJournal

    ledger.sync_obligation(bill)
    ledger.sync_obligation(bill)   # idempotent
    view = ledger.ledger_view(bill)
    assert view["billed"] == bill.amount and view["receivable"] == bill.amount
    assert LedgerJournal.objects.filter(obligation=bill, source="bill").count() == 1
    assert not ledger.unbalanced_journals()


def test_reconciliation_records_a_payment_the_provider_has(bill, inst_user, paystack):
    from payments import reconcile, services
    from payments.models import PaymentAttempt

    a1 = services.start_online_payment(bill, "paystack", user=inst_user)
    paystack["verify"][a1.provider_reference] = True
    run = reconcile.run(trigger="manual")
    assert PaymentAttempt.unscoped.get(pk=a1.pk).status == "provider_reported_paid"
    assert run.issues.filter(kind="provider_paid_not_recorded", auto_fixed=True).exists()


def test_director_of_finance_can_open_reconciliation(p2, client):
    client.force_login(p2.fin_director)
    resp = client.get("/qa/finance/reconciliation/")
    assert resp.status_code == 200 and b"Trial balance" in resp.content
    resp = client.post("/qa/finance/reconciliation/run/", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="qa-panel")
    assert resp.status_code == 200
