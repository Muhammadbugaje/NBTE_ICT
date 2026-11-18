"""Outcome letters (B24) — Phase 8.

Stage 17 issues one letter per programme: an **original** and a **duplicate**
that carries a visible diagonal watermark (``qa.letters.watermark``). Both are
ReportLab PDFs stored as restricted files (``OutcomeLetterCopy``), downloaded
through ``core:protected_download`` (staff) or ``qa_portal:file`` (the
institution). The wording comes from ``qa.letters.template`` and the
signatory from ``qa.letters.signatory``; the settings used are frozen on the
letter (``template_snapshot``, ``template_version``).

Regenerating (``regenerate_letter``) issues a new reference and keeps the old
letter (no longer current).
"""
# scoped-by-caller
from __future__ import annotations

import hashlib
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

import config
from qa_core import references, timeline

from .models import OutcomeLetter, OutcomeLetterCopy

ADDRESS = "Plot B, Bida Road, P.M.B. 2239, Kaduna, Nigeria"


def _json_setting(key, default):
    value = config.get(key, default)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = default
    return value if isinstance(value, dict) else default


def template_settings():
    from qa_core.settings_catalogue import LETTER_TEMPLATE

    return {"template": {**LETTER_TEMPLATE, **_json_setting("qa.letters.template", LETTER_TEMPLATE)},
            "signatory": _json_setting("qa.letters.signatory", {"name": "", "title": "Executive Secretary"}),
            "watermark": _json_setting("qa.letters.watermark", {"text": "NBTE DUPLICATE", "opacity": 0.12,
                                                                "angle": 45})}


def _version(snapshot):
    raw = json.dumps(snapshot, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _fields(letter, decision):
    from .qa_services import window_months

    case, programme = letter.case, decision.programme
    qa_type = case.qa_request.qa_type.name if case.qa_request_id else "Quality assurance"
    expiry = decision.expiry_date
    reassess = None
    if expiry:
        from .models import add_months

        reassess = add_months(expiry, -window_months())
    fmt = "%d %B %Y"
    return _Safe(
        institution=case.institution.name, programme=programme.name,
        level=programme.level.name if programme.level_id else "",
        qa_type=qa_type, qa_type_lower=qa_type.lower(), qa_type_upper=qa_type.upper(),
        approval_type=decision.approval_type.name.lower() if decision.approval_type_id else "",
        streams=decision.approved_streams,
        commencement=decision.commencement_date.strftime(fmt) if decision.commencement_date else "",
        expiry=expiry.strftime(fmt) if expiry else "",
        reassessment_from=reassess.strftime(fmt) if reassess else "",
        reference=letter.reference, date=timezone.localdate().strftime(fmt))


def verification_record(letter, decision):
    """v2.3 (Phase 3): every outcome letter is in the public verification
    registry (``documents.IssuedDocument``), so its QR code verifies on the
    Scan page like any other NBTE document. One record per letter."""
    from django.contrib.contenttypes.models import ContentType

    from documents import codes
    from documents.issue import _unique_code, public_fields_default
    from documents.models import IssuedDocument

    ct = ContentType.objects.get_for_model(OutcomeLetter)
    existing = IssuedDocument.unscoped.filter(subject_ct=ct, subject_id=letter.pk).first()
    if existing is not None:
        return existing
    case, programme = letter.case, decision.programme
    payload = {
        "kind": "letter", "title": f"Outcome of quality assurance: {programme.name}", "reference": letter.reference,
        "institution": case.institution.name, "code": case.institution.code, "programme": programme.name,
        "outcome": decision.get_outcome_display(),
        "approval_type": decision.approval_type.name if decision.approval_type_id else "",
        "commencement": decision.commencement_date.isoformat() if decision.commencement_date else "",
        "expiry": decision.expiry_date.isoformat() if decision.expiry_date else "",
        "issued_at": letter.generated_at.isoformat(), "provisional": False,
    }
    # A re-issued letter (after a rollback, or a duplicate) keeps its reference
    # on paper; the registry row gets a suffix so each record stays unique.
    reference, n = letter.reference, 1
    while IssuedDocument.unscoped.filter(reference=reference).exists():
        n += 1
        reference = f"{letter.reference}/{n}"
    return IssuedDocument.unscoped.create(
        kind="letter", reference=reference[:IssuedDocument._meta.get_field("reference").max_length],
        short_code=_unique_code(), token=codes.new_token(),
        title=payload["title"][:200], audience="institution", institution=case.institution, payload=payload,
        digest=codes.digest(payload), public_fields=public_fields_default(), provisional=False,
        issued_by=letter.generated_by, issued_at=letter.generated_at, subject_ct=ct, subject_id=letter.pk)


def render_pdf(letter, decision, settings_snapshot, *, duplicate=False):
    """The letter as PDF bytes, on the one NBTE document engine
    (``documents.render``): letterhead, the faint crest behind the text, the
    verification QR, fitted onto one page. The duplicate carries the
    ``qa.letters.watermark`` stamp across the page."""
    from documents.issue import scan_url, verify_url
    from documents.render import render

    tpl, sig, mark = settings_snapshot["template"], settings_snapshot["signatory"], settings_snapshot["watermark"]
    data = _fields(letter, decision)
    record = verification_record(letter, decision)
    paragraphs = [str(tpl.get("salutation", "Dear Sir/Madam,")).format_map(data)]
    paragraphs += [str(p).format_map(data) for p in (tpl.get("approve" if decision.outcome == "approve" else "deny")
                                                     or [])]
    weaknesses = [str(w).strip() for w in (decision.weaknesses or []) if str(w).strip()]
    if weaknesses:
        intro = str(tpl.get("weaknesses_intro", "The following weaknesses were identified:")).format_map(data)
        paragraphs.append(intro + "\n" + "\n".join(f"{i}. {w}" for i, w in enumerate(weaknesses, start=1)))
    closing = str(tpl.get("closing", "")).format_map(data)
    if closing:
        paragraphs.append(closing)
    title = (sig.get("title") or "Executive Secretary").strip()
    designation = title if title.lower() == "executive secretary" else f"{title} · For: Executive Secretary, NBTE"
    heading = str(tpl.get("heading", "")).format_map(data).strip()
    subject = str(tpl.get("subject", "")).format_map(data).strip()
    spec = {
        "title": f"Outcome letter {letter.reference}" + (" (duplicate)" if duplicate else ""),
        "reference": letter.reference, "kind_label": "Outcome letter" + (" · duplicate" if duplicate else ""),
        "date": data["date"], "addressee": ["The Chief Executive", data["institution"]],
        "subject": " — ".join(x for x in (heading, subject) if x) or f"Outcome: {data['programme']}",
        "paragraphs": paragraphs, "facts": [], "address": ADDRESS,
        "signers": [{"name": sig.get("name") or "", "designation": designation, "png": None}],
        "qr_url": verify_url(record.token), "short_code": record.short_code,
        "footer_hint": f"Verify: {scan_url()} · {record.short_code}",
        "provisional": bool(duplicate),
        "watermark": {"text": str(mark.get("text") or "NBTE DUPLICATE"), "opacity": mark.get("opacity", 0.12),
                      "angle": mark.get("angle", 45)},
        "provisional_banner": "DUPLICATE COPY — the original letter is held by the institution." if duplicate else "",
        "fit_one_page": True,
    }
    return render(spec)


def _store(letter, kind, content):
    copy = OutcomeLetterCopy(letter=letter, kind=kind)
    name = f"{letter.reference.replace('/', '-')}-{kind}.pdf"
    copy.file.save(name, ContentFile(content), save=False)
    copy.original_name = name
    copy.size_bytes = len(content)
    copy.sha256 = hashlib.sha256(content).hexdigest()
    copy.save()
    return copy


@transaction.atomic
def issue(decision, *, actor=None, reason=""):
    """Create the letter (reference, original + duplicate PDFs) for an ES
    final decision. Earlier letters of the same decision stop being current."""
    snapshot = template_settings()
    previous = OutcomeLetter.unscoped.filter(decision=decision).order_by("-version").first()
    OutcomeLetter.unscoped.filter(decision=decision, is_current=True).update(is_current=False,
                                                                          updated_at=timezone.now())
    letter = OutcomeLetter.unscoped.create(
        case=decision.case, decision=decision, programme=decision.programme,
        reference=references.issue("letter"), version=(previous.version + 1) if previous else 1, is_current=True,
        template_version=_version(snapshot), template_snapshot=snapshot,
        generated_by=actor if getattr(actor, "pk", None) else None, regenerated_reason=(reason or "")[:255])
    _store(letter, OutcomeLetterCopy.ORIGINAL, render_pdf(letter, decision, snapshot))
    _store(letter, OutcomeLetterCopy.DUPLICATE, render_pdf(letter, decision, snapshot, duplicate=True))
    return letter


def can_regenerate(user):
    return bool(user and user.is_authenticated and getattr(user, "user_type", "staff") == "staff"
                and (user.is_superuser or user.has_perm("accreditation.regenerate_letter")))


@transaction.atomic
def regenerate(letter, *, user, reason):
    if not can_regenerate(user):
        raise PermissionDenied("Regenerating a letter needs accreditation.regenerate_letter.")
    if len((reason or "").strip()) < 5:
        raise ValidationError("Say why the letter is regenerated (at least 5 characters).")
    new = issue(letter.decision, actor=user, reason=reason.strip())
    timeline.record(letter.case.institution, "letter.regenerated", subject=new, actor=user, visibility="internal",
                    internal_text=f"Letter {letter.reference} regenerated as {new.reference}: {reason.strip()}")
    return new


def letters_of_case(case):
    from django.db.models import Prefetch

    return list(OutcomeLetter.unscoped.filter(case=case).select_related("programme", "decision")
                .prefetch_related(Prefetch("copies", queryset=OutcomeLetterCopy.unscoped.order_by("kind")))
                .order_by("programme__name", "-version"))
