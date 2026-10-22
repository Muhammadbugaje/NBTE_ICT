"""Digital staff ID card with a rotating QR code (Phase 9).

    make_staff_token(user) -> str
    verify_staff_token(token) -> StaffVerification
    card_details(user) -> dict
    qr_svg(data) -> str

The QR encodes https://<internal host>/verify/staff/<token>/ where the token
is a TimestampSigner-signed payload {user id, 60-second window}. The card
page fetches a fresh token every 60 seconds; the public verify page accepts a
token for VALID_SECONDS (2 minutes) so a code scanned just before it rotates
still works.
"""
from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from datetime import datetime

from django.core import signing
from django.utils import timezone

SALT = "nbte.staff-id.v1"
WINDOW_SECONDS = 60
VALID_SECONDS = 120

VALID, EXPIRED, INVALID = "valid", "expired", "invalid"


def _signer():
    return signing.TimestampSigner(salt=SALT)


def is_eligible(user):
    """Only active NBTE staff accounts get a staff ID."""
    return bool(user and getattr(user, "pk", None) and user.is_active
                and getattr(user, "user_type", "staff") == "staff")


def make_staff_token(user, now=None):
    window = int((now if now is not None else time.time()) // WINDOW_SECONDS)
    return _signer().sign_object({"u": user.pk, "w": window}, compress=True)


def _staff(user):
    try:
        return user.staff_profile
    except Exception:
        return None


def initials(user):
    first = (user.first_name or user.email or "?")[:1]
    last = (user.last_name or "")[:1]
    return (first + last).upper()


def card_details(user):
    """What the card and the verify page show. Never e-mail or phone."""
    staff = _staff(user)
    department = None
    if staff is not None and staff.department_id:
        department = staff.department.name
    elif user.department_id:
        department = user.department.name
    photo_url = ""
    if staff is not None and staff.photo:
        try:
            photo_url = staff.photo.url
        except Exception:
            photo_url = ""
    return {
        "name": user.get_full_name() or user.email.split("@")[0],
        "initials": initials(user),
        "designation": str(staff.designation) if staff is not None and staff.designation_id else "",
        "department": department or "",
        "staff_number": staff.employee_no if staff is not None else "",
        "photo_url": photo_url,
    }


@dataclass
class StaffVerification:
    status: str
    verified_at: datetime = field(default_factory=timezone.now)
    user_id: int | None = None
    name: str = ""
    initials: str = ""
    designation: str = ""
    department: str = ""
    photo_url: str = ""

    @property
    def is_valid(self):
        return self.status == VALID


def verify_staff_token(token, max_age=VALID_SECONDS):
    from .models import User

    try:
        payload = _signer().unsign_object(token or "", max_age=max_age)
    except signing.SignatureExpired:
        return StaffVerification(status=EXPIRED)
    except (signing.BadSignature, ValueError, TypeError):
        return StaffVerification(status=INVALID)
    user_id = payload.get("u") if isinstance(payload, dict) else None
    user = User.objects.filter(pk=user_id).select_related("department").first() if user_id else None
    if not is_eligible(user):
        return StaffVerification(status=INVALID)
    details = card_details(user)
    return StaffVerification(
        status=VALID, user_id=user.pk, name=details["name"], initials=details["initials"],
        designation=details["designation"], department=details["department"], photo_url=details["photo_url"],
    )


def qr_svg(data):
    """An inline <svg> of the QR code (no XML prolog), sized by CSS."""
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2,
                      error_correction=qrcode.constants.ERROR_CORRECT_M)
    buf = io.BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode("utf-8")
    start = svg.find("<svg")
    return svg[start:] if start >= 0 else svg
