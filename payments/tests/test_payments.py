"""Payments (§10): webhooks only ever produce provider_reported_paid, the
Director of Finance is the only path to `confirmed`, statements and mod-97,
§10.6 edge cases, proof permissions, the bank-transfer-only fallback, and the
slip content (amount in words, QR verifies, barcode decodes)."""
import json
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from qa_core.tests.phase2_fixtures import pdf_upload

pytestmark = pytest.mark.django_db


def _ob(pk):
    from payments.models import PaymentObligation

    return PaymentObligation.unscoped.get(pk=pk)


def _open_item(ob):
    from payments.models import PaymentVerification

    return PaymentVerification.unscoped.get(obligation=ob, is_open=True)


def _pay_online(bill, user, provider="paystack"):
    from payments import services

    return services.start_online_payment(bill, provider, user=user, callback_url="https://x/cb")


# ---------------------------------------------------------------------------
# Issue + slip
# ---------------------------------------------------------------------------
def test_bill_is_published_with_slip(bill):
    from payments.models import PaymentSlip

    assert bill.status == "awaiting_payment"
    assert bill.amount == Decimal("4000000.00") and bill.amount_in_words == "Four million naira only"
    slip = PaymentSlip.unscoped.get(obligation=bill, is_current=True)
    assert slip.version == 1 and slip.bank_snapshot["account_number"] == "1234567890"


def test_bills_wait_for_a_real_bank_account(p2, proposed_inst):
    from payments import services
    from payments.models import BankAccount

    BankAccount.objects.update(is_active=False)
    ob = services.issue_obligation(proposed_inst, "institutional_processing")
    assert ob.status == "issued"
    BankAccount.objects.filter(pk=p2.bank.pk).update(is_active=True)
    services.publish_waiting()
    assert _ob(ob.pk).status == "awaiting_payment"


def test_unconfirmed_fee_cannot_be_billed(p2, proposed_inst):
    from payments import services

    with pytest.raises(ValidationError):
        services.issue_obligation(proposed_inst, "qa_request_processing")


# ---------------------------------------------------------------------------
# Webhooks never confirm
# ---------------------------------------------------------------------------
def test_paystack_webhook_only_reports_paid(bill, inst_user):
    from payments import services

    attempt = _pay_online(bill, inst_user, "paystack")
    status, _msg = services.simulate_provider_callback("paystack", attempt.provider_reference, "success")
    assert status == 200
    ob = _ob(bill.pk)
    attempt.refresh_from_db()
    assert attempt.status == "provider_reported_paid" and attempt.webhook_verified
    assert ob.status in ("provider_reported_paid", "awaiting_finance_confirmation")
    assert ob.status != "confirmed" and ob.amount_paid == 0 and ob.confirmed_at is None
    assert _open_item(ob).source == "provider"


def test_remita_webhook_only_reports_paid(bill, inst_user):
    from payments import services

    attempt = _pay_online(bill, inst_user, "remita")
    services.simulate_provider_callback("remita", attempt.provider_reference, "success")
    ob = _ob(bill.pk)
    assert ob.status in ("provider_reported_paid", "awaiting_finance_confirmation")
    from payments.models import PaymentAdvice

    assert ob.amount_paid == 0 and not PaymentAdvice.unscoped.filter(obligation=ob).exists()


def test_paystack_webhook_with_bad_signature_is_rejected(bill, inst_user, client, portal_host):
    from payments.models import WebhookLog

    attempt = _pay_online(bill, inst_user, "paystack")
    body = json.dumps({"event": "charge.success", "data": {"reference": attempt.provider_reference}}).encode()
    response = client.post("/payments/webhooks/paystack/", body, content_type="application/json",
                           HTTP_HOST=portal_host, HTTP_X_PAYSTACK_SIGNATURE="0" * 128)
    assert response.status_code == 401
    attempt.refresh_from_db()
    assert attempt.status not in ("provider_reported_paid", "paid")
    assert _ob(bill.pk).status in ("awaiting_payment", "provider_processing")
    assert WebhookLog.objects.filter(provider="paystack", signature_valid=False).exists()


def test_signed_webhook_whose_verify_disagrees_does_not_pay(bill, inst_user):
    """'webhook_only': the webhook says success but the server-side verify
    does not — verify wins."""
    from payments import services

    attempt = _pay_online(bill, inst_user, "paystack")
    services.simulate_provider_callback("paystack", attempt.provider_reference, "webhook_only")
    attempt.refresh_from_db()
    assert attempt.status != "provider_reported_paid"
    assert _ob(bill.pk).status in ("provider_processing", "awaiting_payment")


def test_failed_online_payment_returns_to_awaiting_payment(bill, inst_user):
    from payments import services

    attempt = _pay_online(bill, inst_user, "paystack")
    services.simulate_provider_callback("paystack", attempt.provider_reference, "failed")
    attempt.refresh_from_db()
    assert attempt.status == "failed"
    assert _ob(bill.pk).status == "awaiting_payment"


# ---------------------------------------------------------------------------
# The Director of Finance confirms
# ---------------------------------------------------------------------------
@pytest.fixture
def claimed(bill, inst_user):
    from payments import services

    services.submit_proof(bill, user=inst_user, upload=pdf_upload("teller.pdf"), amount_claimed=bill.amount,
                          bank_name="CBN", teller_number="T-001", paid_on=None)
    return _ob(bill.pk)


def test_director_confirmation_flow(claimed, p2):
    from payments import services
    from payments.models import PaymentAdvice

    assert claimed.status == "awaiting_finance_confirmation"
    item = _open_item(claimed)
    with pytest.raises(ValidationError):  # nothing before the first pass
        services.final_decision(item, user=p2.fin_director, decision="confirmed")
    services.first_pass(item, user=p2.fin_officer, decision="approved", amount=claimed.amount)
    assert _ob(claimed.pk).status == "awaiting_finance_confirmation"
    with pytest.raises(PermissionDenied):  # the officer can never confirm
        services.final_decision(item, user=p2.fin_officer, decision="confirmed")
    services.final_decision(item, user=p2.fin_director, decision="confirmed")
    ob = _ob(claimed.pk)
    assert ob.status == "confirmed" and ob.balance == 0 and ob.amount_paid == ob.amount
    advice = PaymentAdvice.unscoped.get(obligation=ob)
    assert advice.file and advice.amount == ob.amount


def test_director_without_permission_is_refused(claimed, p2):
    from django.contrib.auth.models import Permission

    from accounts.models import User
    from payments import services

    services.first_pass(_open_item(claimed), user=p2.fin_officer, decision="approved", amount=claimed.amount)
    perm = Permission.objects.get(codename="final_confirm_payment", content_type__app_label="payments")
    role = p2.fin_director.role
    role.permissions.remove(perm)
    for group in role.groups.all():
        group.permissions.remove(perm)
    for group in p2.fin_director.groups.all():
        group.permissions.remove(perm)
    user = User.objects.get(pk=p2.fin_director.pk)
    assert not user.has_perm("payments.final_confirm_payment")
    with pytest.raises(PermissionDenied):
        services.final_decision(_open_item(claimed), user=user, decision="confirmed")
    assert _ob(claimed.pk).status == "awaiting_finance_confirmation"


def test_first_pass_director_cannot_also_decide(claimed, p2, make_user):
    from accounts.models import User
    from payments import services
    from qa_core.departments import qa_department

    other = make_user("p2.fin.director2@t.local", role="qa-finance-director")
    other.department = qa_department("finance")
    other.save()
    other = User.objects.get(pk=other.pk)
    from django.contrib.auth.models import Permission

    # A Director of Finance who also holds the first-pass permission does a first pass …
    p2.fin_director.user_permissions.add(Permission.objects.get(codename="verify_payment",
                                                                 content_type__app_label="payments"))
    director = User.objects.get(pk=p2.fin_director.pk)
    item = _open_item(claimed)
    services.first_pass(item, user=director, decision="approved", amount=claimed.amount)
    # … and therefore cannot also decide; another Director of Finance can.
    with pytest.raises(PermissionDenied):
        services.final_decision(item, user=director, decision="confirmed")
    services.final_decision(item, user=other, decision="confirmed")
    assert _ob(claimed.pk).status == "confirmed"


def test_reject_lets_the_institution_pay_again(claimed, p2, inst_user):
    from payments import services

    item = _open_item(claimed)
    services.first_pass(item, user=p2.fin_officer, decision="rejected", reason="teller not on the statement")
    with pytest.raises(ValidationError):
        services.final_decision(item, user=p2.fin_director, decision="rejected", reason="")
    services.final_decision(item, user=p2.fin_director, decision="rejected", reason="Teller not found")
    assert _ob(claimed.pk).status == "rejected"
    services.submit_proof(_ob(claimed.pk), user=inst_user, upload=pdf_upload("t2.pdf", b"2"),
                          amount_claimed=claimed.amount, bank_name="CBN", teller_number="T-002")
    assert _ob(claimed.pk).status == "awaiting_finance_confirmation"


def test_provider_payment_needs_receipt_before_confirmation(bill, inst_user, p2):
    from payments import services

    attempt = _pay_online(bill, inst_user, "paystack")
    services.simulate_provider_callback("paystack", attempt.provider_reference, "success")
    item = _open_item(_ob(bill.pk))
    services.first_pass(item, user=p2.fin_officer, decision="approved", amount=bill.amount)
    with pytest.raises(ValidationError):
        services.final_decision(item, user=p2.fin_director, decision="confirmed")
    services.submit_proof(_ob(bill.pk), user=inst_user, upload=pdf_upload("receipt.pdf", b"r"), kind="provider_receipt",
                          attempt=attempt, amount_claimed=attempt.amount)
    services.final_decision(_open_item(_ob(bill.pk)), user=p2.fin_director, decision="confirmed")
    assert _ob(bill.pk).status == "confirmed"
    from payments.models import PaymentAttempt

    attempts = {a.provider: a.status for a in PaymentAttempt.unscoped.filter(obligation=bill)}
    assert "pending" not in attempts.values() and "initiated" not in attempts.values()


def test_sweep_never_confirms(claimed):
    from payments import services

    services.sweep()
    assert _ob(claimed.pk).status != "confirmed"


# ---------------------------------------------------------------------------
# CSV statements and mod-97
# ---------------------------------------------------------------------------
def test_mod97_reference_checks(bill):
    from payments import services
    from qa_core import references

    assert references.verify(bill.reference, "mod97")
    body, check = bill.reference.rsplit("-", 1)
    wrong = f"{body}-{(int(check) + 1) % 100:02d}"
    assert not references.verify(wrong, "mod97")
    found = services.extract_references(f"TRF/{bill.reference.lower().replace('-', ' ')}/ZIT")
    assert found and found[0][0] == bill.reference and found[0][1] is True
    assert services.extract_references(f"PAY {wrong}") == [(wrong, False)]
    assert bill.reference in services.typo_candidates(wrong) or services.typo_candidates(wrong) == []


def test_csv_parser_reads_credit_rows_and_names_missing_columns():
    from payments import services

    mapping = {"date": "Value Date", "narration": "Narration", "amount": "Credit", "date_format": "%d/%m/%Y"}
    csv = ("Value Date,Narration,Debit,Credit\n"
           "01/10/2026,Opening,,\n"
           "02/10/2026,TRF NBTE-QAP-2026-ZIT-000001-00,,\"4,000,000.00\"\n"
           "03/10/2026,Bank charge,50.00,\n").encode()
    rows = services.parse_statement_csv(csv, mapping)
    assert len(rows) == 1
    _n, date, narration, amount = rows[0]
    assert str(date) == "2026-10-02" and amount == Decimal("4000000.00") and "NBTE-QAP" in narration
    with pytest.raises(ValidationError):
        services.parse_statement_csv(b"Date,Details,Amount\n02/10/2026,x,1\n", mapping)


def test_statement_auto_match_opens_verification_but_never_confirms(bill, p2):
    from payments import services
    from payments.models import BankStatementLine

    csv = (f"Value Date,Narration,Credit\n02/10/2026,TRF {bill.reference} ZIT,\"4,000,000.00\"\n"
           f"02/10/2026,TRF UNKNOWN,100.00\n").encode()
    statement = services.import_statement(SimpleUploadedFile("stmt.csv", csv, content_type="text/csv"),
                                          user=p2.fin_officer)
    assert statement.matched == 1 and statement.unmatched == 1
    ob = _ob(bill.pk)
    assert ob.status == "awaiting_finance_confirmation"
    assert _open_item(ob).source == "statement"
    assert BankStatementLine.objects.filter(statement=statement, match_status="unmatched").count() == 1


def test_statement_line_with_bad_check_digits_suggests_candidates(bill, p2):
    from payments import services

    body, check = bill.reference.rsplit("-", 1)
    wrong = f"{body}-{(int(check) + 1) % 100:02d}"
    csv = f"Value Date,Narration,Credit\n02/10/2026,TRF {wrong},\"4,000,000.00\"\n".encode()
    statement = services.import_statement(SimpleUploadedFile("s.csv", csv, content_type="text/csv"),
                                          user=p2.fin_officer)
    line = statement.lines.get()
    assert line.match_status == "unmatched" and line.reference_check_failed
    assert bill.reference in line.candidates
    services.manual_match(line, _ob(bill.pk), user=p2.fin_officer, remark="Typo in narration")
    assert _ob(bill.pk).status == "awaiting_finance_confirmation"


# ---------------------------------------------------------------------------
# §10.6 edge cases
# ---------------------------------------------------------------------------
def _confirm(ob, p2):
    from payments import services

    item = _open_item(ob)
    services.first_pass(item, user=p2.fin_officer, decision="approved", amount=ob.amount)
    services.final_decision(item, user=p2.fin_director, decision="confirmed")
    return _ob(ob.pk)


def test_duplicate_payment_after_settlement_opens_refund(claimed, p2):
    from payments import services
    from payments.models import Refund

    ob = _confirm(claimed, p2)
    csv = f"Value Date,Narration,Credit\n05/10/2026,{ob.reference},\"4,000,000.00\"\n".encode()
    services.import_statement(SimpleUploadedFile("s.csv", csv, content_type="text/csv"), user=p2.fin_officer)
    ob = _ob(ob.pk)
    assert ob.status == "confirmed" and "duplicate" in ob.flags
    assert Refund.unscoped.filter(obligation=ob, source="duplicate").exists()


def test_money_after_cancellation_is_flagged(bill, p2):
    from payments import services
    from payments.models import Refund

    services.cancel(bill, actor=p2.fin_director, reason="reissued")
    csv = f"Value Date,Narration,Credit\n05/10/2026,{bill.reference},\"4,000,000.00\"\n".encode()
    services.import_statement(SimpleUploadedFile("s.csv", csv, content_type="text/csv"), user=p2.fin_officer)
    ob = _ob(bill.pk)
    assert ob.status == "cancelled" and "paid_after_cancellation" in ob.flags
    assert Refund.unscoped.filter(obligation=ob).exists()


def test_overpayment_is_recorded(claimed, p2):
    from payments import services

    item = _open_item(claimed)
    services.first_pass(item, user=p2.fin_officer, decision="approved", amount=claimed.amount + 500)
    services.final_decision(item, user=p2.fin_director, decision="confirmed")
    ob = _ob(claimed.pk)
    assert ob.overpaid_amount == Decimal("500.00") and ob.status in ("overpaid", "confirmed")


def test_expired_slip_regenerates_with_same_reference(bill, inst_user):
    from django.utils import timezone

    from payments import services
    from payments.models import PaymentSlip

    PaymentSlip.unscoped.filter(obligation=bill).update(expires_at=timezone.now() - timezone.timedelta(days=1))
    services.regenerate_slip(_ob(bill.pk), actor=inst_user)
    slips = list(PaymentSlip.unscoped.filter(obligation=bill).order_by("version"))
    assert [s.version for s in slips] == [1, 2]
    assert [s.is_current for s in slips] == [False, True]
    assert _ob(bill.pk).reference == bill.reference


def test_same_proof_file_on_two_bills_is_flagged(bill, p2, proposed_inst, inst_user):
    from payments import services
    from payments.models import ProofOfPayment

    other = services.issue_obligation(proposed_inst, "programme_processing", variables={"programmes": 1})
    services.submit_proof(bill, user=inst_user, upload=pdf_upload("a.pdf", b"same"), amount_claimed=bill.amount,
                          bank_name="B", teller_number="1")
    services.submit_proof(other, user=inst_user, upload=pdf_upload("b.pdf", b"same"), amount_claimed=other.amount,
                          bank_name="B", teller_number="2")
    assert ProofOfPayment.unscoped.get(obligation=other).fraud_flag


def test_future_payment_date_is_refused(bill, inst_user):
    import datetime

    from payments import services

    with pytest.raises(ValidationError):
        services.submit_proof(bill, user=inst_user, upload=pdf_upload(), amount_claimed=bill.amount,
                              paid_on=datetime.date.today() + datetime.timedelta(days=2), bank_name="B",
                              teller_number="1")


# ---------------------------------------------------------------------------
# Proof download permissions and host isolation
# ---------------------------------------------------------------------------
def test_proof_download_permissions(claimed, p2, client_for, portal_host, make_user, make_institution):
    from accounts.models import User
    from payments.models import ProofOfPayment

    proof = ProofOfPayment.unscoped.get(obligation=claimed)
    owner = User.objects.get(pk=claimed.institution.primary_user().pk)
    assert client_for(owner, portal_host).get(proof.portal_download_url()).status_code == 200
    stranger = make_user("x@other.edu.ng", role="inst-admin", user_type="institution",
                         institution=make_institution("OTH"))
    assert client_for(stranger, portal_host).get(proof.portal_download_url()).status_code == 404
    assert client_for(p2.fin_officer).get(proof.download_url()).status_code == 200
    assert client_for(p2.legal).get(proof.download_url()).status_code in (403, 404)


def test_finance_screens_are_internal_only(p2, client_for, portal_host):
    # Staff on the portal host are sent back to the internal system; the
    # portal has no Finance route at all.
    response = client_for(p2.fin_officer, portal_host).get("/qa/finance/")
    assert response.status_code in (302, 404)
    assert "/qa/finance" not in response.get("Location", "")
    assert client_for(p2.fin_officer).get("/qa/finance/").status_code == 200
    assert client_for(p2.officer).get("/qa/finance/").status_code == 403


def test_other_institution_cannot_open_bill(bill, client_for, portal_host, make_user, make_institution):
    stranger = make_user("y@other.edu.ng", role="inst-admin", user_type="institution",
                         institution=make_institution("OTH"))
    assert client_for(stranger, portal_host).get(f"/payments/{bill.pk}/").status_code in (302, 404)


# ---------------------------------------------------------------------------
# Bank-transfer-only fallback
# ---------------------------------------------------------------------------
def test_bank_transfer_only_fallback(bill, inst_user, client_for, portal_host, settings):
    from config.models import ConfigEntry
    from django.core.cache import cache

    from payments import providers, services

    ConfigEntry.objects.filter(key__in=["qa.payment.remita.enabled", "qa.payment.paystack.enabled"]).update(
        value="false")
    cache.clear()
    assert [p.code for p in providers.enabled_methods() if p.online] == []
    with pytest.raises(ValidationError):
        services.start_online_payment(_ob(bill.pk), "paystack", user=inst_user)
    page = client_for(inst_user, portal_host).get(f"/payments/{bill.pk}/")
    assert page.status_code == 200
    assert b"Pay with Paystack" not in page.content and b"Pay with Remita" not in page.content
    assert b"Bank transfer or deposit" in page.content


def test_online_methods_off_without_keys_in_production(settings):
    from payments import providers

    settings.QA_PAYMENTS_SIMULATE = False
    settings.PAYSTACK_SECRET_KEY = ""
    assert not providers.get("paystack").is_enabled()


# ---------------------------------------------------------------------------
# Slip content
# ---------------------------------------------------------------------------
def _pdf_text(data):
    import io

    pypdf = pytest.importorskip("pypdf")  # optional; not a project dependency

    return "\n".join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(data)).pages)


def test_slip_text_has_words_reference_and_bank(bill):
    from payments.models import PaymentSlip

    slip = PaymentSlip.unscoped.get(obligation=bill, is_current=True)
    with slip.file.open("rb") as fh:
        text = _pdf_text(fh.read())
    assert "Four million naira only" in text
    assert bill.reference in text
    assert "1234567890" in text and "PAYMENT SLIP" in text


def test_slip_qr_verifies(bill, client, portal_host):
    from payments import services
    from payments.models import PaymentSlip

    slip = PaymentSlip.unscoped.get(obligation=bill, is_current=True)
    token = slip.qr_payload.rstrip("/").rsplit("/", 1)[-1]
    summary = services.verification_summary(token)
    assert summary["reference"] == bill.reference and summary["current"]
    page = client.get(f"/verify/{token}/", HTTP_HOST=portal_host)
    assert page.status_code == 200 and bill.reference.encode() in page.content
    assert client.get(f"/verify/{token[:-3]}xyz/", HTTP_HOST=portal_host).status_code == 404


def test_slip_qr_and_barcode_decode_from_the_pdf(bill, tmp_path):
    """Render the PDF and decode both codes from the image."""
    import shutil
    import subprocess

    cv2 = pytest.importorskip("cv2")
    if not shutil.which("pdftoppm"):
        pytest.skip("pdftoppm is not installed")
    from payments.models import PaymentSlip

    slip = PaymentSlip.unscoped.get(obligation=bill, is_current=True)
    src = tmp_path / "slip.pdf"
    with slip.file.open("rb") as fh:
        src.write_bytes(fh.read())
    subprocess.run(["pdftoppm", "-r", "300", "-png", "-f", "1", "-l", "1", str(src), str(tmp_path / "p")], check=True)
    image = cv2.imread(str(next(tmp_path.glob("p*.png"))))
    qr_text = cv2.QRCodeDetector().detectAndDecode(image)[0]
    assert qr_text == slip.qr_payload
    assert decode_code128(image) == slip.barcode_value == bill.reference


def decode_code128(image):
    """A small Code 128 reader: scan rows across the barcode band, turn bar
    widths into modules and look them up in reportlab's own pattern table."""
    import cv2
    from reportlab.graphics.barcode.code128 import _patterns

    widths_to_value = {}
    for value, pattern in _patterns.items():
        widths_to_value[tuple(ord(ch.lower()) - ord("a") + 1 for ch in pattern)] = value
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _t, bw = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY)
    for y in range(int(bw.shape[0] * 0.2), int(bw.shape[0] * 0.45), 3):
        row = bw[y]
        runs, current, length = [], row[0], 0
        for px in row:
            if px == current:
                length += 1
            else:
                runs.append((current, length))
                current, length = px, 1
        runs.append((current, length))
        # bars are dark (0); find the longest stretch of alternating short runs
        start = next((i for i, (c, n) in enumerate(runs) if c == 0 and n < 40), None)
        if start is None:
            continue
        seq = [n for _c, n in runs[start:]]
        text = _decode_runs(seq, widths_to_value)
        if text:
            return text
    return ""


def _decode_runs(seq, table):
    for i in range(0, max(1, len(seq) - 6)):
        start6 = seq[i:i + 6]
        if len(start6) < 6:
            return ""
        unit = sum(start6) / 11.0
        mods = tuple(max(1, round(n / unit)) for n in start6)
        if table.get(mods) not in (103, 104, 105):
            continue
        values, j = [], i
        while j + 6 <= len(seq):
            chunk = seq[j:j + 6]
            unit = sum(chunk) / 11.0
            mods = tuple(max(1, round(n / unit)) for n in chunk)
            if sum(mods) != 11:
                break
            value = table.get(mods)
            if value is None:
                # stop symbol is 7 elements (2331112)
                break
            values.append(value)
            j += 6
        text = _code128_text(values)
        if text:
            return text
    return ""


def _code128_text(values):
    if len(values) < 3:
        return ""
    start, *data = values
    data, check = data[:-1], data[-1]
    if (start + sum((k + 1) * v for k, v in enumerate(data))) % 103 != check:
        return ""
    mode = {103: "A", 104: "B", 105: "C"}[start]
    out = []
    for v in data:
        if v == 99:
            mode = "C"
        elif v == 100:
            mode = "B"
        elif v == 101:
            mode = "A"
        elif mode == "C":
            out.append(f"{v:02d}")
        elif mode == "B":
            out.append(chr(v + 32))
        else:
            out.append(chr(v + 32) if v < 64 else chr(v - 64))
    return "".join(out)
