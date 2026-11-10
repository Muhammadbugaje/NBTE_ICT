"""Issuing generated documents (v2.1 X22, X24, X25; spec G5.d).

Public API
----------
``issue(template_code, subject, *, actor, ...)``
    Register one document (reference, short code, 144-bit token, certified
    payload + SHA-256 digest, signatory snapshot) and render its PDF with the
    signatories' stored signatures (``signature.png_for(user, at)``), the QR
    (only ``<verify base>/v/<token>/``) and the short code. Provisional
    documents carry the PROVISIONAL watermark.

``approval_recorded(event_code, subject, *, actor, ...)``
    The hook every document-generating approval calls *inside its
    transaction*: the active ``DocumentRule`` for the event issues the
    document. When the approver must sign (Director rank and above,
    ``qa.signatures.required_min_rank``) and has no signature, it raises
    ``signature.SignatureMissing`` and the approval rolls back with a friendly
    message pointing at My profile -> Signature.

``verification(kind, obj, institution=None)``
    ``{"url", "code", "document"}`` for documents rendered elsewhere (the
    external memo letter, messaging.memo_documents) - created once per
    (object, institution) and reused on every render.
"""
from __future__ import annotations

import logging

from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

import config

from . import codes
from . import signature as sig

logger = logging.getLogger(__name__)

DEFAULT_PUBLIC_FIELDS = ["kind", "reference", "institution", "code", "amount", "signatories", "issued_at", "status"]
DEFAULT_POPUP = ("This is a provisional copy generated automatically. Please await the official document, which "
                 "NBTE will upload or send to you.")


class _Safe(dict):
    def __missing__(self, key):
        return f"[{key.upper()}]"


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------
def verify_base():
    base = str(config.get("qa.documents.verify_base_url", "") or "").strip().rstrip("/")
    if base:
        return base
    from qa_core.hosts import portal_base_url

    return portal_base_url()


def verify_url(token):
    return f"{verify_base()}/v/{token}/"


def scan_url():
    return f"{verify_base()}/scan/"


def popup_text():
    return str(config.get("qa.documents.provisional_popup_text", DEFAULT_POPUP) or DEFAULT_POPUP)


def public_fields_default():
    value = config.get("qa.verify.public_fields_default", DEFAULT_PUBLIC_FIELDS)
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except ValueError:
            value = DEFAULT_PUBLIC_FIELDS
    return list(value) if isinstance(value, (list, tuple)) else list(DEFAULT_PUBLIC_FIELDS)


# ---------------------------------------------------------------------------
# Signatories
# ---------------------------------------------------------------------------
def person_name(user):
    return (user.get_full_name() or user.email) if user is not None else ""


def designation_of(user):
    """What the signature block says under the name."""
    if user is None:
        return ""
    try:
        import org

        if org.is_es(user):
            return "Executive Secretary"
        dept = org.directed_department(user)
        if dept is not None:
            return f"Director, {dept.name}"
    except Exception:  # pragma: no cover - org lookups must not stop a document
        pass
    try:
        designation = user.staff_profile.designation
        if designation is not None:
            return designation.name
    except Exception:
        pass
    role = getattr(user, "role", None)
    return getattr(role, "name", "") or ""


def department_of(user):
    try:
        dept = user.staff_profile.department
        return dept.name if dept is not None else ""
    except Exception:
        return ""


def find_es():
    try:
        from messaging.routing import find_es as _find

        return _find()
    except Exception:  # pragma: no cover
        return None


def resolve_signatories(rule, subject, actor):
    """``none`` | ``actor`` | ``es`` | ``actor_and_director``."""
    staff_actor = actor if (actor is not None and getattr(actor, "pk", None)
                            and getattr(actor, "user_type", "staff") == "staff") else None
    if rule == "none":
        return []
    if rule == "es":
        es = getattr(subject, "decided_by", None) if subject is not None else None
        if es is None or not sig.signature_required(es):
            es = staff_actor if (staff_actor is not None and _is_es(staff_actor)) else find_es()
        return [es] if es is not None else []
    if rule == "actor_and_director":
        out = [staff_actor] if staff_actor else []
        try:
            import org

            dept = staff_actor.staff_profile.department if staff_actor else None
            director = org.director_of(dept) if dept else None
        except Exception:
            director = None
        if director is not None and all(director.pk != u.pk for u in out):
            out.append(director)
        return out
    return [staff_actor] if staff_actor else []


def _is_es(user):
    try:
        import org

        return org.is_es(user)
    except Exception:
        return False


def snapshot(user, at):
    current = sig.signature_at(user, at)
    return {"user_id": user.pk, "name": person_name(user), "designation": designation_of(user),
            "department": department_of(user), "signed_at": at.isoformat(),
            "signature_id": current.pk if current else None, "sha256": current.sha256 if current else ""}


def check_signers(signers, actor):
    """X23 enforcement: the approver (when they sign the document) must have a
    signature from Director rank up. Other signatories (e.g. the ES on a
    letter an officer triggers) never block someone else's approval; the
    document then prints their name without an image."""
    for user in signers:
        if actor is not None and user.pk == getattr(actor, "pk", None):
            sig.require_signature_for_approval(user)


# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------
PREFIX = {"letter": "NBTE/QA/LTR", "memo": "NBTE/MEMO", "receipt": "NBTE-RCPT", "payment_slip": "NBTE-SLIP",
          "invoice": "NBTE-INV", "notice": "NBTE/QA/NTC", "schedule": "NBTE/SCH", "payment_advice": "NBTE-PADV"}


def next_reference(kind, when=None):
    from .models import IssuedDocument

    year = (when or timezone.localdate()).year
    prefix = f"{PREFIX.get(kind, 'NBTE/DOC')}/{year}/" if "/" in PREFIX.get(kind, "/") \
        else f"{PREFIX.get(kind, 'NBTE-DOC')}-{year}-"
    taken = IssuedDocument.unscoped.filter(reference__startswith=prefix).count()
    return f"{prefix}{taken + 1:05d}"


def _unique_code():
    from .models import IssuedDocument

    for _ in range(20):
        code = codes.new_short_code()
        if not IssuedDocument.unscoped.filter(short_code=code).exists():
            return code
    raise RuntimeError("Could not find a free short code.")  # pragma: no cover - 2^40 space


# ---------------------------------------------------------------------------
# Issuing
# ---------------------------------------------------------------------------
def _institution_of(subject, institution):
    if institution is not None:
        return institution
    for path in ("institution", "obligation.institution", "case.institution"):
        obj = subject
        for part in path.split("."):
            obj = getattr(obj, part, None) if obj is not None else None
        if obj is not None and obj.__class__.__name__ == "Institution":
            return obj
    return None


def _fmt_date(value):
    return timezone.localtime(value).strftime("%d %B %Y") if value else ""


def _addressee(institution):
    if institution is None:
        return []
    lines = ["The Head of Institution,", f"{institution.name},"]
    state = getattr(institution, "state_name", "") or ""
    if state:
        lines.append(f"{state} State.")
    return lines


@transaction.atomic
def issue(template_code, subject=None, *, actor=None, institution=None, context=None, signatories=None,
          provisional=None, rule=None, kind=None, title=None, amount=None, reference=None, facts=None,
          paragraphs=None, render_pdf=True, legacy_token="", event_code="", issued_at=None, extra_payload=None,
          addressee=None, cc=""):
    """Register and render one document. Returns the ``IssuedDocument``."""
    from .models import DocumentTemplate, IssuedDocument

    tpl = DocumentTemplate.current(template_code)
    if tpl is None:
        raise ValueError(f"No active document template '{template_code}'.")
    now = issued_at or timezone.now()
    institution = _institution_of(subject, institution)
    if signatories is None:
        signatories = resolve_signatories(tpl.signatory_rule, subject, actor)
    signatories = [u for u in signatories if u is not None]
    check_signers(signatories, actor)
    if provisional is None:
        provisional = rule.provisional if (rule is not None and rule.provisional is not None) \
            else tpl.provisional_default
    kind = kind or tpl.kind
    ctx = _Safe({"institution": institution.name if institution else "", "code": institution.code if institution
                 else "", "date": _fmt_date(now)})
    ctx.update({k: v for k, v in (context or {}).items() if v not in (None, "")})
    title = (title or (tpl.subject_line.format_map(ctx) if tpl.subject_line else tpl.title))[:200]
    reference = reference or next_reference(kind, timezone.localdate())
    signer_rows = [snapshot(u, now) for u in signatories]
    if paragraphs is None:
        body = tpl.body.format_map(ctx) if tpl.body else ""
        paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    facts = [[str(k), str(v if v is not None else "")] for k, v in (facts or [])]
    payload = {
        "kind": kind, "title": title, "reference": reference,
        "institution": institution.name if institution else "", "code": institution.code if institution else "",
        "amount": f"{amount:.2f}" if amount is not None else "",
        "issued_at": now.isoformat(), "provisional": bool(provisional),
        "signatories": [{"name": s["name"], "designation": s["designation"]} for s in signer_rows],
        "body": paragraphs, "facts": facts,
        **(extra_payload or {}),
    }
    doc = IssuedDocument(
        kind=kind, template=tpl, rule=rule, event_code=event_code or (rule.event_code if rule else ""),
        reference=reference, short_code=_unique_code(), token=codes.new_token(), legacy_token=legacy_token or "",
        title=title, audience=tpl.audience, institution=institution, amount=amount, signatories=signer_rows,
        payload=payload, digest=codes.digest(payload), public_fields=public_fields_default(),
        provisional=bool(provisional), issued_by=actor if getattr(actor, "pk", None) else None, issued_at=now)
    if subject is not None and getattr(subject, "pk", None):
        doc.subject_ct = ContentType.objects.get_for_model(subject.__class__)
        doc.subject_id = subject.pk
    try:
        with transaction.atomic():
            doc.save()
    except IntegrityError:
        doc.reference = f"{reference}-{codes.new_short_code()[-4:]}"
        doc.save()
    if render_pdf:
        spec = build_spec(doc, tpl, paragraphs=paragraphs, facts=facts, signers=signatories, addressee=addressee,
                          cc=cc)
        attach_pdf(doc, render_spec(spec))
    _after_issue(doc)
    return doc


def build_spec(doc, tpl, *, paragraphs=None, facts=None, signers=(), addressee=None, cc=""):
    from .models import KIND_LABELS
    from .render import watermark_setting

    signer_specs = []
    for user in signers:
        png = None
        try:
            png = sig.png_for(user, doc.issued_at)
        except Exception:  # pragma: no cover - a missing file prints the name only
            logger.exception("Signature image missing for user %s", user.pk)
        signer_specs.append({"name": person_name(user), "designation": designation_of(user), "png": png,
                             "caption": f"Signed {timezone.localtime(doc.issued_at):%d %b %Y}" if png else ""})
    return {
        "title": doc.title, "reference": doc.reference, "kind_label": KIND_LABELS.get(doc.kind, doc.kind),
        "date": _fmt_date(doc.issued_at),
        "addressee": addressee if addressee is not None else (_addressee(doc.institution)
                                                              if tpl.letterhead == "external_letter" else []),
        "subject": doc.title, "paragraphs": paragraphs or [], "facts": facts or [], "signers": signer_specs,
        "qr_url": verify_url(doc.token) if tpl.requires_qr else "", "short_code": doc.short_code,
        "provisional": doc.provisional, "watermark": watermark_setting(),
        "footer_hint": f"Verify: {scan_url()} · {doc.short_code}", "cc": cc,
    }


def render_spec(spec):
    from .render import render

    return render(spec)


def attach_pdf(doc, pdf, name=None):
    name = name or f"{doc.reference.replace('/', '-')}.pdf"
    doc.file.save(name, ContentFile(pdf), save=False)
    doc.original_name = name
    doc.save()
    return doc


def _after_issue(doc):
    """Tell the institution about a provisional document (X25, G5.f)."""
    if not (doc.provisional and doc.institution_id and doc.audience != "internal"):
        return
    inst = doc.institution

    def _send():
        notify_institution_users(inst, f"Provisional document: {doc.title}",
                                 f"{popup_text()} Reference {doc.reference}.", url="/letters/")
    transaction.on_commit(_send)


def notify_institution_users(inst, title, body, *, url=""):
    try:
        from institutions.services import institution_users
        from notifications.models import Notification
        from notifications.services import notify

        for user in institution_users(inst):
            notify(user, title=title[:200], body=body[:2000], url=url, category=Notification.QA)
    except Exception:  # notifications never break the action
        logger.exception("Could not notify institution %s", getattr(inst, "pk", None))


# ---------------------------------------------------------------------------
# The approval hook
# ---------------------------------------------------------------------------
def rule_for(event_code):
    from .models import DocumentRule

    return DocumentRule.objects.filter(event_code=event_code, is_active=True).first()


def will_generate(event_code):
    rule = rule_for(event_code)
    return bool(rule and rule.template is not None)


def precheck(event_code, actor):
    """Call before an approval that generates a document: raises
    ``SignatureMissing`` when the approver must sign and has no signature."""
    if will_generate(event_code) and actor is not None:
        rule = rule_for(event_code)
        if rule.template.signatory_rule in ("actor", "actor_and_director") or _is_es(actor):
            sig.require_signature_for_approval(actor)


def approval_recorded(event_code, subject, *, actor=None, institution=None, context=None, **kwargs):
    """Issue the document of the active rule for ``event_code`` (or None)."""
    rule = rule_for(event_code)
    if rule is None:
        return None
    if rule.template is None:
        logger.warning("Document rule %s points at a missing template %s", event_code, rule.template_code)
        return None
    try:
        return issue(rule.template_code, subject, actor=actor, institution=institution, context=context,
                     rule=rule, event_code=event_code, **kwargs)
    except sig.SignatureMissing:
        raise
    except Exception:
        # A rendering problem must not undo an approval; ICT sees it in the log.
        logger.exception("Could not issue the document for %s", event_code)
        return None


def documents_for(subject, kind=None):
    from .models import IssuedDocument

    qs = IssuedDocument.unscoped.filter(subject_ct=ContentType.objects.get_for_model(subject.__class__),
                                        subject_id=subject.pk)
    return qs.filter(kind=kind) if kind else qs


# ---------------------------------------------------------------------------
# Documents rendered elsewhere (external memo letter)
# ---------------------------------------------------------------------------
def verification(kind, obj, *, institution=None):
    """Registry entry for a document another module renders (memos): one per
    (object, institution), created on first render. None when the rule for
    it is switched off."""
    if kind != "memo":
        return None
    rule = rule_for("memo.external_published")
    if rule is None or rule.template is None:
        return None
    from .models import IssuedDocument

    ct = ContentType.objects.get_for_model(obj.__class__)
    existing = IssuedDocument.unscoped.filter(subject_ct=ct, subject_id=obj.pk, kind="memo",
                                              institution=institution).first()
    if existing is None:
        try:
            from messaging.memo_documents import external_reference

            reference = external_reference(obj)
        except Exception:
            reference = getattr(obj, "reference", "") or ""
        if institution is not None:
            reference = f"{reference}/{institution.code}"
        signer = getattr(obj, "es_signatory", None)
        at = getattr(obj, "es_signed_at", None) or timezone.now()
        existing = issue("external_memo", obj, actor=None, institution=institution,
                         signatories=[signer] if signer is not None else [], rule=rule,
                         event_code="memo.external_published", reference=reference or None,
                         title=getattr(obj, "subject", "")[:200], render_pdf=False, issued_at=at,
                         provisional=False)
    return {"url": verify_url(existing.token), "code": existing.short_code, "document": existing}
