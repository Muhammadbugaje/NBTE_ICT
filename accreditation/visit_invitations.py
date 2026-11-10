"""Visit invitations to professional bodies and external resource persons.

When the ES approves the visit (SOP-03 stage 10, ``es_approve_visit``) every
professional body mapped to the case's programmes and every external resource
person on the visit team gets:

* an invitation letter, issued through the documents registry (QR, short
  code) from the templates ``visit_invitation_body`` / ``visit_invitation_rp``;
* an e-mail with the letter attached and a tokenised confirm link (no sign-in;
  the link stops working after the last day of the visit);
* a notification on the professional-body / resource-person portal, whose
  Visit invitations page has Confirm / Decline buttons.

``VisitInvitation`` tracks each one (sent, failed, no e-mail on file,
confirmed, declined, withdrawn) with a contact log. The desk officer, the
handling director and the ES can resend, regenerate the letter, record a call
and mark an invitation confirmed. A decline alerts the desk officer and the
director so the person can be replaced.

Hooks
-----
* ``_on_case_stages_changed`` (``qa_core.flow.on_stages_changed``): leaving
  ``es_approve_visit`` with the visit approved runs :func:`sync_for_case`
  after the commit. A re-approval with new dates sends amended letters.
* :func:`send_reminders` (``manage.py qa_stage_reminders``): reminders at
  ``qa.visit.invite_reminder_days`` days before the visit (once per
  threshold), an alert to the desk officer / director listing who has not
  confirmed, and a date-change check.

Nothing here raises into the approval: a failed letter or e-mail is recorded
on the invitation and shown on the case page.
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import logging
import secrets

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from qa_core import flow, timeline

from .visit_invitation_models import VisitInvitation, VisitInvitationContact

logger = logging.getLogger(__name__)

TEMPLATE_BODY = "visit_invitation_body"
TEMPLATE_RP = "visit_invitation_rp"
EVENT_BODY = "qa_visit.invitation_body"
EVENT_RP = "qa_visit.invitation_rp"

DEFAULT_REMINDER_DAYS = [7, 2]
DEFAULT_BRING_BODY = ("your council's letter of nomination, a means of identification and your council's assessment "
                      "checklist for the programme(s)")
DEFAULT_BRING_RP = ("a means of identification, this letter, and any discipline checklist you use; the Team Lead "
                    "will brief the team on the first morning")

TEMPLATE_ROWS = [
    # code, title, audience, subject line, body
    (TEMPLATE_BODY, "Visit invitation – professional body", "internal",
     "Invitation to the NBTE QA visit: {institution}",
     "The National Board for Technical Education will conduct a quality-assurance visit to {institution} from "
     "{dates}. The visit covers the following programme(s), which fall under {invitee}: {programmes}.\n\n"
     "As this is a joint QA exercise, you are invited to nominate a representative to join the NBTE team. The "
     "team will be led by {team_lead}. The venue is {venue}.\n\n"
     "Your representative should bring {what_to_bring}.\n\n"
     "Please confirm receipt of this invitation, and whether your council will be represented, by {confirm_by}: "
     "open {confirm_url} (no sign-in needed), use Visit invitations on the NBTE professional-body portal, or "
     "contact {contact_officer} ({contact_details}).\n\n"
     "Please accept the assurances of the Board's highest regards."),
    (TEMPLATE_RP, "Visit invitation – resource person", "internal",
     "Invitation to serve on the NBTE QA visit: {institution}",
     "The National Board for Technical Education is pleased to invite you to serve as a resource person on its "
     "quality-assurance visit to {institution} from {dates}, for the following programme(s): {programmes}.\n\n"
     "The team will be led by {team_lead}. The venue is {venue}.\n\n"
     "Please bring {what_to_bring}.\n\n"
     "Please confirm your attendance by {confirm_by}: open {confirm_url} (no sign-in needed), use Visit "
     "invitations on the NBTE portal, or contact {contact_officer} ({contact_details}). If you cannot attend, "
     "please say so at once so that NBTE can arrange a replacement.\n\n"
     "Please accept the assurances of the Board's highest regards."),
]


# ===========================================================================
# Settings
# ===========================================================================
def _cfg(key, default):
    try:
        import config

        value = config.get(key, default)
    except Exception:
        return default
    return default if value is None else value


def enabled():
    return bool(_cfg("qa.visit.invite_enabled", True))


def reminder_days():
    raw = _cfg("qa.visit.invite_reminder_days", DEFAULT_REMINDER_DAYS)
    if isinstance(raw, str):
        raw = [p for p in raw.replace(";", ",").split(",") if p.strip()]
    out = set()
    for value in raw or []:
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n >= 0:
            out.add(n)
    return sorted(out, reverse=True)


def what_to_bring(kind):
    if kind == VisitInvitation.BODY:
        return str(_cfg("qa.visit.invite_bring_body", DEFAULT_BRING_BODY) or DEFAULT_BRING_BODY).strip()
    return str(_cfg("qa.visit.invite_bring_rp", DEFAULT_BRING_RP) or DEFAULT_BRING_RP).strip()


# ===========================================================================
# Document templates (created on first use and by the migration)
# ===========================================================================
def ensure_templates(apps=None):
    if apps is None:
        from documents.models import DocumentRule, DocumentTemplate
    else:
        DocumentTemplate = apps.get_model("documents", "DocumentTemplate")
        DocumentRule = apps.get_model("documents", "DocumentRule")
    made = 0
    for code, title, audience, subject_line, body in TEMPLATE_ROWS:
        if not DocumentTemplate.objects.filter(code=code).exists():
            DocumentTemplate.objects.create(code=code, version=1, title=title, kind="letter", audience=audience,
                                            letterhead="external_letter", signatory_rule="es",
                                            provisional_default=False, subject_line=subject_line, body=body)
            made += 1
    for event, code, text in ((EVENT_BODY, TEMPLATE_BODY, "Visit approved: invitation letter to each professional "
                                                          "body mapped to the case's programmes."),
                              (EVENT_RP, TEMPLATE_RP, "Visit approved: invitation letter to each external "
                                                      "resource person on the visit team.")):
        if not DocumentRule.objects.filter(event_code=event).exists():
            DocumentRule.objects.create(event_code=event, template_code=code, description=text, provisional=False,
                                        send_to_institution=False)
    return made


# ===========================================================================
# Who is invited
# ===========================================================================
def _is_external(rp):
    return rp is not None and rp.source != "nbte_staff" and not rp.staff_id


def invitees(case):
    """[{kind, body|resource_person, name, email, phone, programmes, portal_users}] for the case now."""
    from inspection.models import VISIT_DUTIES, QAMapping
    from institutions.professional_bodies import bodies_for_programme

    from .models import QARequestProgramme

    bodies, rps = {}, {}
    rows = (QARequestProgramme.unscoped.filter(qa_request=case.qa_request, status=QARequestProgramme.ACTIVE)
            .select_related("programme", "programme__level", "programme__master"))
    for row in rows:
        for body in bodies_for_programme(row.programme):
            entry = bodies.setdefault(body.pk, {"kind": VisitInvitation.BODY, "body": body, "programmes": [],
                                                "portal_users": []})
            if str(row.programme) not in entry["programmes"]:
                entry["programmes"].append(str(row.programme))

    def add_rp(rp, programme=""):
        if not _is_external(rp) or rp.status in ("suspended", "retired", "rejected"):
            return
        entry = rps.setdefault(rp.pk, {"kind": VisitInvitation.RESOURCE_PERSON, "resource_person": rp,
                                       "programmes": [], "portal_users": []})
        if programme and programme not in entry["programmes"]:
            entry["programmes"].append(programme)

    for mapping in (QAMapping.unscoped.filter(case=case, status__in=QAMapping.LIVE, duty__in=VISIT_DUTIES,
                                              resource_person__isnull=False)
                    .select_related("resource_person", "qa_request_programme__programme")):
        add_rp(mapping.resource_person, str(mapping.qa_request_programme.programme))
    # The visit team's external members (SOP-03 stage 3), when that model is present.
    try:
        from .models import VisitExternalMember
    except ImportError:  # pragma: no cover - older schema
        VisitExternalMember = None
    if VisitExternalMember is not None:
        for member in (VisitExternalMember.unscoped.filter(case=case).exclude(status="returned")
                       .select_related("resource_person", "body", "user")):
            if member.kind == VisitExternalMember.RESOURCE_PERSON and member.resource_person_id:
                add_rp(member.resource_person)
            elif member.body_id and member.body.is_active:
                entry = bodies.setdefault(member.body_id, {"kind": VisitInvitation.BODY, "body": member.body,
                                                           "programmes": [], "portal_users": []})
                if member.user_id and member.user.is_active:
                    entry["portal_users"].append(member.user)
    out = []
    for entry in sorted(bodies.values(), key=lambda e: e["body"].name):
        body = entry["body"]
        entry.update(name=body.name, email=body.email or body.registrar_email or "",
                     phone=body.phone or body.registrar_phone or "")
        out.append(entry)
    for entry in sorted(rps.values(), key=lambda e: e["resource_person"].full_name):
        rp = entry["resource_person"]
        if not entry["programmes"]:
            entry["programmes"] = sorted({str(r.programme) for r in rows})
        entry.update(name=rp.full_name, email=rp.email or "", phone=rp.phone or "")
        out.append(entry)
    return out


def _portal_users(inv, extra=()):
    users = list(extra)
    if inv.kind == VisitInvitation.BODY and inv.body_id:
        if inv.body.portal_enabled and _cfg("qa.pb.portal_enabled", True):
            from institutions.pb_portal import body_users

            users += body_users(inv.body)
    elif inv.resource_person_id and inv.resource_person.user_id and inv.resource_person.user.is_active:
        users.append(inv.resource_person.user)
    seen, out = set(), []
    for u in users:
        if u is not None and u.pk not in seen:
            seen.add(u.pk)
            out.append(u)
    return out


# ===========================================================================
# Letter and e-mail content
# ===========================================================================
def _fmt(d):
    return d.strftime("%d %B %Y").lstrip("0") if d else ""


def _dates(start, end):
    if not start:
        return "dates to be confirmed"
    if not end or end == start:
        return _fmt(start)
    return f"{_fmt(start)} to {_fmt(end)}"


def _person(user):
    if user is None:
        return {"name": "", "email": "", "phone": ""}
    staff = getattr(user, "staff_profile", None) if getattr(user, "pk", None) else None
    name = (user.get_full_name() if hasattr(user, "get_full_name") else "") or str(user)
    return {"name": name, "email": getattr(user, "email", "") or getattr(staff, "email", ""),
            "phone": getattr(staff, "phone", "") or ""}


def contact_officer(case):
    officer = case.desk_officer
    if officer is None:
        from .case_services import _director_of

        directors = _director_of(case.owning_department)
        officer = directors[0] if directors else None
    return _person(officer)


def venue(case):
    inst = case.institution
    try:
        address = inst.address("permanent_site") or inst.address()
    except Exception:
        address = None
    line = address.one_line if address is not None else ""
    return f"{inst.name}, {line}" if line else (f"{inst.name}, {inst.state_name} State" if inst.state_name
                                                 else inst.name)


def confirm_url(inv):
    from qa_core.hosts import portal_base_url

    return f"{portal_base_url()}/invite/{inv.token}/"


def portal_login_url(inv):
    from urllib.parse import urlencode

    from qa_core.hosts import portal_base_url

    query = urlencode({"username": inv.email or "", "next": "/visit-invitations/"})
    return f"{portal_base_url()}/login/?{query}"


def portal_password_url(inv):
    from urllib.parse import urlencode

    from qa_core.hosts import portal_base_url

    query = urlencode({"email": inv.email or ""})
    return f"{portal_base_url()}/password/forgot/?{query}"


def confirm_by(inv):
    start = inv.visit_start
    today = timezone.localdate()
    if not start:
        return _fmt(today + datetime.timedelta(days=7))
    target = start - datetime.timedelta(days=3)
    return _fmt(max(target, min(today + datetime.timedelta(days=1), start)))


def letter_context(inv):
    case = inv.case
    visit = getattr(case, "visit", None)
    lead = getattr(visit, "team_lead", None) if visit is not None else None
    officer = contact_officer(case)
    details = ", ".join(p for p in (officer["email"], officer["phone"]) if p) or "NBTE Quality Assurance"
    lead_name = str(lead) if lead is not None else "an NBTE director"
    return {
        "invitee": inv.name, "institution": case.institution.name, "code": case.institution.code,
        "programmes": "; ".join(inv.programmes) or "the programmes under review",
        "dates": _dates(inv.visit_start, inv.visit_end), "venue": venue(case), "team_lead": lead_name,
        "contact_officer": officer["name"] or "the NBTE desk officer", "contact_email": officer["email"],
        "contact_phone": officer["phone"], "contact_details": details,
        "what_to_bring": what_to_bring(inv.kind), "confirm_url": confirm_url(inv), "confirm_by": confirm_by(inv),
        "portal_login_url": portal_login_url(inv), "portal_password_url": portal_password_url(inv),
        "case_reference": case.reference,
    }


class _Blank(dict):
    def __missing__(self, key):
        return f"[{key.upper()}]"


def _paragraphs(tpl, ctx, amended):
    body = tpl.body.format_map(_Blank(ctx)) if tpl.body else ""
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    if getattr(tpl, "code", "") == TEMPLATE_RP:
        paras.append(
            "Resource-person portal: sign in with your registered e-mail at "
            f"{ctx['portal_login_url']}. On your first visit, or if you forgot your password, request a secure "
            f"password link at {ctx['portal_password_url']}. The link is sent only to the registered address."
        )
    if amended:
        paras.insert(0, "This letter replaces our earlier invitation: the visit details have changed. Please note "
                        "the dates below and confirm again.")
    return paras


def _addressee(inv):
    if inv.kind == VisitInvitation.BODY and inv.body_id:
        body = inv.body
        lines = [f"The Registrar / {body.contact_person}," if body.contact_person else "The Registrar,",
                 f"{body.name},"]
        lines += [ln.strip() for ln in (body.address or "").splitlines() if ln.strip()][:3]
        return lines
    rp = inv.resource_person
    lines = [f"{rp.rank} {rp.full_name},".strip() if rp.rank else f"{rp.full_name},"]
    if rp.home_institution_id:
        lines.append(f"{rp.home_institution.name}.")
    return lines


def issue_letter(inv, *, actor=None, amended=False):
    """Issue (and render) the invitation letter; supersedes the previous one."""
    from documents.issue import issue
    from documents.models import DocumentTemplate, IssuedDocument

    ensure_templates()
    code = TEMPLATE_BODY if inv.kind == VisitInvitation.BODY else TEMPLATE_RP
    tpl = DocumentTemplate.current(code)
    if tpl is None:
        raise ValueError(f"The document template '{code}' is switched off.")
    ctx = letter_context(inv)
    title = tpl.subject_line.format_map(_Blank(ctx)) if tpl.subject_line else tpl.title
    if amended:
        title = f"Amended: {title}"
    facts = [("Institution", ctx["institution"]), ("Programme(s)", ctx["programmes"]), ("Dates", ctx["dates"]),
             ("Venue", ctx["venue"]), ("Team lead", ctx["team_lead"]),
             ("Contact officer", f"{ctx['contact_officer']} · {ctx['contact_details']}"),
             ("Please confirm by", ctx["confirm_by"]), ("Case", ctx["case_reference"])]
    previous = inv.letter
    doc = issue(code, inv, actor=None, institution=inv.case.institution, context=ctx, provisional=False,
                title=title, facts=facts, paragraphs=_paragraphs(tpl, ctx, amended), addressee=_addressee(inv),
                event_code=EVENT_BODY if inv.kind == VisitInvitation.BODY else EVENT_RP,
                extra_payload={"invitation": inv.pk, "invitee": inv.name, "amended": bool(amended)})
    if getattr(actor, "pk", None):
        IssuedDocument.unscoped.filter(pk=doc.pk).update(issued_by=actor)
    if previous is not None and previous.pk != doc.pk:
        IssuedDocument.unscoped.filter(pk=previous.pk, status=IssuedDocument.VALID).update(
            status=IssuedDocument.SUPERSEDED, superseded_by=doc, updated_at=timezone.now())
    inv.letter = doc
    inv.letter_version = (inv.letter_version or 0) + 1
    inv.save(update_fields=["letter", "letter_version", "updated_at"])
    return doc


def can_download_word(user, case):
    """The editable Word copy: the handling director of the case's owning department, the ES and super
    admins only. Desk officers, other staff, invitees and the portal get the PDF only."""
    if user is None or not getattr(user, "is_authenticated", False) or getattr(user, "user_type", "") != "staff":
        return False
    if user.is_superuser:
        return True
    from .case_services import _director_of

    if any(d.pk == user.pk for d in _director_of(case.owning_department)):
        return True
    try:
        import org

        return bool(org.is_es(user))
    except Exception:
        return False


def build_docx(inv):
    """The invitation letter as a Word file (bytes), from the same template text as the PDF."""
    import io

    from docx import Document
    from docx.shared import Pt

    from documents.models import DocumentTemplate

    code = TEMPLATE_BODY if inv.kind == VisitInvitation.BODY else TEMPLATE_RP
    tpl = DocumentTemplate.current(code)
    if tpl is None:
        raise ValueError(f"The document template '{code}' is switched off.")
    ctx = letter_context(inv)
    amended = bool((inv.letter_version or 0) > 1)
    title = tpl.subject_line.format_map(_Blank(ctx)) if tpl.subject_line else tpl.title
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11)
    doc.add_paragraph(f"{inv.letter.reference}" if inv.letter_id else "")
    for line in _addressee(inv):
        doc.add_paragraph(line).paragraph_format.space_after = Pt(0)
    doc.add_heading(title, level=2)
    for para in _paragraphs(tpl, ctx, amended):
        doc.add_paragraph(para)
    rows = [("Institution", ctx["institution"]), ("Programme(s)", ctx["programmes"]), ("Dates", ctx["dates"]),
            ("Venue", ctx["venue"]), ("Team lead", ctx["team_lead"]),
            ("Contact officer", f"{ctx['contact_officer']} · {ctx['contact_details']}"),
            ("Please confirm by", ctx["confirm_by"]), ("Case", ctx["case_reference"])]
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    for label, value in rows:
        cells = table.add_row().cells
        cells[0].text, cells[1].text = str(label), str(value)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _letter_attachment(inv):
    doc = inv.letter
    if doc is None or not doc.file:
        return []
    try:
        with doc.file.open("rb") as fh:
            content = fh.read()
    except Exception:
        logger.exception("Could not read invitation letter %s", doc.pk)
        return []
    name = doc.original_name or f"{doc.reference.replace('/', '-')}.pdf"
    return [(name, content, "application/pdf")]


# ===========================================================================
# Sending
# ===========================================================================
def _log(inv, outcome, *, by=None, note="", channel="system", contact_name="", at=None):
    return VisitInvitationContact.unscoped.create(invitation=inv, channel=channel, outcome=outcome, note=note[:2000],
                                                  by=by if getattr(by, "pk", None) else None,
                                                  contact_name=contact_name[:150], at=at or timezone.now())


def _token_expiry(visit_end):
    if not visit_end:
        return None
    day_after = visit_end + datetime.timedelta(days=1)
    return timezone.make_aware(datetime.datetime.combine(day_after, datetime.time.min))


def _new_token():
    for _ in range(10):
        token = secrets.token_urlsafe(32)
        if not VisitInvitation.unscoped.filter(token=token).exists():
            return token
    raise RuntimeError("Could not create a unique invitation token.")  # pragma: no cover


def deliver(inv, *, actor=None, kind="sent", extra_portal_users=()):
    """E-mail (letter attached) + portal notification for the current letter.
    ``kind``: sent | resent | amended | reminder. Updates status; never raises."""
    from qa_core import mail

    now = timezone.now()
    ctx = letter_context(inv)
    subject = {
        "sent": f"NBTE QA visit invitation: {inv.case.institution.name}",
        "resent": f"NBTE QA visit invitation (copy): {inv.case.institution.name}",
        "amended": f"Amended NBTE QA visit invitation: {inv.case.institution.name}",
        "reminder": f"Reminder: please confirm the NBTE QA visit to {inv.case.institution.name}",
    }[kind]
    portal_users = _portal_users(inv, extra_portal_users)
    if portal_users:
        try:
            from notifications.models import Notification
            from notifications.services import notify_many

            title = {"sent": "Visit invitation: please confirm", "resent": "Visit invitation: please confirm",
                     "amended": "Visit dates changed: please confirm again",
                     "reminder": "Reminder: please confirm the visit"}[kind]
            made = notify_many(portal_users, title=f"{title} ({inv.case.institution.name})",
                               body=f"NBTE QA visit to {inv.case.institution.name}, {ctx['dates']}. Open the "
                                    f"invitation to read the letter and confirm.",
                               url="/visit-invitations/", category=Notification.QA)
            if made:
                inv.portal_notified_at, inv.portal_recipient_count = now, len(made)
        except Exception:
            logger.exception("Portal notice for invitation %s failed", inv.pk)
    status_before = inv.status
    error = ""
    if inv.email:
        try:
            mail.send(subject, [inv.email], "emails/visit_invitation.html",
                      {"inv": inv, "ctx": ctx, "kind": kind, "letter": inv.letter,
                       "is_body": inv.kind == VisitInvitation.BODY},
                      category="qa", attachments=_letter_attachment(inv), portal_notification=False)
            sent = True
        except Exception as exc:
            logger.exception("Invitation e-mail %s failed", inv.pk)
            sent, error = False, str(exc)[:500] or exc.__class__.__name__
    else:
        sent = False
    if status_before in (VisitInvitation.ACKNOWLEDGED, VisitInvitation.DECLINED):
        new_status = status_before  # a copy or reminder never undoes an answer
    elif sent:
        new_status = VisitInvitation.SENT
    elif kind == "reminder" and status_before == VisitInvitation.SENT:
        new_status = status_before  # the letter itself did go out
    else:
        new_status = VisitInvitation.FAILED if inv.email else VisitInvitation.NO_CONTACT
    inv.status = new_status
    inv.last_error = error if not sent and inv.email else ("" if sent else "No e-mail address on file.")
    if sent:
        inv.sent_at = now
        inv.send_count = (inv.send_count or 0) + 1
    inv.save(update_fields=["status", "last_error", "sent_at", "send_count", "portal_notified_at",
                            "portal_recipient_count", "updated_at"])
    where = []
    if sent:
        where.append(f"e-mailed to {inv.email}")
    elif inv.email:
        where.append(f"e-mail to {inv.email} failed")
    else:
        where.append("no e-mail on file")
    if portal_users:
        where.append(f"portal notice to {len(portal_users)} account{'s' if len(portal_users) != 1 else ''}")
    _log(inv, kind if (sent or portal_users) else "failed", by=actor, note="; ".join(where).capitalize() + ".")
    return sent


def _passed_thresholds(start, today=None):
    """Reminder thresholds already reached when the letter goes out (the letter
    itself is the reminder for those)."""
    if not start:
        return []
    days_left = (start - (today or timezone.localdate())).days
    return [t for t in reminder_days() if days_left <= t]


def _visit_ready(visit):
    return visit is not None and visit.status == "es_approved" and visit.planned_start is not None


def sync_for_case(case, *, actor=None):
    """Create / amend / withdraw the invitations so they match the approved
    visit. Returns {"created": n, "amended": n, "withdrawn": n}."""
    from .models import VisitDetail

    out = {"created": 0, "amended": 0, "withdrawn": 0}
    if not enabled():
        return out
    visit = VisitDetail.unscoped.filter(case=case).select_related("team_lead").first()
    if not _visit_ready(visit):
        return out
    current = invitees(case)
    keys = set()
    for entry in current:
        is_body = entry["kind"] == VisitInvitation.BODY
        lookup = {"body": entry["body"]} if is_body else {"resource_person": entry["resource_person"]}
        keys.add(("b", entry["body"].pk) if is_body else ("r", entry["resource_person"].pk))
        try:
            if (not is_body and entry["resource_person"].user_id is None
                    and entry["resource_person"].status == "active"):
                from inspection.services import invite_to_portal

                invite_to_portal(entry["resource_person"], actor=actor)
            with transaction.atomic():
                inv = VisitInvitation.unscoped.filter(case=case, **lookup).first()
                if inv is None:
                    inv = VisitInvitation.unscoped.create(
                        case=case, visit=visit, kind=entry["kind"], name=entry["name"][:200], **lookup,
                        email=entry["email"], phone=entry["phone"][:60], programmes=entry["programmes"],
                        visit_start=visit.planned_start, visit_end=visit.planned_end, token=_new_token(),
                        token_expires_at=_token_expiry(visit.planned_end),
                        reminders_sent=_passed_thresholds(visit.planned_start))
                    _issue_safely(inv, actor=actor)
                    deliver(inv, actor=actor, kind="sent", extra_portal_users=entry["portal_users"])
                    out["created"] += 1
                    continue
                dates_changed = (inv.visit_start, inv.visit_end) != (visit.planned_start, visit.planned_end)
                reopened = inv.status == VisitInvitation.WITHDRAWN
                if not (dates_changed or reopened):
                    continue
                inv.visit, inv.name = visit, entry["name"][:200]
                inv.email, inv.phone, inv.programmes = entry["email"], entry["phone"][:60], entry["programmes"]
                inv.visit_start, inv.visit_end = visit.planned_start, visit.planned_end
                inv.token_expires_at = _token_expiry(visit.planned_end)
                inv.reminders_sent = _passed_thresholds(visit.planned_start)
                inv.withdrawn_at = None
                if inv.status != VisitInvitation.DECLINED or dates_changed:
                    inv.status = VisitInvitation.QUEUED
                    inv.responded_at, inv.responded_via, inv.responded_by = None, "", None
                inv.save()
                _issue_safely(inv, actor=actor, amended=True)
                deliver(inv, actor=actor, kind="amended" if dates_changed else "sent",
                        extra_portal_users=entry["portal_users"])
                out["amended" if dates_changed else "created"] += 1
        except Exception:
            logger.exception("Visit invitation for %s on case %s failed", entry.get("name"), case.pk)
    for inv in VisitInvitation.unscoped.filter(case=case).exclude(status=VisitInvitation.WITHDRAWN):
        key = ("b", inv.body_id) if inv.body_id else ("r", inv.resource_person_id)
        if key not in keys:
            VisitInvitation.unscoped.filter(pk=inv.pk).update(status=VisitInvitation.WITHDRAWN,
                                                              withdrawn_at=timezone.now(), updated_at=timezone.now())
            _log(inv, "withdrawn", by=actor, note="No longer on the visit (programme or team changed).")
            out["withdrawn"] += 1
    if any(out.values()):
        parts = [f"{n} {label}" for label, n in (("invited", out["created"]), ("amended", out["amended"]),
                                                 ("withdrawn", out["withdrawn"])) if n]
        timeline.record(case.institution, "qa_case.visit_invitations", subject=case, actor=actor,
                        visibility="internal", internal_text=f"Visit invitations: {', '.join(parts)}.")
    return out


def _issue_safely(inv, *, actor=None, amended=False):
    try:
        return issue_letter(inv, actor=actor, amended=amended)
    except Exception as exc:
        logger.exception("Invitation letter for %s failed", inv.pk)
        VisitInvitation.unscoped.filter(pk=inv.pk).update(last_error=f"Letter could not be generated: {exc}"[:500])
        return None


# ===========================================================================
# The approval hook
# ===========================================================================
def _on_case_stages_changed(instance, entered, left, transition, actor, remark, subject, automatic):
    from .models import QACase

    if not isinstance(subject, QACase) or "es_approve_visit" not in left:
        return
    if getattr(getattr(transition, "from_stage", None), "code", "") != "es_approve_visit":
        return
    if getattr(transition, "action_code", "") == "return":
        return
    case_pk = subject.pk
    actor_pk = getattr(actor, "pk", None)

    def run():
        from django.contrib.auth import get_user_model

        case = QACase.unscoped.filter(pk=case_pk).first()
        if case is None:
            return
        user = get_user_model().objects.filter(pk=actor_pk).first() if actor_pk else None
        try:
            sync_for_case(case, actor=user)
        except Exception:
            logger.exception("Visit invitations failed for case %s", case_pk)
    transaction.on_commit(run)


flow.on_stages_changed(_on_case_stages_changed)


# ===========================================================================
# Responses (portal, link, NBTE on the invitee's behalf)
# ===========================================================================
def _alert_staff(case, title, body, *, level="warning"):
    """In-app (and e-mail) alert to the desk officer and the handling director."""
    from notifications.models import Notification
    from notifications.services import notify

    from .case_services import _director_of

    users = [u for u in [case.desk_officer, *_director_of(case.owning_department)] if u is not None]
    seen = set()
    for user in users:
        if user.pk in seen:
            continue
        seen.add(user.pk)
        notify(user, title=title, body=body, url=f"/qa/cases/{case.pk}/?tab=visit", level=level,
               category=Notification.QA)
    return list(seen)


def acknowledge(inv, *, via, by=None, name="", note="", attending=True):
    """Confirm (``attending``) or decline the invitation."""
    if inv.status == VisitInvitation.WITHDRAWN:
        raise ValidationError("This invitation was withdrawn.")
    note = (note or "").strip()
    if not attending and len(note) < 3 and via in ("portal", "link"):
        raise ValidationError("Please say briefly why you cannot attend, so NBTE can arrange a replacement.")
    now = timezone.now()
    inv.status = VisitInvitation.ACKNOWLEDGED if attending else VisitInvitation.DECLINED
    inv.responded_at, inv.responded_via = now, via
    inv.responded_by = by if getattr(by, "pk", None) else None
    inv.responder_name = (name or (by.get_full_name() if getattr(by, "pk", None) else ""))[:150]
    inv.response_note = note[:2000]
    inv.save(update_fields=["status", "responded_at", "responded_via", "responded_by", "responder_name",
                            "response_note", "updated_at"])
    case = inv.case
    what = "confirmed" if attending else "declined"
    timeline.record(case.institution, f"qa_case.visit_invitation_{what}", subject=case, actor=by,
                    visibility="internal",
                    internal_text=f"Visit invitation {what} by {inv.name} ({inv.get_responded_via_display()})"
                                  + (f": {note}" if note else "."))
    if not attending:
        kind = "professional body" if inv.kind == VisitInvitation.BODY else "resource person"
        _alert_staff(case, f"Visit invitation declined: {inv.name}",
                     f"{inv.name} ({kind}) cannot attend the visit to {case.institution.name}"
                     f"{': ' + note if note else '.'} Arrange a replacement or contact them.")
    return inv


def by_token(token):
    if not token or len(token) < 20:
        return None
    return (VisitInvitation.unscoped.filter(token=token)
            .select_related("case", "case__institution", "body", "resource_person", "letter").first())


def respond_by_token(inv, *, attending, name="", note=""):
    if inv.token_expired:
        raise PermissionDenied("This link has expired.")
    return acknowledge(inv, via="link", name=name, note=note, attending=attending)


def portal_invitations(user):
    """Invitations a portal user may answer: their body's, or their own as a resource person."""
    qs = VisitInvitation.unscoped.exclude(status=VisitInvitation.WITHDRAWN).select_related(
        "case", "case__institution", "body", "resource_person", "letter")
    if getattr(user, "user_type", "") == "professional_body" and getattr(user, "professional_body_id", None):
        return qs.filter(body_id=user.professional_body_id).order_by("-visit_start", "-pk")
    from inspection.services import rp_of

    rp = rp_of(user)
    if rp is not None:
        return qs.filter(resource_person=rp).order_by("-visit_start", "-pk")
    return qs.none()


# ===========================================================================
# Staff actions (desk officer, handling director, ES)
# ===========================================================================
def can_manage(user, case):
    if user is None or not getattr(user, "is_authenticated", False) or getattr(user, "user_type", "") != "staff":
        return False
    if user.is_superuser or (case.desk_officer_id and case.desk_officer_id == user.pk):
        return True
    from .case_services import _director_of, is_monitor

    if is_monitor(user):
        return False
    if any(d.pk == user.pk for d in _director_of(case.owning_department)):
        return True
    try:
        import org

        return bool(org.is_es(user))
    except Exception:
        return False


def _require(user, inv):
    if not can_manage(user, inv.case):
        raise PermissionDenied("Only the desk officer, the handling director or the ES can do this.")


def resend(inv, *, user):
    _require(user, inv)
    if inv.status == VisitInvitation.WITHDRAWN:
        raise ValidationError("This invitation was withdrawn.")
    if inv.letter is None:
        _issue_safely(inv, actor=user)
        inv.refresh_from_db()
    if not inv.email and not _portal_users(inv):
        raise ValidationError(f"{inv.name} has no e-mail or portal account on file. Record a call instead.")
    return deliver(inv, actor=user, kind="resent")


def regenerate(inv, *, user):
    _require(user, inv)
    if inv.status == VisitInvitation.WITHDRAWN:
        raise ValidationError("This invitation was withdrawn.")
    entry = next((e for e in invitees(inv.case) if (e.get("body") and e["body"].pk == inv.body_id)
                  or (e.get("resource_person") and e["resource_person"].pk == inv.resource_person_id)), None)
    if entry is not None:
        inv.name, inv.email, inv.phone = entry["name"][:200], entry["email"], entry["phone"][:60]
        inv.programmes = entry["programmes"]
        inv.save(update_fields=["name", "email", "phone", "programmes", "updated_at"])
    doc = issue_letter(inv, actor=user, amended=False)
    _log(inv, "regenerated", by=user, note=f"New letter {doc.reference}. Use Resend to send it.")
    return doc


def record_contact(inv, *, user, channel, outcome, note="", contact_name=""):
    _require(user, inv)
    if channel not in VisitInvitationContact.MANUAL_CHANNELS:
        raise ValidationError("Choose how you contacted them.")
    if outcome not in VisitInvitationContact.MANUAL_OUTCOMES:
        raise ValidationError("Choose the outcome.")
    note = (note or "").strip()
    if outcome in ("declined", "other") and len(note) < 3:
        raise ValidationError("Add a short note (what they said).")
    entry = _log(inv, outcome, by=user, note=note, channel=channel, contact_name=contact_name)
    via = {"phone": "phone", "email": "email", "sms": "other", "in_person": "in_person", "letter": "letter"}[channel]
    if outcome == "confirmed":
        acknowledge(inv, via=via, by=user, name=contact_name, note=note, attending=True)
    elif outcome == "declined":
        acknowledge(inv, via=via, by=user, name=contact_name, note=note, attending=False)
    return entry


def mark_acknowledged(inv, *, user, via, name="", note=""):
    _require(user, inv)
    valid = {v for v, _l in VisitInvitation.VIA_CHOICES} - {"portal", "link"}
    if via not in valid:
        raise ValidationError("Say how they confirmed (phone, e-mail, in person …).")
    channel = {"phone": "phone", "email": "email", "in_person": "in_person", "letter": "letter"}.get(via, "sms")
    _log(inv, "confirmed", by=user, note=note or "Marked confirmed by NBTE.", channel=channel, contact_name=name)
    return acknowledge(inv, via=via, by=user, name=name, note=note, attending=True)


# ===========================================================================
# Reminders (qa_stage_reminders)
# ===========================================================================
def send_reminders(today=None):
    """Remind unanswered invitees at each threshold (once), alert the staff
    once per case and threshold, and re-sync visits whose dates changed.
    Returns [(invitation_pk, "reminder"|"alert"|"amended")]."""
    from .models import VisitDetail

    out = []
    if not enabled():
        return out
    today = today or timezone.localdate()
    thresholds = reminder_days()
    visits = (VisitDetail.unscoped.filter(status="es_approved", planned_start__isnull=False,
                                          planned_end__gte=today).select_related("case", "case__institution"))
    for visit in visits:
        case = visit.case
        invitations = list(VisitInvitation.unscoped.filter(case=case).exclude(status=VisitInvitation.WITHDRAWN))
        if any((i.visit_start, i.visit_end) != (visit.planned_start, visit.planned_end) for i in invitations):
            result = sync_for_case(case)
            if result["amended"]:
                out.append((case.pk, "amended"))
            continue
        if not thresholds or visit.planned_start < today:
            continue
        days_left = (visit.planned_start - today).days
        due = [t for t in thresholds if days_left <= t]
        if not due:
            continue
        newest = min(due)  # the closest threshold reached
        reminded = []
        for inv in invitations:
            done = set(inv.reminders_sent or [])
            if newest in done:
                continue
            inv.reminders_sent = sorted(done | set(due), reverse=True)
            inv.save(update_fields=["reminders_sent", "updated_at"])
            if inv.status in VisitInvitation.OPEN:
                try:
                    deliver(inv, kind="reminder")
                except Exception:
                    logger.exception("Reminder for invitation %s failed", inv.pk)
                reminded.append(inv)
                out.append((inv.pk, "reminder"))
        if reminded:
            names = ", ".join(i.name for i in reminded[:6]) + (" …" if len(reminded) > 6 else "")
            total = len(invitations)
            _alert_staff(case, f"{len(reminded)} of {total} visit invitee{'s' if total != 1 else ''} not confirmed",
                         f"The visit to {case.institution.name} starts on {_fmt(visit.planned_start)} "
                         f"({days_left} day{'s' if days_left != 1 else ''}). Not yet confirmed: {names}. "
                         "They were reminded; consider calling them.")
            out.append((case.pk, "alert"))
    return out


# ===========================================================================
# Read helpers for the case page
# ===========================================================================
def summary(invitations):
    rows = [i for i in invitations if i.status != VisitInvitation.WITHDRAWN]
    confirmed = sum(1 for i in rows if i.status == VisitInvitation.ACKNOWLEDGED)
    declined = sum(1 for i in rows if i.status == VisitInvitation.DECLINED)
    problems = sum(1 for i in rows if i.status in (VisitInvitation.FAILED, VisitInvitation.NO_CONTACT))
    return {"total": len(rows), "confirmed": confirmed, "declined": declined, "problems": problems,
            "waiting": len(rows) - confirmed - declined}


def for_case(case):
    return list(VisitInvitation.unscoped.filter(case=case)
                .select_related("body", "resource_person", "letter", "responded_by").order_by("kind", "name"))
