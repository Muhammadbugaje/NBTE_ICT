"""Payment documents in the verification registry (X22).

Called by ``payments.services`` (and by the gateway work in pf-pay):

* ``register_slip(obligation, slip)`` - before the slip PDF is drawn: creates
  the registry row and points ``slip.qr_payload`` at ``/v/<token>/`` (the
  short code is printed under the QR). ``finish_slip(doc, slip)`` links the
  stored PDF afterwards.
* ``issue_receipt(payment, actor=None)`` - the official receipt when the
  Director of Finance confirms (``payment.confirmed`` -> ``fee_receipt``). It
  replaces the Payment Advice PDF (the advice row is kept and its file *is*
  the receipt), signed with the DoF's stored signature. ``payment`` may be a
  ``PaymentAdvice`` or a ``PaymentObligation`` (its latest advice). Idempotent.

Slips and advices printed before v2.1 keep verifying: their signed tokens are
stored as ``legacy_token`` (data migration 0003) and resolved by Scan and by
the old ``/verify/<token>/`` page.
"""
from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from . import issue as issuing


def legacy_token_of(qr_payload):
    """The signed token inside an old ``.../verify/<token>/`` URL."""
    text = (qr_payload or "").strip().rstrip("/")
    if "/verify/" in text:
        return text.rsplit("/verify/", 1)[1].split("/")[0]
    return ""


def _money(value):
    try:
        from payments.money import naira

        return naira(value)
    except Exception:  # pragma: no cover
        return f"NGN {value:,.2f}"


def register_slip(obligation, slip, *, actor=None):
    rule = issuing.rule_for("payment.slip_generated")
    if rule is None:
        return None
    doc = issuing.issue(
        "payment_slip", obligation, actor=actor, institution=obligation.institution, rule=rule,
        event_code="payment.slip_generated", kind="payment_slip", amount=slip.amount,
        reference=f"{obligation.reference}-S{slip.version}", title=f"Payment slip · {obligation.description}"[:200],
        render_pdf=False, signatories=[], issued_at=slip.generated_at,
        extra_payload={"payment_reference": obligation.reference, "fee": obligation.description,
                       "slip_version": slip.version,
                       "valid_until": slip.expires_at.isoformat() if slip.expires_at else ""})
    slip.qr_payload = issuing.verify_url(doc.token)
    slip.short_code = doc.short_code  # printed under the QR (payments.pdfs.render_slip)
    return doc


def finish_slip(doc, slip):
    """Point the registry row at the slip's stored PDF (no second copy)."""
    if doc is None or not slip.file:
        return doc
    doc.file.name = slip.file.name
    doc.original_name = slip.original_name or slip.file.name.rsplit("/", 1)[-1]
    doc.size_bytes, doc.sha256 = slip.size_bytes, slip.sha256
    doc.save(update_fields=["file", "original_name", "size_bytes", "sha256", "updated_at"])
    return doc


def _advice_of(payment):
    from payments.models import PaymentAdvice, PaymentObligation

    if isinstance(payment, PaymentAdvice):
        return payment, payment.obligation
    if isinstance(payment, PaymentObligation):
        return PaymentAdvice.unscoped.filter(obligation=payment).order_by("-generated_at").first(), payment
    raise TypeError("issue_receipt takes a PaymentAdvice or a PaymentObligation.")


def receipt_for(payment):
    advice, obligation = _advice_of(payment)
    subject = advice or obligation
    return issuing.documents_for(subject, "receipt").order_by("-issued_at").first()


def issue_receipt(payment, actor=None):
    """The signed, QR-registered receipt for a confirmed payment."""
    from django.core.files.base import ContentFile

    from payments.money import amount_in_words

    advice, obligation = _advice_of(payment)
    existing = receipt_for(payment)
    if existing is not None:
        return existing
    rule = issuing.rule_for("payment.confirmed")
    if rule is None:
        return None
    item = getattr(advice, "verification", None) if advice else None
    approver = getattr(item, "final_approver", None) if item else None
    signer = approver or actor
    amount = advice.amount if advice else (obligation.amount_paid if hasattr(obligation, "amount_paid")
                                           else obligation.amount)
    confirmed_at = getattr(item, "final_decided_at", None) or timezone.now()
    facts = [
        ("Institution", f"{obligation.institution.name} ({obligation.institution.code})"),
        ("Payment reference", obligation.reference),
    ]
    if obligation.source_reference:
        facts.append(("Application / request", obligation.source_reference))
    facts += [("Fee", obligation.description), ("Amount received", _money(amount)),
              ("In words", amount_in_words(amount))]
    if item is not None:
        facts.append(("Paid by", dict(item.SOURCE_CHOICES).get(item.source, item.source)))
    facts.append(("Confirmed on", timezone.localtime(confirmed_at).strftime("%d %b %Y, %H:%M")))
    if advice is not None:
        facts.append(("Advice reference", advice.reference))
    doc = issuing.issue(
        "fee_receipt", advice or obligation, actor=signer, institution=obligation.institution, rule=rule,
        event_code="payment.confirmed", kind="receipt", amount=amount,
        reference=(advice.reference if advice else None), legacy_token=legacy_token_of(advice.qr_payload)
        if advice else "", signatories=[signer] if signer is not None else [], facts=facts,
        context={"fee": obligation.description}, addressee=[], issued_at=confirmed_at,
        extra_payload={"payment_reference": obligation.reference, "fee": obligation.description})
    if advice is not None and doc.file:
        # The advice row keeps its reference; its PDF becomes the receipt.
        old = advice.file.name if advice.file else ""
        with doc.file.open("rb") as fh:
            pdf = fh.read()
        advice.qr_payload = issuing.verify_url(doc.token)
        advice.file.save(f"receipt-{advice.reference}.pdf", ContentFile(pdf), save=False)
        advice.save()
        if old and old != advice.file.name:
            try:
                advice.file.storage.delete(old)
            except Exception:  # pragma: no cover
                pass
    return doc


def slip_document(obligation, version):
    from .models import IssuedDocument

    return (IssuedDocument.unscoped.filter(subject_ct=ContentType.objects.get_for_model(obligation.__class__),
                                           subject_id=obligation.pk, kind="payment_slip",
                                           payload__slip_version=version).first())
