"""My profile -> Signature (X23). Owner only; never while "acting as"."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from core import reauth

from . import signature as SIG


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _context(request, notice=None, error=None):
    user = request.user
    active, pending = SIG.active_for(user), SIG.pending_for(user)
    impersonating = bool(getattr(request, "is_impersonating", False))
    show_images = not impersonating
    return {
        "sig": {
            "active": active, "pending": pending, "inks": list(SIG.INKS),
            "drawn": bool(active and active.source_name == SIG.DRAWN),
            "demo": bool(active and (active.source_name or "").startswith("demo")),
            "active_uri": SIG.preview_uri(active) if (active and show_images) else "",
            "pending_uri": SIG.preview_uri(pending) if (pending and show_images) else "",
            "required": SIG.signature_required(user),
            "min_rank": SIG.min_rank(),
        },
        "impersonating": impersonating,
        "recent_auth": reauth.is_recent(request),
        "reauth_minutes": reauth.window_seconds() // 60,
        "notice": notice, "error": error,
    }


def _respond(request, notice=None, error=None, status=200):
    if _is_htmx(request):
        return render(request, "documents/_signature_panel.html", _context(request, notice, error), status=status)
    if error:
        messages.error(request, error)
    elif notice:
        messages.success(request, notice)
    return redirect(reverse("documents:my_signature"))


CHANGING = {"sig_upload", "sig_accept", "sig_draw", "sig_remove"}


@login_required
@require_http_methods(["GET", "POST"])
def my_signature(request):
    if getattr(request.user, "user_type", "staff") != "staff":
        raise Http404
    if request.method == "GET":
        template = "documents/_signature_panel.html" if _is_htmx(request) else "documents/my_signature.html"
        return render(request, template, _context(request))

    if getattr(request, "is_impersonating", False):
        return _respond(request, error="Only the account owner can change their signature, not while acting as "
                                       "them.", status=403)
    section = request.POST.get("section", "")
    user = request.user
    if section == "reauth":
        try:
            ok = reauth.confirm(request, request.POST.get("password", ""))
        except reauth.ReauthRequired as exc:
            return _respond(request, error=exc.message, status=429)
        if not ok:
            return _respond(request, error="That password is not right. Try again.", status=400)
        return _respond(request, notice="Thank you. You can change your signature for the next "
                                        f"{reauth.window_seconds() // 60} minutes.")
    if section in CHANGING and not reauth.is_recent(request):
        return _respond(request, error="Enter your password first to change your signature.", status=403)

    try:
        sens = max(0, min(100, int(request.POST.get("sensitivity") or 50)))
    except ValueError:
        sens = 50
    ink = request.POST.get("ink") if request.POST.get("ink") in SIG.INKS else "navy"
    pending = SIG.pending_for(user)
    try:
        if section == "sig_upload":
            SIG.start_from_photo(user, request.FILES.get("sig_photo"), user, sens, ink)
            return _respond(request, notice="Check the cleaned-up signature below. Adjust it if needed, then use it.")
        if section == "sig_adjust" and pending:
            SIG.adjust_pending(pending, sens, ink)
            return _respond(request)
        if section == "sig_accept" and pending:
            SIG.accept_pending(pending, user)
            return _respond(request, notice="Signature saved. It now appears on documents and memos your "
                                            "approvals generate.")
        if section == "sig_discard" and pending:
            SIG.discard_pending(pending)
            return _respond(request, notice="Discarded.")
        if section == "sig_draw":
            SIG.save_drawn(user, request.POST.get("sig_data") or "", user, ink)
            return _respond(request, notice="Signature saved. It now appears on documents and memos your "
                                            "approvals generate.")
        if section == "sig_remove":
            if SIG.retire(user, user):
                return _respond(request, notice="Your signature is no longer used. Documents you already signed "
                                                "keep it.")
            return _respond(request)
    except SIG.SignatureError as exc:
        return _respond(request, error=str(exc), status=400)
    return _respond(request, error="That signature action is not available.", status=400)


@login_required
def directors_without_signature(request):
    """Names-only report for HR / ICT (documents.view_signature_report)."""
    if not request.user.has_perm("documents.view_signature_report"):
        raise Http404
    return render(request, "documents/directors_without_signature.html",
                  {"people": SIG.directors_without_signature(), "min_rank": SIG.min_rank()})
