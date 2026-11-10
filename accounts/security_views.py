"""Phase 9 account-security views for the INTERNAL host, plus the shared
request-level helpers the QA portal's own views (qa_core/portal_account_views.py)
call. Business logic is in accounts/sessions_service.py, accounts/passkeys.py,
accounts/deletion.py and accounts/staff_id.py.
"""
from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from core import ratelimit

from . import deletion, passkeys, sessions_service, staff_id

AUTH_BACKEND = "accounts.backends.RolePermissionBackend"
PASSKEY_FAILURES = (10, 15 * 60)
VERIFY_LIMIT = (30, 60)  # 30 look-ups a minute per address


# ---------------------------------------------------------------------------
# Shared helpers (both doors)
# ---------------------------------------------------------------------------
def is_impersonating(request):
    return bool(getattr(request, "is_impersonating", False) or getattr(request, "impersonator", None))


IMPERSONATION_MESSAGE = "Not available while you are viewing as another user."


INTERNAL_URLS = {
    "device_revoke": "accounts:device_revoke", "devices_revoke_others": "accounts:devices_revoke_others",
    "deletion_request": "accounts:deletion_request", "deletion_withdraw": "accounts:deletion_withdraw",
    "passkey_register_begin": "accounts:passkey_register_begin",
    "passkey_register_finish": "accounts:passkey_register_finish",
    "passkey_rename": "accounts:passkey_rename", "passkey_remove": "accounts:passkey_remove",
}
PORTAL_URLS = {
    "device_revoke": "qa_portal:account_device_revoke",
    "devices_revoke_others": "qa_portal:account_devices_revoke_others",
    "deletion_request": "qa_portal:account_deletion_request",
    "deletion_withdraw": "qa_portal:account_deletion_withdraw",
    "passkey_register_begin": "qa_portal:account_passkey_register_begin",
    "passkey_register_finish": "qa_portal:account_passkey_register_finish",
    "passkey_rename": "qa_portal:account_passkey_rename", "passkey_remove": "qa_portal:account_passkey_remove",
}


def security_context(request):
    """Devices, passkeys and the deletion request for the signed-in user."""
    user = request.user
    portal = bool(getattr(request, "is_qa_portal", False))
    return {
        "p9_door": "portal" if portal else "internal",
        "p9_urls": PORTAL_URLS if portal else INTERNAL_URLS,
        "p9_devices": sessions_service.list_sessions(user, request.session.session_key),
        "p9_passkeys": passkeys.list_passkeys(user),
        "p9_deletion": deletion.latest_request(user),
        "p9_deletion_open": deletion.open_request(user),
        "p9_deletion_removed": deletion.REMOVED_ITEMS,
        "p9_deletion_kept": deletion.KEPT_ITEMS,
        "p9_impersonating": is_impersonating(request),
    }


def do_revoke_device(request, pk):
    if is_impersonating(request):
        messages.error(request, IMPERSONATION_MESSAGE)
        return
    current = request.session.session_key
    row = request.user.device_sessions.filter(pk=pk).first()
    if row is not None and row.session_key == current:
        messages.info(request, "That is this device. Use Sign out to leave this browser.")
        return
    if sessions_service.revoke(request.user, pk):
        messages.success(request, "That device has been signed out.")
    else:
        messages.error(request, "That device was not found or is already signed out.")


def do_revoke_others(request):
    if is_impersonating(request):
        messages.error(request, IMPERSONATION_MESSAGE)
        return
    n = sessions_service.revoke_all_except(request.user, request.session.session_key)
    if n:
        messages.success(request, f"Signed out of {n} other device{'s' if n != 1 else ''}.")
    else:
        messages.info(request, "No other devices were signed in.")


def do_request_deletion(request):
    if is_impersonating(request):
        messages.error(request, IMPERSONATION_MESSAGE)
        return
    if request.POST.get("confirm") != "yes":
        messages.error(request, "Tick the box to confirm you understand what happens next.")
        return
    req = deletion.request_deletion(request.user, request.POST.get("reason", ""))
    messages.success(request, f"Your deletion request (#{req.pk}) has been sent to NBTE administrators.")


def do_withdraw_deletion(request):
    if is_impersonating(request):
        messages.error(request, IMPERSONATION_MESSAGE)
        return
    if deletion.withdraw(request.user):
        messages.success(request, "Your deletion request has been withdrawn.")
    else:
        messages.info(request, "There is no pending request to withdraw.")


def do_passkey_rename(request, pk):
    if is_impersonating(request):
        messages.error(request, IMPERSONATION_MESSAGE)
        return
    if passkeys.rename(request.user, pk, request.POST.get("name", "")):
        messages.success(request, "Passkey renamed.")
    else:
        messages.error(request, "Give the passkey a name.")


def do_passkey_remove(request, pk):
    if is_impersonating(request):
        messages.error(request, IMPERSONATION_MESSAGE)
        return False
    if passkeys.remove(request.user, pk):
        messages.success(request, "Passkey removed. It can no longer be used to sign in.")
        return True
    raise Http404("Passkey not found.")


def _json_body(request):
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _json_error(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def passkey_register_begin_json(request):
    if not request.user.is_authenticated:
        return _json_error("Please sign in first.", 401)
    if is_impersonating(request):
        return _json_error(IMPERSONATION_MESSAGE, 403)
    return JsonResponse(passkeys.registration_options(request, request.user))


def passkey_register_finish_json(request):
    if not request.user.is_authenticated:
        return _json_error("Please sign in first.", 401)
    if is_impersonating(request):
        return _json_error(IMPERSONATION_MESSAGE, 403)
    body = _json_body(request)
    try:
        passkey = passkeys.finish_registration(request, request.user, body.get("credential"), body.get("name", ""))
    except passkeys.PasskeyError as exc:
        return _json_error(str(exc))
    messages.success(request, f"Passkey “{passkey.name}” added. You can now sign in with it.")
    return JsonResponse({"ok": True, "id": passkey.pk, "name": passkey.name})


def passkey_auth_begin_json(request):
    return JsonResponse(passkeys.authentication_options(request))


def _safe_next(request, value, default):
    if value and url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()},
                                                 require_https=request.is_secure()):
        return value
    return default


def passkey_auth_finish_json(request, default_redirect):
    ident = ratelimit.client_ip(request)
    if ratelimit.is_limited("passkey", ident, PASSKEY_FAILURES[0]):
        return _json_error("Too many failed passkey attempts. Wait 15 minutes or use your password.", 429)
    body = _json_body(request)
    try:
        user = passkeys.finish_authentication(request, body.get("credential"))
    except passkeys.PasskeyError as exc:
        ratelimit.hit("passkey", ident, PASSKEY_FAILURES[1])
        return _json_error(str(exc), 400)
    login(request, user, backend=AUTH_BACKEND)
    return JsonResponse({"ok": True, "redirect": _safe_next(request, body.get("next"), default_redirect)})


# ---------------------------------------------------------------------------
# Internal host views
# ---------------------------------------------------------------------------
def _back_to_profile(anchor="p9-security"):
    return redirect(reverse("accounts:profile") + f"#{anchor}")


@login_required
@require_POST
def device_revoke(request, pk):
    do_revoke_device(request, pk)
    return _back_to_profile("p9-devices")


@login_required
@require_POST
def devices_revoke_others(request):
    do_revoke_others(request)
    return _back_to_profile("p9-devices")


@login_required
@require_POST
def deletion_request(request):
    do_request_deletion(request)
    return _back_to_profile("p9-deletion")


@login_required
@require_POST
def deletion_withdraw(request):
    do_withdraw_deletion(request)
    return _back_to_profile("p9-deletion")


@login_required
@require_POST
def passkey_rename(request, pk):
    do_passkey_rename(request, pk)
    return _back_to_profile("p9-passkeys")


@login_required
@require_POST
def passkey_remove(request, pk):
    do_passkey_remove(request, pk)
    return _back_to_profile("p9-passkeys")


@require_POST
def passkey_register_begin(request):
    return passkey_register_begin_json(request)


@require_POST
def passkey_register_finish(request):
    return passkey_register_finish_json(request)


@require_POST
def passkey_auth_begin(request):
    return passkey_auth_begin_json(request)


@require_POST
def passkey_auth_finish(request):
    return passkey_auth_finish_json(request, reverse("core:home"))


# ---------------------------------------------------------------------------
# Digital staff ID (internal host)
# ---------------------------------------------------------------------------
def _qr_payload(request, user):
    token = staff_id.make_staff_token(user)
    url = request.build_absolute_uri(reverse("staff_verify", args=[token]))
    return {"svg": staff_id.qr_svg(url), "url": url, "refresh_seconds": staff_id.WINDOW_SECONDS,
            "generated_at": timezone.now().isoformat()}


@login_required
@never_cache
def my_staff_id(request):
    user = request.user
    if not staff_id.is_eligible(user):
        raise Http404("Staff ID cards are for NBTE staff accounts.")
    from staff import staff_id_styles

    staff = getattr(user, "staff_profile", None) if hasattr(user, "staff_profile") else None
    card = staff_id_styles.cards_for([staff], request)[0] if staff is not None else None
    if card is None:                     # an account without a staff record
        d = staff_id.card_details(user)
        cs = staff_id_styles.card_settings()
        from datetime import date
        card = {**d, **cs, "qr_svg": "", "valid_until": date(date.today().year + cs["valid_years"] - 1, 12, 31)}
    qr = _qr_payload(request, user)
    qr["url"] = reverse("accounts:staff_id_qr")
    return render(request, "accounts/staff_id.html", {
        "card": card, "qr": qr, "staff": staff, "own": True,
        "styles": staff_id_styles.STYLES,
    })


@login_required
@require_GET
@never_cache
def my_staff_id_qr(request):
    if not staff_id.is_eligible(request.user):
        raise Http404
    return JsonResponse(_qr_payload(request, request.user))


@never_cache
def staff_verify(request, token):
    """Public (no login) check of a scanned staff ID QR code."""
    ident = ratelimit.client_ip(request)
    limit, window = VERIFY_LIMIT
    if ratelimit.is_limited("staff-verify", ident, limit):
        response = render(request, "accounts/staff_verify.html", {"result": None, "limited": True}, status=429)
    else:
        ratelimit.hit("staff-verify", ident, window)
        result = staff_id.verify_staff_token(token)
        response = render(request, "accounts/staff_verify.html", {"result": result, "limited": False})
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    return response
