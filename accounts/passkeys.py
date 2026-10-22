"""Passkeys: sign in with fingerprint, face or device PIN (Phase 9, WebAuthn).

Thin wrappers around py_webauthn so the website and the future API share them:

    rp_id(request) / expected_origin(request)
    registration_options(request, user) -> dict      (challenge kept in the session)
    finish_registration(request, user, credential, name="") -> Passkey
    authentication_options(request) -> dict          (usernameless / discoverable)
    finish_authentication(request, credential) -> User
    door_allows(user, request) -> bool
    rename(user, passkey_id, name) / remove(user, passkey_id) -> bool

Errors are raised as PasskeyError with a message safe to show the user.
"""
from __future__ import annotations

import logging

from django.utils import timezone

from .models import Passkey

logger = logging.getLogger(__name__)

REG_CHALLENGE_KEY = "p9_passkey_reg_challenge"
AUTH_CHALLENGE_KEY = "p9_passkey_auth_challenge"
RP_NAME = "NBTE"


class PasskeyError(Exception):
    pass


# ---------------------------------------------------------------------------
# Relying party
# ---------------------------------------------------------------------------
def rp_id(request):
    from qa_core.hosts import request_hostname

    return request_hostname(request)


def expected_origin(request):
    return f"{request.scheme}://{request.get_host()}"


def _is_portal(request):
    return bool(getattr(request, "is_qa_portal", False))


def door_allows(user, request):
    """Staff sign in on the internal host only; institution users and resource
    persons on the QA portal only (Master Plan §3 door rules)."""
    is_staff_account = getattr(user, "user_type", "staff") == "staff"
    return (not is_staff_account) if _is_portal(request) else is_staff_account


def door_refusal_message(request):
    if _is_portal(request):
        return ("This passkey belongs to an NBTE staff account. Staff sign in on the internal "
                "NBTE system, not the QA portal.")
    return "This passkey belongs to a QA portal account. Please sign in at the QA portal."


def user_handle(user):
    from django.utils.crypto import salted_hmac

    return salted_hmac("nbte.passkey.user-handle", str(user.pk)).digest()[:32]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def registration_options(request, user):
    from webauthn import generate_registration_options
    from webauthn.helpers import bytes_to_base64url, options_to_json_dict
    from webauthn.helpers.base64url_to_bytes import base64url_to_bytes
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    exclude = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id))
               for p in user.passkeys.all()]
    options = generate_registration_options(
        rp_id=rp_id(request), rp_name=RP_NAME,
        user_name=user.email, user_id=user_handle(user),
        user_display_name=user.get_full_name() or user.email,
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    request.session[REG_CHALLENGE_KEY] = bytes_to_base64url(options.challenge)
    return options_to_json_dict(options)


def _pop_challenge(request, key):
    from webauthn.helpers.base64url_to_bytes import base64url_to_bytes

    value = request.session.pop(key, None)
    if not value:
        raise PasskeyError("This request has expired. Please try again.")
    return base64url_to_bytes(value)


def finish_registration(request, user, credential, name=""):
    from webauthn import verify_registration_response
    from webauthn.helpers import bytes_to_base64url

    from .sessions_service import parse_device_label

    challenge = _pop_challenge(request, REG_CHALLENGE_KEY)
    if not isinstance(credential, dict):
        raise PasskeyError("The passkey response was not understood.")
    try:
        verified = verify_registration_response(
            credential=credential, expected_challenge=challenge,
            expected_rp_id=rp_id(request), expected_origin=expected_origin(request),
        )
    except Exception as exc:
        logger.info("Passkey registration rejected: %s", exc)
        raise PasskeyError("Your device's passkey could not be verified. Please try again.") from None
    credential_id = bytes_to_base64url(verified.credential_id)
    if Passkey.objects.filter(credential_id=credential_id).exists():
        raise PasskeyError("This passkey is already registered.")
    transports = (credential.get("response") or {}).get("transports") or []
    label = (name or "").strip()[:80] or parse_device_label(request.META.get("HTTP_USER_AGENT", ""))
    return Passkey.objects.create(
        user=user, credential_id=credential_id,
        public_key=bytes_to_base64url(verified.credential_public_key),
        sign_count=verified.sign_count,
        transports=[str(t) for t in transports if isinstance(t, str)][:10],
        name=label[:80],
    )


# ---------------------------------------------------------------------------
# Authentication (usernameless)
# ---------------------------------------------------------------------------
def authentication_options(request):
    from webauthn import generate_authentication_options
    from webauthn.helpers import bytes_to_base64url, options_to_json_dict
    from webauthn.helpers.structs import UserVerificationRequirement

    options = generate_authentication_options(
        rp_id=rp_id(request), user_verification=UserVerificationRequirement.PREFERRED,
    )
    request.session[AUTH_CHALLENGE_KEY] = bytes_to_base64url(options.challenge)
    return options_to_json_dict(options)


def finish_authentication(request, credential):
    """Verify the assertion and return the (not yet logged-in) user."""
    from webauthn import verify_authentication_response
    from webauthn.helpers.base64url_to_bytes import base64url_to_bytes

    challenge = _pop_challenge(request, AUTH_CHALLENGE_KEY)
    if not isinstance(credential, dict):
        raise PasskeyError("The passkey response was not understood.")
    credential_id = str(credential.get("id") or credential.get("rawId") or "")
    passkey = Passkey.objects.select_related("user").filter(credential_id=credential_id).first()
    if passkey is None:
        raise PasskeyError("This passkey is not registered with NBTE. Sign in with your password, "
                           "then add a passkey from your profile.")
    try:
        verified = verify_authentication_response(
            credential=credential, expected_challenge=challenge,
            expected_rp_id=rp_id(request), expected_origin=expected_origin(request),
            credential_public_key=base64url_to_bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
        )
    except Exception as exc:
        logger.info("Passkey sign-in rejected for passkey %s: %s", passkey.pk, exc)
        raise PasskeyError("The passkey could not be verified. Please try again.") from None
    user = passkey.user
    if not user.is_active:
        raise PasskeyError("This account is not active.")
    if not door_allows(user, request):
        raise PasskeyError(door_refusal_message(request))
    if getattr(user, "user_type", "") == "institution" and user.institution_id:
        inst = user.institution  # same rule as the portal's password sign-in form
        if inst.status == "delisted" or not inst.portal_enabled:
            raise PasskeyError("Portal access for your institution is switched off. Contact NBTE for help.")
    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = timezone.now()
    passkey.save(update_fields=["sign_count", "last_used_at"])
    return user


# ---------------------------------------------------------------------------
# Management
# ---------------------------------------------------------------------------
def list_passkeys(user):
    return list(user.passkeys.all())


def rename(user, passkey_id, name):
    name = (name or "").strip()[:80]
    if not name:
        return False
    return bool(Passkey.objects.filter(pk=passkey_id, user=user).update(name=name))


def remove(user, passkey_id):
    passkey = Passkey.objects.filter(pk=passkey_id, user=user).first()
    if passkey is None:
        return False
    passkey.delete()
    return True
