"""Return to the mobile app after an online payment (Phase 9).

When the app starts a payment with ``"return_to": "app"`` (API
``payments/<id>/online/``), the provider's return URL is not the signed-in
portal page but ``payments/<id>/app-return/<token>/`` on the portal host:

* the phone's browser has no website session, so this page needs none; the
  ``token`` (signed bill id, valid APP_RETURN_MAX_AGE) is what allows it;
* it checks the attempt with the provider exactly like the portal page does
  (``services.verify_on_return``), then shows a "Return to the Digital NBTE
  app" button and redirects to
  ``digitalnbte://payment-return?bill=<id>&status=<paid|pending|failed>&reference=<ref>``;
* it shows no bill details (amount, institution) - the app reloads the bill.

The normal website flow (``qa_portal:payment``) is unchanged.
"""
from __future__ import annotations

from urllib.parse import urlencode

from django.core import signing
from django.urls import reverse

SALT = "payments.app_return"
APP_RETURN_MAX_AGE = 3 * 24 * 3600
DEEP_LINK = "digitalnbte://payment-return"
RAW_FLAG = "return_to"


def make_token(obligation_pk):
    return signing.TimestampSigner(salt=SALT).sign(str(obligation_pk))


def token_ok(token, obligation_pk):
    try:
        value = signing.TimestampSigner(salt=SALT).unsign(token, max_age=APP_RETURN_MAX_AGE)
    except signing.BadSignature:
        return False
    return value == str(obligation_pk)


def return_url(obligation, request=None):
    """Absolute portal URL the provider sends the payer back to."""
    from qa_core.hosts import portal_absolute

    return portal_absolute(reverse("qa_portal:payment_app_return", args=[obligation.pk, make_token(obligation.pk)]),
                           request)


def mark_attempt(attempt):
    """Remember on the attempt that the payer goes back to the app (read by
    the development simulator, which ignores provider return URLs)."""
    raw = dict(attempt.raw_request or {})
    raw[RAW_FLAG] = "app"
    attempt.raw_request = raw
    attempt.save(update_fields=["raw_request"])


def is_app_attempt(attempt):
    return bool(attempt is not None and (attempt.raw_request or {}).get(RAW_FLAG) == "app")


def status_of(obligation, attempt):
    """paid | pending | failed, as the app shows it."""
    if attempt is not None and attempt.status == "failed":
        return "failed"
    if (attempt is not None and attempt.status == "provider_reported_paid") or obligation.is_settled:
        return "paid"
    return "pending"


def deep_link(obligation, attempt):
    return DEEP_LINK + "?" + urlencode({"bill": obligation.pk, "status": status_of(obligation, attempt),
                                        "reference": attempt.provider_reference if attempt is not None else ""})


def obligation_for_token(pk):
    """The bill behind a verified app-return token (the token is the permission)."""
    from .models import PaymentObligation
    return PaymentObligation.unscoped.filter(pk=pk).first()


def latest_attempt(obligation):
    from .models import PaymentAttempt
    return PaymentAttempt.unscoped.filter(obligation=obligation).order_by("-created_at").first()
