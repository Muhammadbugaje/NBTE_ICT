"""v2.1 Phase F payments: F.4 (P1–P10, T-SEC-8) and F.18 (X21).

Fee components (base + processing), the method choice that hides the other
methods, the Director of Finance told on every claim, the ICT reminder
(rate-limited), provider verification, the
``obligation_confirmed`` signal and the production fail-fast check."""
import json
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings as dj_settings
from django.core.exceptions import PermissionDenied, ValidationError

from qa_core.tests.phase2_fixtures import pdf_upload

pytestmark = pytest.mark.django_db


def _ob(pk):
    from payments.models import PaymentObligation

    return PaymentObligation.unscoped.get(pk=pk)


def _item(ob):
    from payments.models import PaymentVerification

    return PaymentVerification.unscoped.get(obligation=ob, is_open=True)


@pytest.fixture(autouse=True)
def _enable_gateway_simulator_for_tests(settings):
    settings.QA_PAYMENTS_SIMULATE = True


def _set(key, value):
    from config.models import ConfigEntry

    entry = ConfigEntry.objects.get(key=key, scope="global")
    entry.value = str(value)
    entry.save()  # invalidates the config cache


@pytest.fixture
def components(p2):
    """The finance.fee_components data set (seed_nbte), loaded directly."""
    from institutions.models import InstitutionType
    from payments.models import FeeComponent, FeeSchedule

    data = json.loads((Path(dj_settings.BASE_DIR) / "core/seed_data/finance/fee_components.json").read_text())
    for row in data["rows"]:
        schedule = FeeSchedule.objects.get(code=row["schedule"])
        itype = InstitutionType.objects.filter(code=row.get("institution_type", "")).first() \
            if row.get("institution_type") else None
        FeeComponent.objects.create(schedule=schedule, code=row["code"], label=row["label"], kind=row["kind"],
                                    description=row.get("description", ""),
                                    amount_setting=row.get("amount_setting", ""), institution_type=itype,
                                    order=row.get("order", 10), to_confirm=row.get("to_confirm", False))


@pytest.fixture
def poly_bill(components, p2, proposed_inst):
    """A private polytechnic's SOP-01 bill: 4,000,000 + 1,000,000 processing (Settings defaults)."""
    from payments import services

    return services.issue_obligation(proposed_inst, "institutional_processing", actor=p2.fin_officer)


def _pay(ob, user, provider):
    from payments import services

    attempt = services.start_online_payment(ob, provider, user=user, callback_url="https://x/cb")
    services.simulate_provider_callback(provider, attempt.provider_reference, "success")
    attempt.refresh_from_db()
    return attempt


def _teller(ob, user, name="teller.pdf"):
    from payments import services

    services.submit_proof(ob, user=user, upload=pdf_upload(name, name.encode()), amount_claimed=ob.balance,
                          bank_name="CBN", teller_number="T-9", paid_on=None)
    return _ob(ob.pk)


# ---------------------------------------------------------------------------
# P1: fee components
# ---------------------------------------------------------------------------
def test_p_fee_components_total(poly_bill, p2, proposed_inst, lookups):
    """The SOP-01 institution fee and processing fee come from Settings per
    institution category AND ownership (qa.fee.institution.<type>.<public|private>,
    qa.fee.institution_processing.<…>), seeded with the former formula's amounts
    (private ₦4,000,000 / public ₦2,000,000) and the existing processing fee."""
    from payments import billing
    from payments.models import FeeSchedule

    def compute(ownership, itype="polytechnic"):
        _snap, lines = billing.compute(schedule, proposed_inst, {
            "ownership": ownership, "institution_type": itype, "programmes": 0, "new_programmes": 0,
            "streams": 0, "max_streams": 0, "falsified": 0})
        return [(line["label"], line["amount"]) for line in lines]

    schedule = FeeSchedule.objects.get(code="institutional_processing")
    # Defaults: a private polytechnic (the fixture) pays 4,000,000 + 1,000,000.
    lines = poly_bill.breakdown
    assert [(line["label"], line["amount"]) for line in lines] == [
        ("Institution fee", Decimal("4000000.00")), ("Processing fee", Decimal("1000000.00"))]
    assert poly_bill.amount == Decimal("5000000.00") == sum(line["amount"] for line in lines)
    assert poly_bill.balance == poly_bill.amount
    assert poly_bill.formula_snapshot["key"] == "qa.fee.institution.polytechnic.private"
    assert poly_bill.formula_snapshot["base_result"] in ("4000000", "4000000.00")
    assert compute("federal") == compute("state") == [
        ("Institution fee", Decimal("2000000.00")), ("Processing fee", Decimal("1000000.00"))]
    # Other categories start from the same ownership amounts (no processing fee set for them).
    assert compute("private", "college-nursing") == [("Institution fee", Decimal("4000000.00"))]
    assert compute("state", "college-nursing") == [("Institution fee", Decimal("2000000.00"))]

    # An override for one category + ownership changes only that bill.
    _set("qa.fee.institution.polytechnic.private", "3500000")
    _set("qa.fee.institution_processing.polytechnic.private", "750000")
    assert compute("private") == [("Institution fee", Decimal("3500000.00")),
                                  ("Processing fee", Decimal("750000.00"))]
    assert compute("federal") == [("Institution fee", Decimal("2000000.00")),
                                  ("Processing fee", Decimal("1000000.00"))]
    assert compute("private", "college-nursing") == [("Institution fee", Decimal("4000000.00"))]
    # The live estimate and a new bill read the same settings.
    from payments import services

    assert services.fee_estimate(proposed_inst, "institutional_processing", [])["total"] == Decimal("4250000.00")
    assert services.issue_obligation(proposed_inst, "institutional_processing",
                                     actor=p2.fin_officer).amount == Decimal("4250000.00")
    # 0 = no fee line; switching processing off drops the processing line.
    _set("qa.fee.institution_processing.polytechnic.private", "0")
    assert compute("private") == [("Institution fee", Decimal("3500000.00"))]
    _set("qa.fee.institution_processing.polytechnic.private", "750000")
    _set("qa.fee.processing.enabled", "false")
    assert compute("private") == [("Institution fee", Decimal("3500000.00"))]


def test_p_fee_components_on_slip_and_advice(poly_bill):
    from payments.models import PaymentSlip
    from payments.pdfs import render_slip

    from .test_payments import _pdf_text

    text = _pdf_text(render_slip(PaymentSlip.unscoped.get(obligation=poly_bill, is_current=True)))
    assert "Processing fee" in text and "Institution fee" in text


# ---------------------------------------------------------------------------
# P9: choosing a method hides the others
# ---------------------------------------------------------------------------
def test_p_method_hides_others(poly_bill, inst_user, client_for, portal_host):
    from payments import services
    from payments.models import PaymentAttempt

    client = client_for(inst_user, portal_host)
    html = client.get(f"/payments/{poly_bill.pk}/", HTTP_HOST=portal_host).content.decode()
    assert "How will you pay?" in html and "Paystack" in html and "Bank transfer" in html

    services.choose_method(poly_bill, "remita", user=inst_user)
    html = client.get(f"/payments/{poly_bill.pk}/", HTTP_HOST=portal_host).content.decode()
    assert "Continue to Remita" in html
    assert "Continue to Paystack" not in html and "Upload proof of payment" not in html
    assert "hidden because you chose Remita" in html

    # A Paystack attempt started earlier is abandoned and never shown as pending.
    services.change_method(_ob(poly_bill.pk), user=inst_user)
    attempt = services.start_online_payment(_ob(poly_bill.pk), "paystack", user=inst_user, callback_url="https://x")
    assert _ob(poly_bill.pk).chosen_method == "paystack"
    services.choose_method(_ob(poly_bill.pk), "remita", user=inst_user)
    attempt.refresh_from_db()
    assert attempt.status == "abandoned"
    ob = _ob(poly_bill.pk)
    visible = services.visible_attempts(ob, list(PaymentAttempt.unscoped.filter(obligation=ob)))
    assert all(a.provider == "remita" and a.status != "abandoned" for a in visible)
    html = client.get(f"/payments/{poly_bill.pk}/", HTTP_HOST=portal_host).content.decode()
    assert attempt.provider_reference not in html

    # Change method brings every method back.
    services.change_method(ob, user=inst_user)
    assert _ob(poly_bill.pk).chosen_method == ""
    html = client.get(f"/payments/{poly_bill.pk}/", HTTP_HOST=portal_host).content.decode()
    assert "How will you pay?" in html and "Paystack" in html


def test_p_choose_method_view_and_api(poly_bill, inst_user, client_for, portal_host):
    client = client_for(inst_user, portal_host)
    r = client.post(f"/payments/{poly_bill.pk}/method/", {"method": "bank_transfer"}, HTTP_HOST=portal_host)
    assert r.status_code == 200
    assert _ob(poly_bill.pk).chosen_method == "bank_transfer"
    assert "Upload proof of payment" in r.content.decode()
    r = client.post(f"/payments/{poly_bill.pk}/method/", {"method": ""}, HTTP_HOST=portal_host)
    assert r.status_code == 200 and _ob(poly_bill.pk).chosen_method == ""


# ---------------------------------------------------------------------------
# Payments go directly to the Director of Finance
# ---------------------------------------------------------------------------
def test_preliminary_approval_is_disabled(poly_bill, p2, inst_user):
    from payments import services

    ob = _teller(poly_bill, inst_user)
    item = _item(ob)
    with pytest.raises(PermissionDenied, match="directly to the Director"):
        services.first_pass(item, user=p2.fin_officer, decision="approved", amount=ob.balance)
    assert services.approvals_required(item) == 0
    assert services.ready_for_final(item) == (True, "")
    assert not services.item_rights(item, p2.fin_officer)["can_first"]


def test_bank_transfer_direct_decision(bill, p2, inst_user):
    from payments import services

    ob = _teller(bill, inst_user)
    item = _item(ob)
    assert services.ready_for_final(item) == (True, "")
    services.final_decision(item, user=p2.fin_director, decision="confirmed")
    assert _ob(bill.pk).status == "confirmed"


def test_p_approvers_above_limit(poly_bill, p2, inst_user):
    """Payment limits no longer add preliminary approvers."""
    from payments import services

    ob = _teller(poly_bill, inst_user)
    ready, why = services.ready_for_final(_item(ob))
    assert ready and not why
    assert services.approvals_required(_item(ob)) == 0
    services.final_decision(_item(ob), user=p2.fin_director, decision="confirmed")
    assert _ob(ob.pk).status == "confirmed"


# ---------------------------------------------------------------------------
# P3/P4: the Director of Finance is told; ICT reminders are rate-limited
# ---------------------------------------------------------------------------
def test_p_dof_notified(bill, p2, inst_user, django_capture_on_commit_callbacks):
    from notifications.models import Notification

    with django_capture_on_commit_callbacks(execute=True):
        _teller(bill, inst_user)
    assert Notification.objects.filter(recipient=p2.fin_director, title__icontains="Payment claimed").exists()
    assert not Notification.objects.filter(recipient=p2.fin_officer, title__icontains="Payment to verify").exists()


def test_p4_reminder_rate_limit(bill, p2, inst_user, make_user, django_capture_on_commit_callbacks):
    from accounts.models import User
    from notifications.models import Notification
    from payments import services

    ob = _teller(bill, inst_user)
    ict = User.objects.get(pk=make_user("p2.ict@t.local", role="ict-qa-monitor").pk)
    with pytest.raises(PermissionDenied):
        services.remind_director(_item(ob), user=p2.fin_officer)
    assert services.item_rights(_item(ob), ict)["can_remind"]
    with django_capture_on_commit_callbacks(execute=True):
        services.remind_director(_item(ob), user=ict, note="Institution called twice")
    assert Notification.objects.filter(recipient=p2.fin_director, title__icontains="Reminder from ICT").exists()
    with pytest.raises(ValidationError):
        services.remind_director(_item(ob), user=ict)  # once per 24 h
    assert not services.item_rights(_item(ob), ict)["can_remind"]
    from core.models import AuditLogEntry as AuditLog

    assert AuditLog.objects.filter(note__icontains="ICT reminder").count() == 1


def test_p4_reminder_view(bill, p2, inst_user, make_user, client_for):
    from accounts.models import User

    ob = _teller(bill, inst_user)
    ict = User.objects.get(pk=make_user("p2.ict2@t.local", role="ict-qa-monitor").pk)
    # Assignment privacy mode (X1/X2): the ICT monitor sees the bill only as the
    # institution's ICT supervisor (an open oversight_ict assignment); otherwise 404.
    from assignments.models import WorkAssignment

    WorkAssignment.objects.create(institution=ob.institution, kind=WorkAssignment.OVERSIGHT_ICT, assignee=ict,
                                  status="in_progress", subject_id=f"ict-{ob.institution_id}")
    client = client_for(ict)
    r = client.post(f"/qa/finance/queue/{_item(ob).pk}/remind/")
    assert r.status_code == 200
    r = client.post(f"/qa/finance/queue/{_item(ob).pk}/remind/")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# X21: provider-verified payments supply evidence for the Director
# ---------------------------------------------------------------------------
def test_gateway_verified_evidence_goes_to_director(poly_bill, p2, inst_user, django_capture_on_commit_callbacks):
    from notifications.models import Notification
    from payments import services

    with django_capture_on_commit_callbacks(execute=True):
        attempt = _pay(poly_bill, inst_user, "paystack")
    assert attempt.status == "provider_reported_paid"
    ob = _ob(poly_bill.pk)
    item = _item(ob)
    assert item.verified_via == "gateway" and item.auto_first_pass and item.amount_matches
    assert item.decision == "approved" and item.verified_by_id is None
    assert item.amount_confirmed == ob.balance == item.provider_amount
    # No receipt upload, no first-pass officer: straight to the Director.
    assert services.ready_for_final(item) == (True, "")
    assert not services.item_rights(item, p2.fin_officer)["can_first"]
    assert item not in services.queue_items(p2.fin_officer, stage="first")
    assert Notification.objects.filter(recipient=p2.fin_director, title__icontains="Verified by").exists()
    assert not Notification.objects.filter(recipient=p2.fin_officer, title__icontains="Payment to verify").exists()
    from payments.portal_views import pay_options
    from payments.models import PaymentAttempt, ProofOfPayment

    assert not pay_options(ob, list(PaymentAttempt.unscoped.filter(obligation=ob)),
                           list(ProofOfPayment.unscoped.filter(obligation=ob)))["receipt_needed"]
    # Still evidence: not confirmed until the Director decides.
    assert ob.status != "confirmed" and ob.amount_paid == 0
    services.final_decision(item, user=p2.fin_director, decision="confirmed")
    assert _ob(ob.pk).status == "confirmed"


def test_remita_gateway_evidence_goes_to_director(poly_bill, inst_user):
    _pay(poly_bill, inst_user, "remita")
    item = _item(_ob(poly_bill.pk))
    assert item.verified_via == "gateway" and item.auto_first_pass


def test_x21_amount_mismatch_stays_manual(poly_bill, p2, inst_user, make_user, django_capture_on_commit_callbacks):
    from accounts.models import User
    from notifications.models import Notification
    from payments import services
    from payments.providers import simulator

    ict = User.objects.get(pk=make_user("p2.ict3@t.local", role="ict-qa-monitor").pk)
    attempt = services.start_online_payment(poly_bill, "paystack", user=inst_user, callback_url="https://x/cb")
    data = simulator._get("paystack", attempt.provider_reference)
    data["amount"] = int(data["amount"]) - 50_000_00  # ₦50,000 short
    simulator._put("paystack", attempt.provider_reference, data)
    with django_capture_on_commit_callbacks(execute=True):
        services.simulate_provider_callback("paystack", attempt.provider_reference, "success")
    ob = _ob(poly_bill.pk)
    item = _item(ob)
    assert item.amount_matches is False and not item.auto_first_pass and item.decision == ""
    assert item.verified_via == "officer"
    assert services.ready_for_final(item)[0]
    assert Notification.objects.filter(recipient=ict, title__icontains="Payment alert").exists()
    assert not Notification.objects.filter(recipient=p2.fin_officer, title__icontains="Payment to verify").exists()
    services.final_decision(item, user=p2.fin_director, decision="confirmed", amount=item.provider_amount)
    assert _ob(ob.pk).status == "partially_paid"


def test_x21_threshold_does_not_add_approvers(poly_bill, p2, inst_user):
    from payments import services

    _pay(poly_bill, inst_user, "paystack")
    item = _item(_ob(poly_bill.pk))
    assert item.auto_first_pass
    ready, why = services.ready_for_final(item)
    assert ready and not why
    assert services.approvals_required(item) == 0


def test_gateway_evidence_does_not_need_preliminary_approval(poly_bill, p2, inst_user):
    _pay(poly_bill, inst_user, "paystack")
    item = _item(_ob(poly_bill.pk))
    assert item.amount_matches and item.auto_first_pass and item.decision == "approved"


def test_provider_never_confirms_alone(poly_bill, p2, inst_user):
    """However many callbacks arrive, only the Director's decision confirms."""
    from payments import services

    attempt = _pay(poly_bill, inst_user, "paystack")
    for _ in range(3):
        services.simulate_provider_callback("paystack", attempt.provider_reference, "success")
        services.verify_on_return(_ob(poly_bill.pk), {"reference": attempt.provider_reference})
    services.sweep()
    ob = _ob(poly_bill.pk)
    assert ob.status != "confirmed" and ob.amount_paid == 0 and ob.confirmed_at is None
    from payments.models import PaymentVerification

    assert PaymentVerification.unscoped.filter(obligation=ob).count() == 1


def test_bank_transfer_goes_directly_to_director(poly_bill, p2, inst_user):
    from payments import services

    ob = _teller(poly_bill, inst_user)
    item = _item(ob)
    assert item.verified_via == "officer" and not item.auto_first_pass
    assert services.ready_for_final(item) == (True, "")
    assert services.item_rights(item, p2.fin_director)["can_final"]


# ---------------------------------------------------------------------------
# P10 / D-5: the signal, the welcome on the dashboard
# ---------------------------------------------------------------------------
def test_p10_signal_redirect_and_welcome(poly_bill, p2, inst_user, client_for, portal_host,
                                         django_capture_on_commit_callbacks):
    from payments import services
    from payments.signals import obligation_confirmed

    received = []

    def listener(sender, obligation, verification, actor, **kw):
        received.append((obligation.pk, verification.pk, actor.pk))

    obligation_confirmed.connect(listener)
    try:
        _pay(poly_bill, inst_user, "paystack")
        item = _item(_ob(poly_bill.pk))
        with django_capture_on_commit_callbacks(execute=True):
            services.final_decision(item, user=p2.fin_director, decision="confirmed")
    finally:
        obligation_confirmed.disconnect(listener)
    assert received == [(poly_bill.pk, item.pk, p2.fin_director.pk)]
    client = client_for(inst_user, portal_host)
    r = client.get(f"/payments/{poly_bill.pk}/", HTTP_HOST=portal_host)
    assert r.status_code == 302 and f"welcome={poly_bill.pk}" in r["Location"]
    r = client.get(r["Location"], HTTP_HOST=portal_host)
    html = r.content.decode()
    assert "Payment confirmed" in html and "Next" in html
    # Second visit: the bill shows the receipt page, no redirect.
    r = client.get(f"/payments/{poly_bill.pk}/", HTTP_HOST=portal_host)
    assert r.status_code == 200 and "Official receipt" in r.content.decode()


# ---------------------------------------------------------------------------
# T-SEC-8: production fails fast when a gateway is on without keys
# ---------------------------------------------------------------------------
def test_tsec8_prod_check_gateway_keys(settings, qa_seed):
    from payments.checks import gateway_keys_check

    settings.DEBUG = False
    settings.QA_PAYMENTS_SIMULATE = False
    settings.PAYSTACK_SECRET_KEY = settings.PAYSTACK_PUBLIC_KEY = ""
    _set("qa.payment.paystack.enabled", "true")
    errors = gateway_keys_check(None, force=True)
    assert any("PAYSTACK_SECRET_KEY" in e.msg for e in errors)
    settings.PAYSTACK_SECRET_KEY, settings.PAYSTACK_PUBLIC_KEY = "sk_test_x", "pk_test_x"
    settings.REMITA_MERCHANT_ID = settings.REMITA_API_KEY = "x"
    assert gateway_keys_check(None, force=True) == []


def test_tsec8_env_example_names_every_payment_variable():
    text = (Path(dj_settings.BASE_DIR) / ".env.example").read_text()
    for name in ("REMITA_MERCHANT_ID", "REMITA_SERVICE_TYPE_ID", "REMITA_API_KEY", "REMITA_BASE_URL",
                 "REMITA_WEBHOOK_TOKEN", "PAYSTACK_PUBLIC_KEY", "PAYSTACK_SECRET_KEY", "PAYSTACK_BASE_URL"):
        assert f"\n{name}=" in text, name
