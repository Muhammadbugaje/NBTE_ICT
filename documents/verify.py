"""Verification: Scan (camera, image, typed code) and the public /v/<token>/ page (X26, S-19, S-20).

``check(request, raw, via)`` is the one entry point the views use: it applies
the per-IP rate limit (``qa.verify.rate_limit_per_hour``), resolves the input
(registry token, short code, a pre-v2.1 signed slip/advice token), writes the
``VerificationLog`` row, bumps the document's counter and alerts ICT when a
revoked document is scanned. It returns ``(card, http_status)`` where
``card`` is the dict the result-card template renders.

Anonymous visitors get ``public_fields`` only (signatories' names and
designations, never e-mails or phones; no signature images unless
``qa.verify.public_signature_thumbnails`` is switched on). Signed-in staff see
signature thumbnails (inline data: URIs - no URL ever serves a signature),
the subject's internal link and the last verifications.
"""
from __future__ import annotations

import logging
import re

from django.db.models import F
from django.utils import timezone

import config

from . import codes

logger = logging.getLogger(__name__)

STATUS_LABELS = {"valid": "Valid", "superseded": "Superseded by a newer copy", "revoked": "Revoked",
                 "not_found": "Not an NBTE code", "rate_limited": "Too many checks", "legacy": "Valid"}
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,40}$")


def rate_limit():
    try:
        return max(1, int(config.get("qa.verify.rate_limit_per_hour", 60)))
    except (TypeError, ValueError):
        return 60


def extract(raw):
    """What was scanned or typed -> (kind, value). Accepts full URLs of any host."""
    text = (raw or "").strip()
    if not text:
        return "empty", ""
    text = text.split("#", 1)[0].split("?", 1)[0]
    m = re.search(r"/v/([A-Za-z0-9_-]{20,40})/?$", text)
    if m:
        return "token", m.group(1)
    m = re.search(r"/verify/([^/]+)/?$", text)
    if m and "/verify/staff/" not in text:
        return "legacy", m.group(1)
    code = codes.normalise_short_code(text)
    if code and len(text) <= 20:
        return "code", code
    if TOKEN_RE.match(text):
        return "token", text
    if ":" in text and len(text) < 500 and "/" not in text:
        return "legacy", text  # a bare signed token
    return "unknown", text[:80]


def lookup(raw):
    """-> (IssuedDocument | None, legacy_summary | None, kind, value)."""
    from .models import IssuedDocument

    kind, value = extract(raw)
    doc = legacy = None
    if kind == "token":
        doc = IssuedDocument.unscoped.filter(token=value).first()
    elif kind == "code":
        doc = IssuedDocument.unscoped.filter(short_code=value).first()
    elif kind == "legacy":
        doc = IssuedDocument.unscoped.filter(legacy_token=value).first()
        if doc is None:
            try:
                from payments.services import verification_summary

                legacy = verification_summary(value)
            except Exception:  # pragma: no cover
                legacy = None
    return doc, legacy, kind, value


# ---------------------------------------------------------------------------
# What the card shows
# ---------------------------------------------------------------------------
def _money(value):
    if value is None:
        return ""
    try:
        from payments.money import naira

        return naira(value)
    except Exception:  # pragma: no cover
        return f"NGN {value:,.2f}"


def _thumb(signer, at):
    from django.contrib.auth import get_user_model

    from . import signature

    user = get_user_model().objects.filter(pk=signer.get("user_id")).first()
    if user is None:
        return ""
    try:
        return signature.data_uri_for(user, at) or ""
    except Exception:  # pragma: no cover
        return ""


def _staff(user):
    return bool(user is not None and user.is_authenticated and getattr(user, "user_type", "staff") == "staff")


def card_for(doc, *, user=None):
    """The result card for a registry document, reduced to ``public_fields``
    unless the viewer is NBTE staff."""
    from django.utils.dateparse import parse_datetime

    staff = _staff(user)
    fields = set(doc.public_fields or [])
    show_thumbs = staff or bool(config.get("qa.verify.public_signature_thumbnails", False))
    official = doc.official_copy
    signers = []
    for s in doc.signatories or []:
        at = parse_datetime(s.get("signed_at") or "") or doc.issued_at
        signers.append({"name": s.get("name", ""), "designation": s.get("designation", ""), "signed_at": at,
                        "on_file": bool(s.get("signature_id")),
                        "thumb": _thumb(s, at) if (show_thumbs and s.get("signature_id")) else ""})
    payload = doc.payload or {}
    card = {
        "found": True, "status": doc.status, "status_label": STATUS_LABELS.get(doc.status, doc.status),
        "kind_label": doc.kind_label, "title": doc.title, "provisional": doc.provisional,
        "official_at": official.uploaded_at if official else None, "short_code": doc.short_code,
        "reference": doc.reference if (staff or "reference" in fields) else "",
        "institution": (payload.get("institution") or "") if (staff or "institution" in fields) else "",
        "code": (payload.get("code") or "") if (staff or "code" in fields) else "",
        "amount": _money(doc.amount) if (doc.amount is not None and (staff or "amount" in fields)) else "",
        "payment_reference": payload.get("payment_reference", "") if staff or "reference" in fields else "",
        "issued_at": doc.issued_at if (staff or "issued_at" in fields) else None,
        "signatories": signers if (staff or "signatories" in fields) else [],
        "revoked_at": doc.revoked_at, "staff": staff, "legacy": False,
        "verify_count": doc.verify_count if staff else None,
    }
    if doc.status == "superseded" and doc.superseded_by_id:
        card["superseded_by"] = doc.superseded_by.reference
    if staff:
        card["revoke_reason"] = doc.revoke_reason
        card["document_pk"] = doc.pk
        try:
            from django.urls import NoReverseMatch, reverse

            card["register_url"] = reverse("documents_register:detail", args=[doc.pk])
        except NoReverseMatch:  # the portal URLconf has no register
            card["register_url"] = ""
        card["history"] = list(doc.verifications.order_by("-at")[:10])
    return card


def card_for_legacy(summary):
    return {"found": True, "status": "valid" if summary.get("current", True) else "superseded",
            "status_label": "Valid" if summary.get("current", True) else "Superseded by a newer copy",
            "kind_label": summary.get("title", "Payment document"), "title": summary.get("description", ""),
            "reference": summary.get("reference", ""), "institution": summary.get("institution", ""),
            "code": summary.get("code", ""), "amount": summary.get("amount", ""), "issued_at": summary.get("issued"),
            "signatories": [], "provisional": False, "legacy": True, "official_at": None,
            "payment_reference": summary.get("payment_reference", "")}


def not_found_card(status="not_found"):
    return {"found": False, "status": status, "status_label": STATUS_LABELS[status]}


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------
def check(request, raw, via="code"):
    from core import ratelimit

    from .models import VerificationLog

    ip = ratelimit.client_ip(request)
    user = request.user if getattr(request, "user", None) is not None and request.user.is_authenticated else None
    host = "portal" if getattr(request, "is_qa_portal", False) else "staff"
    via = via if via in dict(VerificationLog.VIA_CHOICES) else "code"
    if ratelimit.is_limited("doc-verify", ip, rate_limit()):
        _log(None, raw, via, "rate_limited", ip, user, host, hashed=True)
        return not_found_card("rate_limited"), 429
    ratelimit.hit("doc-verify", ip, 60 * 60)
    doc, legacy, _kind, value = lookup(raw)
    if doc is not None:
        _log(doc, value, via, doc.status, ip, user, host)
        type(doc).unscoped.filter(pk=doc.pk).update(verify_count=F("verify_count") + 1,
                                                    last_verified_at=timezone.now())
        doc.refresh_from_db()
        if doc.status == "revoked":
            _alert_revoked(doc, ip)
        return card_for(doc, user=user), 200
    if legacy is not None:
        _log(None, codes.hashed(value), via, "legacy", ip, user, host)
        return card_for_legacy(legacy), 200
    _log(None, raw, via, "not_found", ip, user, host, hashed=True)
    return not_found_card(), 404


def _log(doc, raw, via, result, ip, user, host, hashed=False):
    from .models import VerificationLog

    try:
        VerificationLog.objects.create(document=doc, token_or_code=codes.hashed(raw) if hashed else (raw or "")[:80],
                                       via=via, ip=ip if ip and ip != "unknown" else None, user=user, result=result,
                                       host=host)
    except Exception:  # pragma: no cover - logging never blocks a check
        logger.exception("Could not write the verification log")


def _alert_revoked(doc, ip):
    """A revoked document was scanned: possible fraud (G5.f) - alert the
    holders of documents.view_verification_log (Director of ICT, ES)."""
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    try:
        from notifications.models import Notification
        from notifications.services import notify

        c = "view_verification_log"
        perm = (Q(user_permissions__codename=c) | Q(groups__permissions__codename=c)
                | Q(role__permissions__codename=c) | Q(role__groups__permissions__codename=c))
        users = get_user_model().objects.filter(is_active=True, user_type="staff").filter(perm).distinct()[:20]
        for user in users:
            notify(user, title=f"Revoked document scanned: {doc.reference}",
                   body=f"Someone checked {doc.title} ({doc.reference}), which was revoked. Check the verification log.",
                   url=f"/qa/documents/{doc.pk}/", category=Notification.QA, level="warning")
    except Exception:
        logger.exception("Could not alert ICT about a revoked document scan")
