"""me/preferences/, me/devices/, me/deletion-request/, me/staff-id/ - glue to
the website's own services:

* preferences  notifications.preferences.get_preferences + the website's
               NotificationPreferenceForm (notifications.settings_views.preference_form),
               door "internal" for staff, "portal" otherwise (only that door's kinds)
* devices      accounts.sessions_service (list_sessions / revoke / revoke_all_except /
               set_push_token); app rows via api.devices
* deletion     accounts.deletion (request_deletion / withdraw / latest_request / open_request)
* staff ID     accounts.staff_id (make_staff_token / card_details / qr_svg)
"""
from rest_framework import serializers

from .. import devices


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
def pref_door(user):
    return "internal" if getattr(user, "user_type", "staff") == "staff" else "portal"


def _hhmm(value):
    return value.strftime("%H:%M") if value else None


def preferences_payload(user):
    from notifications.preferences import TIMEZONE_CHOICES, fields_for_door, get_preferences

    pref = get_preferences(user)
    kinds = fields_for_door(pref_door(user))
    data = {name: bool(getattr(pref, name)) for name, *_ in kinds}
    data.update({
        "email_digest": pref.email_digest,
        "quiet_hours_enabled": pref.quiet_hours_enabled,
        "quiet_from": _hhmm(pref.quiet_from),
        "quiet_to": _hhmm(pref.quiet_to),
        "allow_urgent_during_quiet": pref.allow_urgent_during_quiet,
        "show_previews": pref.show_previews,
        "transcribe_voice_notes": pref.transcribe_voice_notes,
        "timezone": pref.timezone or "Africa/Lagos",
        "kinds": [{"key": name, "label": label, "help": help_text} for name, label, help_text, _d in kinds],
        "timezones": list(TIMEZONE_CHOICES),
    })
    return data


def save_preferences(user, changes):
    """PATCH semantics through the website's form: current values + changes."""
    from notifications.forms import COMMON_FIELDS
    from notifications.settings_views import preference_form

    current = preference_form(user, pref_door(user))
    data = {}
    for name in COMMON_FIELDS + tuple(k[0] for k in current.kinds):
        value = changes.get(name, getattr(current.instance, name))
        if hasattr(value, "strftime"):
            value = value.strftime("%H:%M")
        data[name] = value
    form = preference_form(user, pref_door(user), data)
    if not form.is_valid():
        raise serializers.ValidationError({("non_field_errors" if k == "__all__" else k): [str(e) for e in v]
                                           for k, v in form.errors.items()})
    form.save()


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------
def device_rows(user, request):
    from accounts import sessions_service

    current = devices.current_session(request)
    current_key = current.session_key if current is not None else None
    rows = sessions_service.list_sessions(user, current_key)
    out = []
    for row in rows:
        out.append({
            "id": row.pk,
            "label": row.device_label or "Unknown device",
            "kind": row.kind,
            "platform": row.platform or ("web" if row.kind == "web" else ""),
            "door": row.door,
            "app_version": row.app_version,
            "created_at": row.created_at,
            "last_seen_at": row.last_seen_at,
            "current": bool(current is not None and row.pk == current.pk),
        })
    return out


def revoke_device(user, pk):
    """True when one of the user's active devices was signed out."""
    from accounts import sessions_service

    return sessions_service.revoke(user, pk)


def revoke_others(request):
    from accounts import sessions_service

    current = devices.current_session(request)
    # No device row (a token minted outside auth/token/): keep nothing back.
    return sessions_service.revoke_all_except(request.user, current.session_key if current else None)


def store_push_token(request, push_token, device_id=""):
    """On the request's own device row; falls back to the user's active app
    row with that device_id. False when there is no such row."""
    from accounts import sessions_service
    from accounts.models import UserSession

    row = devices.current_session(request)
    if row is None and device_id:
        row = (UserSession.objects.filter(user=request.user, kind=UserSession.APP, device_id=device_id,
                                          revoked_at__isnull=True).order_by("-last_seen_at").first())
    if row is None:
        return False
    sessions_service.set_push_token(row, push_token or "", device_id or None)
    return True


# ---------------------------------------------------------------------------
# Deletion request
# ---------------------------------------------------------------------------
def deletion_row(req):
    if req is None:
        return None
    from accounts.models import AccountDeletionRequest

    return {"id": req.pk, "status": req.status, "status_label": req.get_status_display(),
            "reason": req.reason, "created_at": req.created_at, "handled_at": req.handled_at,
            "note": getattr(req, "note", "") or "",
            "can_withdraw": req.status == AccountDeletionRequest.PENDING}


def deletion_status(user):
    from accounts import deletion

    return {"request": deletion_row(deletion.latest_request(user)),
            "open": deletion.open_request(user) is not None,
            "removed_items": list(deletion.REMOVED_ITEMS), "kept_items": list(deletion.KEPT_ITEMS)}


# ---------------------------------------------------------------------------
# Staff ID
# ---------------------------------------------------------------------------
def verify_url(request, token):
    """Absolute /verify/staff/<token>/ on the internal host (INTERNAL_HOST
    when configured, else the host this staff-door request came in on)."""
    from django.conf import settings
    from django.urls import reverse

    path = reverse("staff_verify", args=[token], urlconf=settings.ROOT_URLCONF)
    if (getattr(settings, "INTERNAL_HOST", "") or "").strip():
        from qa_core.hosts import internal_url

        return internal_url(request, path)
    return request.build_absolute_uri(path)


def staff_id_payload(user, request):
    from accounts import staff_id

    token = staff_id.make_staff_token(user)
    url = verify_url(request, token)
    card = staff_id.card_details(user)
    photo = card["photo_url"] or None
    if photo and not photo.startswith(("http://", "https://")):
        photo = request.build_absolute_uri(photo)
    return {
        "token": token,
        "verify_url": url,
        "expires_in": staff_id.WINDOW_SECONDS,
        "valid_for": staff_id.VALID_SECONDS,
        "qr_svg": staff_id.qr_svg(url),
        "card": {"full_name": card["name"], "initials": card["initials"], "designation": card["designation"],
                 "department": card["department"], "staff_number": card["staff_number"] or "",
                 "photo_url": photo},
    }
