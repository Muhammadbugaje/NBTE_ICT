"""Visit invitations — staff actions (/qa/visit-invitations/), the portal
page for professional bodies and resource persons (/visit-invitations/) and
the tokenised confirm link from the e-mail (/invite/<token>/, no sign-in).

Every staff lookup goes through ``case_services.visible_cases`` (a case the
user cannot open answers 404); the actions themselves are limited to the desk
officer, the handling director and the ES (``visit_invitations.can_manage``).
"""
from __future__ import annotations

import datetime
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import case_services as cs
from . import visit_invitations as vi
from .visit_invitation_models import VisitInvitation


# ===========================================================================
# Staff (internal host)
# ===========================================================================
def _staff_invitation(request, pk):
    if not cs.can_view_cases(request.user):
        raise Http404
    inv = (VisitInvitation.unscoped.filter(pk=pk)
           .select_related("case", "case__institution", "body", "resource_person", "letter").first())
    if inv is None or not cs.visible_cases(request.user).filter(pk=inv.case_id).exists():
        raise Http404
    return inv


def _back(inv_or_case):
    case_id = getattr(inv_or_case, "case_id", None) or inv_or_case.pk
    return redirect(f"/qa/cases/{case_id}/?tab=visit#vin")


def _run(request, inv, func, ok_title):
    try:
        result = func()
    except (ValidationError, PermissionDenied) as exc:
        text = " ".join(getattr(exc, "messages", None) or [str(exc)])
        messages.error(request, text or "That did not work.")
        return _back(inv), None
    messages.success(request, ok_title)
    return _back(inv), result


@require_POST
def resend(request, pk):
    inv = _staff_invitation(request, pk)
    response, sent = _run(request, inv, lambda: vi.resend(inv, user=request.user), f"Invitation resent to {inv.name}")
    return response


@require_POST
def regenerate(request, pk):
    inv = _staff_invitation(request, pk)
    response, _doc = _run(request, inv, lambda: vi.regenerate(inv, user=request.user),
                          f"New letter generated for {inv.name}. Use Resend to send it.")
    return response


@require_POST
def contact(request, pk):
    inv = _staff_invitation(request, pk)
    p = request.POST
    response, _row = _run(request, inv, lambda: vi.record_contact(
        inv, user=request.user, channel=p.get("channel", ""), outcome=p.get("outcome", ""), note=p.get("note", ""),
        contact_name=p.get("contact_name", "")), f"Contact with {inv.name} recorded")
    return response


@require_POST
def acknowledge(request, pk):
    inv = _staff_invitation(request, pk)
    p = request.POST
    response, _row = _run(request, inv, lambda: vi.mark_acknowledged(
        inv, user=request.user, via=p.get("via", ""), name=p.get("contact_name", ""), note=p.get("note", "")),
        f"{inv.name} marked as confirmed")
    return response


@require_POST
def send_for_case(request, pk):
    """"Send invitations now": create any missing invitations for an approved visit."""
    if not cs.can_view_cases(request.user):
        raise Http404
    case = cs.visible_cases(request.user).filter(pk=pk).first()
    if case is None:
        raise Http404
    if not vi.can_manage(request.user, case):
        raise PermissionDenied("Only the desk officer, the handling director or the ES can do this.")
    result = vi.sync_for_case(case, actor=request.user)
    total = sum(result.values())
    if total:
        messages.success(request, f"Invitations: {result['created']} sent, {result['amended']} amended, "
                                  f"{result['withdrawn']} withdrawn.")
    else:
        messages.info(request, "Every invitee already has an up-to-date invitation.")
    return _back(case)


def _file_response(doc):
    if doc is None or not doc.file:
        raise Http404
    try:
        handle = doc.file.open("rb")
    except Exception:
        raise Http404 from None
    name = doc.original_name or f"{doc.reference.replace('/', '-')}.pdf"
    return FileResponse(handle, as_attachment=False, filename=name, content_type="application/pdf")


@require_GET
def letter(request, pk):
    inv = _staff_invitation(request, pk)
    return _file_response(inv.letter)


@require_GET
def letter_docx(request, pk):
    """Word copy of the letter; the PDF stays the official one. Restricted server-side (404 otherwise)."""
    inv = _staff_invitation(request, pk)
    if not vi.can_download_word(request.user, inv.case) or inv.letter_id is None:
        raise Http404
    try:
        data = vi.build_docx(inv)
    except ValueError:
        raise Http404 from None
    name = f"{inv.letter.reference.replace('/', '-')}.docx"
    resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


# ===========================================================================
# Portal (professional-body and resource-person accounts)
# ===========================================================================
def _portal_required(view):
    from qa_core.portal_views import portal_login_required

    @portal_login_required
    def wrapped(request, *args, **kwargs):
        if getattr(request.user, "user_type", "") not in ("professional_body", "resource_person"):
            raise Http404("This page is for professional bodies and resource persons.")
        return view(request, *args, **kwargs)
    wrapped.__name__ = view.__name__
    wrapped.__doc__ = view.__doc__
    return wrapped


def _portal_invitation(request, pk):
    inv = vi.portal_invitations(request.user).filter(pk=pk).first()
    if inv is None:
        raise Http404
    return inv


@_portal_required
@require_GET
def portal_list(request):
    from qa_core.portal_views import _shell_context

    rows = list(vi.portal_invitations(request.user))
    waiting = [r for r in rows if r.is_open]
    ctx = _shell_context(request, "visit_invitations", rows=rows, waiting=waiting,
                         is_body=getattr(request.user, "user_type", "") == "professional_body",
                         contexts={r.pk: vi.letter_context(r) for r in rows})
    for r in rows:
        r.ctx = ctx["contexts"][r.pk]
    return render(request, "qa_portal/visit_invitations/list.html", ctx)


@_portal_required
@require_POST
def portal_respond(request, pk):
    inv = _portal_invitation(request, pk)
    attending = request.POST.get("answer") != "decline"
    try:
        vi.acknowledge(inv, via="portal", by=request.user, note=request.POST.get("note", ""),
                       name=request.POST.get("name", ""), attending=attending)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("/visit-invitations/")
    messages.success(request, "Thank you: NBTE has your confirmation." if attending
                     else "Thank you: NBTE has been told you cannot attend.")
    return redirect("/visit-invitations/")


@_portal_required
@require_GET
def portal_letter(request, pk):
    return _file_response(_portal_invitation(request, pk).letter)


# ===========================================================================
# Tokenised link (no sign-in)
# ===========================================================================
def _visit_dates(inv):
    start = inv.visit_start or getattr(getattr(inv.case, "visit", None), "planned_start", None)
    end = inv.visit_end or getattr(getattr(inv.case, "visit", None), "planned_end", None) or start
    return start, end


def _calendar(inv):
    """Add-to-calendar links and the live countdown data for the invite page."""
    start, end = _visit_dates(inv)
    if not start:
        return None
    start_dt = timezone.make_aware(datetime.datetime.combine(start, datetime.time(9, 0)))
    end_dt = timezone.make_aware(datetime.datetime.combine(end, datetime.time(16, 0)))
    ctx = vi.letter_context(inv)
    title = f"NBTE QA visit — {ctx['institution']}"
    details = (f"QA visit to {ctx['institution']} ({ctx['code']}). "
               f"Programmes: {ctx['programmes']}. Team lead: {ctx['team_lead']}.")
    location = ctx["venue"]
    google = "https://calendar.google.com/calendar/render?" + urlencode({
        "action": "TEMPLATE", "text": title, "dates": f"{start:%Y%m%d}T090000/{end:%Y%m%d}T160000",
        "details": details, "location": location})
    return {"start": start.isoformat(), "end": end.isoformat(),
            "start_label": f"{start:%A, %d %B %Y}", "end_label": f"{end:%A, %d %B %Y}",
            "google": google, "ics": f"/invite/{inv.token}/calendar.ics",
            "start_ms": int(start_dt.timestamp() * 1000), "end_ms": int(end_dt.timestamp() * 1000),
            "same_day": start == end}


def _token_ctx(request, inv, **extra):
    from qa_core.hosts import portal_absolute
    from qa_core.portal_views import PORTAL_NAME, internal_url
    from urllib.parse import urlencode

    base = request.path if request.path.endswith("/") else request.path + "/"
    login_query = urlencode({"username": inv.email or "", "next": "/visit-invitations/"})
    password_query = urlencode({"email": inv.email or ""})
    ctx = {"inv": inv, "ctx": vi.letter_context(inv), "portal_name": PORTAL_NAME,
           "internal_login_url": internal_url(request, "/accounts/login/"),
           "portal_login_url": portal_absolute(f"/login/?{login_query}", request=request),
           "portal_password_url": portal_absolute(f"/password/forgot/?{password_query}", request=request),
           "invitee_email": inv.email or "",
           "calendar": _calendar(inv),
           # Relative on purpose: the same page answers on both hosts.
           "letter_url": f"{base}letter/", "calendar_url": f"{base}calendar.ics",
           "reminder_url": f"{base}reminder/",
           "is_body": inv.kind == VisitInvitation.BODY, "expired": inv.token_expired}
    ctx.update(extra)
    return ctx


@require_http_methods(["GET", "POST"])
def token_page(request, token):
    inv = vi.by_token(token)
    if inv is None:
        raise Http404
    error = ""
    done = ""
    if request.method == "POST":
        if inv.token_expired:
            error = "This link has expired. Please contact NBTE directly."
        elif inv.status == VisitInvitation.WITHDRAWN:
            error = "This invitation was withdrawn. NBTE no longer needs a reply."
        else:
            attending = request.POST.get("answer") != "decline"
            try:
                vi.respond_by_token(inv, attending=attending, name=request.POST.get("name", ""),
                                    note=request.POST.get("note", ""))
                done = "confirmed" if attending else "declined"
            except (ValidationError, PermissionDenied) as exc:
                error = " ".join(getattr(exc, "messages", None) or [str(exc)])
        inv.refresh_from_db()
    status = 422 if error else 200
    return render(request, "qa_portal/visit_invitations/token.html", _token_ctx(request, inv, error=error, done=done),
                  status=status)


@require_GET
def token_letter(request, token):
    inv = vi.by_token(token)
    if inv is None or inv.token_expired or inv.status == VisitInvitation.WITHDRAWN:
        raise Http404
    return _file_response(inv.letter)


@require_GET
def token_calendar(request, token):
    """The visit as a calendar file, from the invitation link."""
    inv = vi.by_token(token)
    if inv is None or inv.status == VisitInvitation.WITHDRAWN:
        raise Http404
    start, end = _visit_dates(inv)
    if not start:
        raise Http404
    ctx = vi.letter_context(inv)
    stamp = "%Y%m%dT%H%M%SZ"
    now = timezone.now().strftime(stamp)
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//NBTE//Quality Assurance//EN", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", "BEGIN:VEVENT",
        f"UID:visit-{inv.pk}-{inv.token[:12]}@nbte", f"DTSTAMP:{now}",
        f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{(end + datetime.timedelta(days=1)):%Y%m%d}",
        f"SUMMARY:NBTE QA visit — {ctx['institution']}",
        f"DESCRIPTION:QA visit to {ctx['institution']} ({ctx['code']}). Programmes: {ctx['programmes']}.",
        f"LOCATION:{ctx['venue']}",
        "BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY", "DESCRIPTION:NBTE QA visit tomorrow",
        "END:VALARM", "END:VEVENT", "END:VCALENDAR", "",
    ]
    body = "\r\n".join(lines).replace(",", "\\,").replace(";", "\\;")
    response = HttpResponse(body, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="nbte-visit-{inv.pk}.ics"'
    return response


@require_POST
def token_reminder(request, token):
    """The invitee asks for a reminder before the visit (no account needed)."""
    inv = vi.by_token(token)
    if inv is None or inv.token_expired or inv.status == VisitInvitation.WITHDRAWN:
        raise Http404
    VisitInvitation.unscoped.filter(pk=inv.pk).update(reminder_requested_at=timezone.now(),
                                                      updated_at=timezone.now())
    messages.success(request, "Reminder set: NBTE will remind you before the visit.")
    base = request.path[:-len("reminder/")] if request.path.endswith("reminder/") else request.path
    return redirect(f"{base}?reminder=1")
