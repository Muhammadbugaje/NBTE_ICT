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


def render_pdf(letter, decision, settings_snapshot, *, duplicate=False):
    """The letter as PDF bytes (the duplicate carries the watermark)."""
    from payments.pdfs import GREEN, GREEN_DARK, INK, LINE, MUTED, _Doc, _wrap

    tpl, sig, mark = settings_snapshot["template"], settings_snapshot["signatory"], settings_snapshot["watermark"]
    data = _fields(letter, decision)
    doc = _Doc(f"Outcome letter {letter.reference}" + (" (duplicate)" if duplicate else ""),
               "NBTE outcome letter")
    if duplicate:
        doc.c.setKeywords("duplicate")
    c, m = doc.c, doc.margin
    width = doc.w - 2 * m
    if duplicate:
        _watermark(doc, mark)
    y = doc.header("Outcome letter", "Duplicate copy" if duplicate else "Original")
    doc.text(m, y, ADDRESS, size=8.5, color=MUTED)
    y -= 22
    doc.text(m, y, f"Ref: {letter.reference}", size=10, mono=True)
    doc.text(doc.w - m, y, data["date"], size=10, align="right")
    y -= 26
    doc.text(m, y, "The Chief Executive", size=10)
    y -= 14
    for line in _wrap(data["institution"], doc.f["bold"], 10.5, width):
        doc.text(m, y, line, size=10.5, bold=True)
        y -= 14
    y -= 12
    doc.text(m, y, str(tpl.get("salutation", "Dear Sir/Madam,")).format_map(data), size=10.5)
    y -= 22
    for line in _wrap(str(tpl.get("heading", "")).format_map(data), doc.f["bold"], 11, width):
        doc.text(doc.w / 2, y, line, size=11, bold=True, color=GREEN_DARK, align="center")
        y -= 15
    for line in _wrap(str(tpl.get("subject", "")).format_map(data), doc.f["bold"], 10.5, width):
        doc.text(doc.w / 2, y, line, size=10.5, bold=True, align="center")
        y -= 14
    y -= 10
    paragraphs = tpl.get("approve" if decision.outcome == "approve" else "deny") or []
    for para in paragraphs:
        for line in _wrap(str(para).format_map(data), doc.f["regular"], 10.5, width):
            doc.text(m, y, line, size=10.5, color=INK)
            y -= 14.5
        y -= 8
    weaknesses = [w for w in (decision.weaknesses or []) if str(w).strip()]
    if weaknesses:
        doc.text(m, y, str(tpl.get("weaknesses_intro", "")).format_map(data), size=10.5, bold=True)
        y -= 16
        for i, item in enumerate(weaknesses, start=1):
            lines = _wrap(str(item), doc.f["regular"], 10, width - 22)
            doc.text(m + 4, y, f"{i}.", size=10)
            for line in lines:
                doc.text(m + 22, y, line, size=10)
                y -= 13.5
            y -= 3
            if y < m + 160:
                doc.footer(letter.reference)
                c.showPage()
                if duplicate:
                    _watermark(doc, mark)
                y = doc.h - m - 20
        y -= 6
    for line in _wrap(str(tpl.get("closing", "")).format_map(data), doc.f["regular"], 10.5, width):
        doc.text(m, y, line, size=10.5)
        y -= 14
    y -= 46
    c.setStrokeColor(doc.color(LINE))
    c.setLineWidth(0.8)
    c.line(m, y + 12, m + 190, y + 12)
    doc.text(m, y, sig.get("name") or "", size=10.5, bold=True)
    doc.text(m, y - 14, sig.get("title") or "Executive Secretary", size=10, color=MUTED)
    if (sig.get("title") or "Executive Secretary").strip().lower() != "executive secretary":
        doc.text(m, y - 28, "For: Executive Secretary, NBTE", size=9, color=MUTED)
    doc.text(doc.w - m, m + 26, "DUPLICATE — not the original" if duplicate else "ORIGINAL", size=8.5, bold=True,
             color=GREEN if not duplicate else MUTED, align="right")
    doc.footer(letter.reference)
    return doc.finish()


def _watermark(doc, mark):
    """A large diagonal text across the page (duplicate only)."""
    c = doc.c
    c.saveState()
    try:
        opacity = float(mark.get("opacity", 0.12))
    except (TypeError, ValueError):
        opacity = 0.12
    c.setFillColor(doc.color("#9A1B1B"))
    try:
        c.setFillAlpha(max(0.03, min(opacity, 0.6)))
    except AttributeError:
        pass
    c.translate(doc.w / 2, doc.h / 2)
    try:
        c.rotate(float(mark.get("angle", 45)))
    except (TypeError, ValueError):
        c.rotate(45)
    text = str(mark.get("text") or "DUPLICATE")
    size = 64 if len(text) <= 14 else max(30, int(64 * 14 / len(text)))
    c.setFont("Helvetica-Bold", size)  # base-14 font: the watermark text stays searchable
    c.drawCentredString(0, 0, text)
    c.restoreState()


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
