"""Check-out and the rotating office QR (v2.1 Phase D, G1-G4).

Office QR
    The office screen (Attendance -> Office codes -> Show on a screen) shows a
    QR of ``/attendance/scan/<token>/``. The token is a signed
    ``{location, slot}``; a slot is ``attendance.qr_rotation_seconds`` long
    (default 60) and a token is accepted until its slot ends plus
    ``attendance.qr_grace_seconds`` (default 20). An older token is refused
    ("expired"); a forged one fails the signature. Scanning while signed in
    checks you in, or out when you are already checked in.

Check-out
    A button: the server accepts it when the GPS reading is inside an office
    geofence of the person's department, or the current office code, or a
    scanned office QR. The day counts from the check-in; check-out only
    records when people left. A missed check-out is flagged by the nightly
    job (``missing_checkout``) and shown on reports.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import AttendanceLocation, AttendanceRecord

QR_SALT = "attendance.office-qr.v1"


def _cfg_int(key, default):
    from .codes import _cfg_int as c
    return c(key, default)


def qr_rotation_seconds():
    return max(15, min(3600, _cfg_int("attendance.qr_rotation_seconds", 60)))


def qr_grace_seconds():
    return max(0, min(300, _cfg_int("attendance.qr_grace_seconds", 20)))


def _now_ts(now=None):
    return (now or timezone.now()).timestamp()


def current_slot(now=None):
    return int(_now_ts(now) // qr_rotation_seconds())


def make_token(location, now=None):
    return signing.dumps({"l": location.pk, "s": current_slot(now)}, salt=QR_SALT, compress=True)


def qr_payload(location, request=None, now=None):
    """What the office screen shows: the scan URL (absolute when a request is
    given), seconds left in this slot, rotation and the live code."""
    from . import codes

    now = now or timezone.now()
    open_ = codes.location_is_open(location, now)
    token = make_token(location, now) if open_ else ""
    path = reverse("attendance:scan", kwargs={"token": token}) if token else ""
    url = request.build_absolute_uri(path) if (request is not None and path) else path
    rot = qr_rotation_seconds()
    left = rot - int(_now_ts(now) % rot)
    code_row = codes.get_current_code(location, now=now) if open_ else None
    return {"location": location, "is_open": open_, "token": token, "url": url, "seconds_left": left,
            "rotation": rot, "code": code_row.code if code_row else "",
            "code_seconds_left": code_row.seconds_remaining if code_row else 0}


class QRError(ValidationError):
    pass


EXPIRED_MESSAGE = "This office QR has expired. Scan the code on the office screen again."
INVALID_MESSAGE = "This is not a valid office QR."


def verify_token(user, token, now=None):
    """Return the location for a live token that serves the user's department,
    or raise QRError with the user-facing reason."""
    from . import codes
    from .services import non_working_reason

    now = now or timezone.now()
    try:
        data = signing.loads(token or "", salt=QR_SALT)
        loc_id, slot = int(data["l"]), int(data["s"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise QRError(INVALID_MESSAGE)
    rot = qr_rotation_seconds()
    slot_end = (slot + 1) * rot
    ts = _now_ts(now)
    if slot > current_slot(now) + 1 or ts > slot_end + qr_grace_seconds():
        raise QRError(EXPIRED_MESSAGE)
    location = AttendanceLocation.objects.filter(pk=loc_id, is_active=True).first()
    if location is None:
        raise QRError(INVALID_MESSAGE)
    blocked = non_working_reason(timezone.localtime(now).date(), user)
    if blocked:
        raise QRError(blocked)
    if not codes.location_is_open(location, now):
        raise QRError(f"{location.name} is closed: QR check-in works during office hours only.")
    if not location.applies_to_department(user.department_id):
        raise QRError(f"{location.name} is not an attendance location for your department.")
    return location


@dataclass
class Outcome:
    ok: bool
    action: str = ""          # check_in | check_out
    message: str = ""
    record: AttendanceRecord | None = None
    error: str = ""           # "" | refused | gps_outside | code | qr | already


def check_in_with_qr(user, token, now=None):
    from . import codes, services

    now = now or timezone.now()
    today = timezone.localtime(now).date()
    reason = codes.code_check_in_block_reason(user, today)
    if reason:
        return Outcome(False, "check_in", reason, error="refused")
    try:
        location = verify_token(user, token, now)
    except QRError as exc:
        return Outcome(False, "check_in", exc.messages[0], error="qr")
    status = services._status_for_check_in(location, timezone.localtime(now))
    try:
        rec = services.mark_status(user, today, status, source=AttendanceRecord.SOURCE_QR, actor=user,
                                   location=location, check_in_at=now,
                                   notes=f"Check-in by office QR · {location.name}")
    except ValidationError as exc:
        return Outcome(False, "check_in", " ".join(exc.messages), error="refused")
    services.log(user, today, f"QR check-in via {location.name}", kind="check_in")
    return Outcome(True, "check_in", f"Checked in at {location.name}. "
                   + ("You're on time." if status == AttendanceRecord.PRESENT else "Recorded as late."), rec)


def checkout_state(user, day=None):
    """("can", record) | (reason, record) for the check-out button."""
    day = day or timezone.localdate()
    rec = AttendanceRecord.objects.filter(user=user, date=day).select_related("location").first()
    if rec is None or rec.status not in AttendanceRecord.STATUSES_PRESENT or rec.check_in_at is None:
        return "You have not checked in today, so there is nothing to check out from.", rec
    if rec.check_out_at is not None:
        return f"You already checked out at {timezone.localtime(rec.check_out_at):%H:%M}.", rec
    return "", rec


@transaction.atomic
def check_out(user, *, method, lat=None, lng=None, accuracy=None, code="", token="", now=None):
    """Record the check-out after verifying presence (G2)."""
    from . import codes, services

    now = now or timezone.now()
    today = timezone.localtime(now).date()
    reason, rec = checkout_state(user, today)
    if reason:
        return Outcome(False, "check_out", reason, rec, error="already" if rec and rec.check_out_at else "refused")
    rec = AttendanceRecord.objects.select_for_update().get(pk=rec.pk)
    fields = {"check_out_at": now, "missing_checkout": False}
    if method == "gps":
        try:
            lat, lng, _acc = services.validate_gps_reading(lat, lng, accuracy)
        except ValidationError as exc:
            return Outcome(False, "check_out", exc.messages[0], rec, error="gps_outside")
        location, distance = services.find_matching_location(user, lat, lng)
        if location is None:
            return Outcome(False, "check_out", "You're not at an office location. Enter the current office code "
                           "or scan the office QR instead.", rec, error="gps_outside")
        fields.update(check_out_latitude=lat, check_out_longitude=lng, check_out_distance_meters=distance,
                      check_out_source=AttendanceRecord.SOURCE_GPS)
        where = location.name
    elif method == "code":
        try:
            location, _row = codes.verify_check_in_code(user, code, now=now)
        except ValidationError as exc:
            return Outcome(False, "check_out", "; ".join(exc.messages), rec, error="code")
        if location is None:
            return Outcome(False, "check_out", codes.INVALID_CODE_MESSAGE, rec, error="code")
        fields["check_out_source"] = AttendanceRecord.SOURCE_CODE
        where = location.name
    elif method == "qr":
        try:
            location = verify_token(user, token, now)
        except QRError as exc:
            return Outcome(False, "check_out", exc.messages[0], rec, error="qr")
        fields["check_out_source"] = AttendanceRecord.SOURCE_QR
        where = location.name
    else:
        return Outcome(False, "check_out", "Choose how to confirm you're at the office.", rec, error="refused")
    for k, v in fields.items():
        setattr(rec, k, v)
    rec.save(update_fields=list(fields) + ["updated_at"])
    services.log(user, today, f"Check-out ({fields['check_out_source']}) at {where}", kind="check_out")
    return Outcome(True, "check_out", f"Checked out at {timezone.localtime(now):%H:%M}. See you tomorrow.", rec)


def scan(user, token, now=None):
    """The scan endpoint: check in, or check out when already checked in."""
    reason, rec = checkout_state(user, timezone.localtime(now or timezone.now()).date())
    if not reason:
        return check_out(user, method="qr", token=token, now=now)
    if rec is not None and rec.check_out_at is not None:
        return Outcome(False, "check_out", reason, rec, error="already")
    return check_in_with_qr(user, token, now=now)


def flag_missing_checkouts(before=None):
    """Nightly (G3): every past day checked in without a check-out is flagged.
    The day still counts as present / late."""
    before = before or timezone.localdate()
    return (AttendanceRecord.objects
            .filter(date__lt=before, status__in=AttendanceRecord.STATUSES_PRESENT, check_in_at__isnull=False,
                    check_out_at__isnull=True, missing_checkout=False)
            .update(missing_checkout=True, updated_at=timezone.now()))


def qr_svg(url):
    from accounts.staff_id import qr_svg as _svg
    return _svg(url)


__all__ = ["make_token", "verify_token", "check_in_with_qr", "check_out", "scan", "flag_missing_checkouts",
           "qr_payload", "checkout_state"]
