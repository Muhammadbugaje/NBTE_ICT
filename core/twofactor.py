"""Optional e-mail sign-in code (v2.1 S-26). OFF by default.

No OTP/TOTP library is installed (requirements.txt), so this is an e-mailed
6-digit code, valid 10 minutes, 5 tries, after a PASSWORD sign-in, for:

* roles flagged ``requires_2fa`` (ES, Directors, Deputy Directors - seed_roles);
* role codes in ``security.2fa.role_codes`` (Finance first-pass officers, PB
  admins ... - JSON list);
* anyone holding a permission in ``security.2fa.permissions`` (JSON list:
  ``honoraria.*``, ``documents.revoke_document``, dual-control editors ...;
  a trailing ``.*`` means any permission of that app).

Switch on with ``security.2fa.email_code_enabled`` = true. Passkey sign-ins
skip it (a passkey is already two factors).
"""
import hashlib
import secrets
import time

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.shortcuts import redirect
from django.urls import reverse

from config import registry as config

PENDING = "_otp_pending"
DATA = "_otp"
CODE_TTL = 10 * 60
MAX_TRIES = 5


def enabled():
    return bool(config.get("security.2fa.email_code_enabled", False))


def _as_list(value):
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def required_for(user):
    if user is None or not getattr(user, "is_authenticated", False) or getattr(user, "user_type", "staff") != "staff":
        return False
    role = getattr(user, "role", None)
    if role is not None and (role.requires_2fa or role.code in _as_list(config.get("security.2fa.role_codes", []))):
        return True
    perms = _as_list(config.get("security.2fa.permissions", []))
    if perms:
        held = user.get_all_permissions()
        for p in perms:
            if p.endswith(".*"):
                if any(h.startswith(p[:-1]) for h in held):
                    return True
            elif p in held:
                return True
    return False


def _hash(code):
    return hashlib.sha256(code.encode()).hexdigest()


def start(request, user):
    code = f"{secrets.randbelow(10 ** 6):06d}"
    request.session[DATA] = {"h": _hash(code), "exp": time.time() + CODE_TTL, "tries": 0}
    request.session[PENDING] = True
    try:
        from core.email_utils import send_templated_email

        send_templated_email("Your NBTE sign-in code", [user.email], "emails/account_security_notice.html",
                             {"subject": "Your sign-in code", "name": user.get_full_name() or user.email,
                              "body": f"Your sign-in code is {code}. It is valid for 10 minutes. "
                                      "Nobody from NBTE will ever ask you for it."},
                             category="security")
    except Exception:
        pass
    return code


def verify(request, code):
    data = request.session.get(DATA) or {}
    if not data or time.time() > data.get("exp", 0):
        return False, "The code has expired. Sign in again to get a new one."
    if data.get("tries", 0) >= MAX_TRIES:
        return False, "Too many wrong codes. Sign in again to get a new one."
    if _hash((code or "").strip()) != data.get("h"):
        data["tries"] = data.get("tries", 0) + 1
        request.session[DATA] = data
        return False, "That code is not right."
    request.session.pop(DATA, None)
    request.session.pop(PENDING, None)
    return True, ""


ALLOWED_PREFIXES = ("/static/", "/media/", "/favicon")


def gate(request):
    """Middleware hook: while a code is pending, only the code page and sign-out work."""
    if not request.session.get(PENDING):
        return None
    try:
        allowed = {reverse("core:verify_code"), reverse("accounts:logout")}
    except Exception:
        return None
    if request.path in allowed or request.path.startswith(ALLOWED_PREFIXES):
        return None
    return redirect(reverse("core:verify_code"))


@receiver(user_logged_in, dispatch_uid="v21_session_policy_login")
def _on_login(sender, request, user, **kwargs):
    if request is None or not hasattr(request, "session"):
        return
    from .session_policy import mark_login

    mark_login(request)
    if "/passkeys/" in (request.path or ""):
        return
    if enabled() and required_for(user):
        start(request, user)
