"""v2.5: cancellation & refund policy — the quote per stage, settings
overrides, voiding unpaid bills, and the refund lifecycle (institution →
Finance verifies → ES approves → refunded)."""
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from qa_core.tests.phase3_fixtures import *  # noqa: F401,F403
from qa_core.tests.phase3_fixtures import bill_of, make_request, set_config
from qa_core.tests.phase4_fixtures import *  # noqa: F401,F403
from qa_core.tests.phase4_fixtures import advance_to

pytestmark = pytest.mark.django_db

BANK = {"bank_name": "First Bank", "account_name": "Alpha Polytechnic Revenue", "account_number": "3012345678"}


@pytest.fixture
def es(p2, make_user):
    return make_user("es@t.local", role="executive-secretary")


def _submitted(p3, req=None):
    from accreditation import qa_services as svc

    req = req or make_request(p3)
    svc.submit(req, user=p3.ceo, declaration=True)
    req.refresh_from_db()
    assert req.status == "fee_due"
    return req


def _pay(ob):
    """Mark a bill fully paid and confirmed (what the Director's final decision leaves)."""
    from payments.models import PaymentObligation

    PaymentObligation.unscoped.filter(pk=ob.pk).update(status="confirmed", amount_paid=ob.amount, balance=0)
    ob.refresh_from_db()
    return ob


# ---------------------------------------------------------------------------
# The quote
# ---------------------------------------------------------------------------
def test_quote_before_assignment_is_90_percent_of_what_was_paid(p3):
    from payments import refund_policy as policy

    req = make_request(p3)
    q = policy.refund_quote(req)
    assert (q["stage"], q["band"], q["percent"], q["paid"]) == ("qa_request:draft", policy.BEFORE, 90, Decimal("0"))
    req = _submitted(p3, req)
    ob = _pay(bill_of(req))
    q = policy.refund_quote(req)
    assert q["band"] == policy.BEFORE and q["paid"] == ob.amount
    assert q["refund_amount"] == (ob.amount * Decimal("0.9")).quantize(Decimal("0.01"))
    assert q["admin_fee"] + q["refund_amount"] == ob.amount
    assert q["rule_label"]


def test_quote_nbte_withdrawal_is_100_percent(p3):
    from payments import refund_policy as policy

    req = _submitted(p3)
    ob = _pay(bill_of(req))
    q = policy.refund_quote(req, by_nbte=True)
    assert (q["band"], q["percent"], q["refund_amount"], q["admin_fee"]) == (policy.NBTE, 100, ob.amount, 0)


def test_quote_follows_the_qa_case_stage(p4):
    from payments import refund_policy as policy

    req = p4.case.qa_request
    req.refresh_from_db()
    q = policy.refund_quote(req)
    assert q["stage"] == "qa_case:case_opened" and (q["band"], q["percent"]) == (policy.UNDER, 50)
    advance_to(p4, 8)  # the ES approved the cost: visit details
    q = policy.refund_quote(req)
    assert q["stage"].startswith("qa_case:") and (q["band"], q["percent"]) == (policy.AFTER, 0)
    assert q["refund_amount"] == 0


def test_settings_override_percent_min_fee_and_stage_band(p3):
    from payments import refund_policy as policy

    req = _submitted(p3)
    ob = _pay(bill_of(req))
    set_config("qa.refund.percent.before_assignment", "75")
    q = policy.refund_quote(req)
    assert q["percent"] == 75 and q["refund_amount"] == (ob.amount * Decimal("0.75")).quantize(Decimal("0.01"))
    set_config("qa.refund.min_admin_fee", str(ob.amount))  # the floor eats everything
    assert policy.refund_quote(req)["refund_amount"] == 0
    set_config("qa.refund.min_admin_fee", "0")
    set_config("qa.refund.stage_bands", '{"qa_request:fee_due": "under_assessment"}')
    q = policy.refund_quote(req)
    assert (q["band"], q["percent"]) == (policy.UNDER, 50)


def test_decided_request_cannot_be_cancelled(p3):
    from accreditation.models import QARequest
    from payments import cancellations, refund_policy as policy

    req = make_request(p3)
    QARequest.unscoped.filter(pk=req.pk).update(status="decided")
    req.refresh_from_db()
    assert policy.refund_quote(req)["cancellable"] is False
    with pytest.raises(ValidationError):
        cancellations.cancel(req, actor=p3.ceo, reason="No longer needed", declaration=True)


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------
def test_cancel_voids_the_unpaid_bill_and_opens_no_refund(p3, django_capture_on_commit_callbacks):
    from payments import cancellations
    from payments.models import RefundRequest

    req = _submitted(p3)
    ob = bill_of(req)
    with django_capture_on_commit_callbacks(execute=True):
        c = cancellations.cancel(req, actor=p3.ceo, reason="Programmes not ready", declaration=True)
    req.refresh_from_db()
    ob.refresh_from_db()
    assert req.status == "cancelled" and ob.status == "cancelled"
    assert c.voided_obligations == [ob.reference] and not c.by_nbte
    assert not RefundRequest.unscoped.filter(cancellation=c).exists()
    with pytest.raises(ValidationError):
        cancellations.cancel(req, actor=p3.ceo, reason="Again", declaration=True)


def test_cancel_needs_reason_and_declaration(p3):
    from payments import cancellations

    req = _submitted(p3)
    with pytest.raises(ValidationError):
        cancellations.cancel(req, actor=p3.ceo, reason="", declaration=True)
    with pytest.raises(ValidationError):
        cancellations.cancel(req, actor=p3.ceo, reason="Not ready yet", declaration=False)


def test_other_institution_and_department_users_cannot_cancel(p3):
    from payments import cancellations

    req = _submitted(p3)
    with pytest.raises(PermissionDenied):
        cancellations.cancel(req, actor=p3.other_admin, reason="Not ours", declaration=True)
    with pytest.raises(PermissionDenied):
        cancellations.cancel(req, actor=p3.dept, reason="Department", declaration=True)


def test_only_the_chief_executive_cancels_not_the_institution_admin(p3, client_for, portal_host):
    """Owner decision: the institution administrator sees why, not the button."""
    from payments import cancellations

    req = _submitted(p3)
    assert cancellations.can_cancel_as_institution(p3.ceo, req)
    assert not cancellations.can_cancel_as_institution(p3.admin, req)
    with pytest.raises(PermissionDenied, match="Only the Chief Executive"):
        cancellations.cancel(req, actor=p3.admin, reason="Admin tries", declaration=True)
    url = f"/qa-requests/{req.pk}/cancel-with-refund/"
    assert client_for(p3.admin, portal_host).get(url).status_code == 404
    for user, button, hint in ((p3.admin, False, True), (p3.ceo, True, False)):
        html = client_for(user, portal_host).get(f"/qa-requests/{req.pk}/").content.decode()
        assert ("cancel-with-refund" in html) is button
        assert ("Only the Chief Executive can cancel" in html) is hint


def test_other_institution_gets_404_on_the_cancel_page(p3, client_for, portal_host):
    req = _submitted(p3)
    url = f"/qa-requests/{req.pk}/cancel-with-refund/"
    assert client_for(p3.other_admin, portal_host).get(url).status_code == 404
    response = client_for(p3.ceo, portal_host).get(url)
    assert response.status_code == 200 and b"90%" in response.content
    assert b"SOP" not in response.content


def test_cancel_past_r25_stage_ends_the_case(p4, django_capture_on_commit_callbacks):
    from accounts.models import User
    from accreditation.models import QACase
    from payments import cancellations
    from qa_core.models import QATaskLink

    req = p4.case.qa_request
    ceo = User.objects.filter(institution=req.institution, user_type="institution",
                              role__code="inst-chief-executive").first()   # only the CE may cancel
    assert ceo is not None and cancellations.is_cancellable(req)
    with django_capture_on_commit_callbacks(execute=True):
        cancellations.cancel(req, actor=ceo, reason="Merging with another request", declaration=True)
    req.refresh_from_db()
    case = QACase.unscoped.get(pk=p4.case.pk)
    assert req.status == "cancelled" and case.status == "cancelled"
    assert not QATaskLink.objects.filter(object_id=case.pk, closed_at__isnull=True,
                                         content_type__model="qacase").exists()


# ---------------------------------------------------------------------------
# Refund lifecycle
# ---------------------------------------------------------------------------
def test_paid_cancel_refund_finance_then_es(p3, es, django_capture_on_commit_callbacks):
    from payments import cancellations
    from payments.models import RefundRequest

    req = _submitted(p3)
    ob = _pay(bill_of(req))
    with django_capture_on_commit_callbacks(execute=True):
        c = cancellations.cancel(req, actor=p3.ceo, reason="Funding withdrawn", declaration=True)
    rr = RefundRequest.unscoped.get(cancellation=c)
    assert rr.status == RefundRequest.DETAILS and rr.percent == 90 and rr.paid_amount == ob.amount
    assert rr.approver_kind == "es"

    fin = p3.p2.fin_officer
    with pytest.raises(ValidationError):  # no bank details yet
        cancellations.verify(rr, actor=fin)
    with pytest.raises(PermissionDenied):
        cancellations.submit_bank_details(rr, actor=p3.other_admin, bank=BANK)
    rr = cancellations.submit_bank_details(rr, actor=p3.ceo, bank=BANK)
    assert rr.status == RefundRequest.FINANCE

    with pytest.raises(PermissionDenied):  # institution users never verify
        cancellations.verify(rr, actor=p3.ceo)
    rr = cancellations.verify(rr, actor=fin, note="Payment and account checked")
    assert rr.status == RefundRequest.APPROVAL

    with pytest.raises(PermissionDenied):  # Finance cannot approve its own verification (not the ES)
        cancellations.approve(rr, actor=fin)
    with pytest.raises(PermissionDenied):
        cancellations.approve(rr, actor=p3.p2.fin_director)
    with pytest.raises(ValidationError):
        cancellations.mark_refunded(rr, actor=fin, paid_reference="TRF-1")  # not approved yet
    rr = cancellations.approve(rr, actor=es, remark="OK")
    assert rr.status == RefundRequest.APPROVED and rr.decided_by == es
    rr = cancellations.mark_refunded(rr, actor=fin, paid_reference="FBN-TRF-0001")
    assert rr.status == RefundRequest.REFUNDED and rr.paid_reference == "FBN-TRF-0001"


def test_es_reject_needs_a_reason(p3, es):
    from payments import cancellations
    from payments.models import RefundRequest

    req = _submitted(p3)
    _pay(bill_of(req))
    c = cancellations.cancel(req, actor=p3.ceo, reason="Funding withdrawn", declaration=True, bank=BANK)
    rr = RefundRequest.unscoped.get(cancellation=c)
    assert rr.status == RefundRequest.FINANCE  # bank details given with the cancellation
    rr = cancellations.verify(rr, actor=p3.p2.fin_officer)
    with pytest.raises(ValidationError):
        cancellations.reject(rr, actor=es, remark="")
    rr = cancellations.reject(rr, actor=es, remark="Payment was a waiver credit")
    assert rr.status == RefundRequest.REJECTED


def test_nbte_withdrawal_full_refund_and_only_es_may_withdraw(p3, es):
    from payments import cancellations
    from payments.models import RefundRequest

    req = _submitted(p3)
    ob = _pay(bill_of(req))
    with pytest.raises(PermissionDenied):
        cancellations.cancel(req, actor=p3.p2.officer, reason="Officer cannot do this", by_nbte=True)
    c = cancellations.cancel(req, actor=es, reason="NBTE cannot run this exercise this year", by_nbte=True)
    rr = RefundRequest.unscoped.get(cancellation=c)
    assert c.by_nbte and rr.percent == 100 and rr.refund_amount == ob.amount and rr.admin_fee == 0


def test_without_es_approval_the_director_of_finance_approves(p3, es):
    from payments import cancellations
    from payments.models import RefundRequest

    set_config("qa.refund.requires_es_approval", "false")
    req = _submitted(p3)
    _pay(bill_of(req))
    c = cancellations.cancel(req, actor=p3.ceo, reason="Funding withdrawn", declaration=True, bank=BANK)
    rr = cancellations.verify(RefundRequest.unscoped.get(cancellation=c), actor=p3.p2.fin_officer)
    assert rr.approver_kind == "finance"
    with pytest.raises(PermissionDenied):
        cancellations.approve(rr, actor=es)
    rr = cancellations.approve(rr, actor=p3.p2.fin_director)
    assert rr.status == RefundRequest.APPROVED


def test_refund_lapses_after_the_window(p3):
    import datetime

    from django.utils import timezone

    from payments import cancellations
    from payments.models import RefundRequest

    req = _submitted(p3)
    _pay(bill_of(req))
    c = cancellations.cancel(req, actor=p3.ceo, reason="Funding withdrawn", declaration=True)
    rr = RefundRequest.unscoped.get(cancellation=c)
    assert cancellations.lapse_overdue(timezone.now() + datetime.timedelta(days=91)) == 1
    rr.refresh_from_db()
    assert rr.status == RefundRequest.LAPSED


def test_staff_refund_pages(p3, es, client_for, django_capture_on_commit_callbacks):
    from payments import cancellations
    from payments.models import RefundRequest

    req = _submitted(p3)
    _pay(bill_of(req))
    c = cancellations.cancel(req, actor=p3.ceo, reason="Funding withdrawn", declaration=True, bank=BANK)
    rr = RefundRequest.unscoped.get(cancellation=c)
    fin = client_for(p3.p2.fin_officer)
    page = fin.get("/qa/finance/refunds/")
    assert page.status_code == 200 and rr.reference.encode() in page.content and b"qa-hero" in page.content
    assert fin.get(f"/qa/finance/refund-requests/{rr.pk}/").status_code == 200
    assert fin.post(f"/qa/finance/refund-requests/{rr.pk}/verify/", {"note": "ok"}).status_code == 200
    rr.refresh_from_db()
    assert rr.status == RefundRequest.APPROVAL
    assert fin.post(f"/qa/finance/refund-requests/{rr.pk}/approve/").status_code == 403
    assert client_for(es).post(f"/qa/finance/refund-requests/{rr.pk}/approve/").status_code == 200
    rr.refresh_from_db()
    assert rr.status == RefundRequest.APPROVED
