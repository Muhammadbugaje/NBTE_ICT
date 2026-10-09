"""Reusable template tags for the attendance module."""
from django import template

register = template.Library()


# ---------------------------------------------------------------------------
# Simple filters
# ---------------------------------------------------------------------------
_PILL_CLASS = {
    "present": "att-pill-present",
    "late":    "att-pill-late",
    "absent":  "att-pill-absent",
    "excused": "att-pill-excused",
    "holiday": "att-pill-holiday",
}


@register.filter
def attendance_pill_class(status):
    return _PILL_CLASS.get(status, "att-pill-neutral")


_MATRIX_CELL_CLASS = {
    "present": "is-present",
    "late":    "is-late",
    "absent":  "is-absent",
    "excused": "is-excused",
    "holiday": "is-holiday",
    "off":     "is-off",
    "":        "is-unrecorded",
}


@register.filter
def attendance_cell_class(status):
    return _MATRIX_CELL_CLASS.get(status, "is-unrecorded")


@register.filter
def attendance_cell_letter(status):
    """Letter used inside the department matrix."""
    return {
        "present": "P",
        "late":    "L",
        "absent":  "A",
        "excused": "E",
        "holiday": "H",
        "off":     "·",
        "":        "—",
    }.get(status, "—")


# ---------------------------------------------------------------------------
# Simple tags — callable from any template
# ---------------------------------------------------------------------------
@register.simple_tag
def attendance_stats_for(user, days=30):
    """Return attendance_stats(user, days) or None if user isn't a real user."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    try:
        from attendance import services
        return services.attendance_stats(user, days=days)
    except Exception:
        return None


@register.simple_tag
def attendance_today_for(user):
    """Return today's AttendanceRecord for the user, or None."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    try:
        from attendance.models import AttendanceRecord
        from django.utils import timezone
        return AttendanceRecord.objects.filter(
            user=user, date=timezone.localdate(),
        ).first()
    except Exception:
        return None


@register.simple_tag
def attendance_is_working_day(d=None):
    try:
        from attendance import services
        from django.utils import timezone
        return services.is_working_day(d or timezone.localdate())
    except Exception:
        return True


@register.simple_tag
def attendance_recent_records(user, limit=5):
    if not user or not getattr(user, "is_authenticated", False):
        return []
    try:
        from attendance.models import AttendanceRecord
        return list(
            AttendanceRecord.objects
            .filter(user=user)
            .order_by("-date")[:limit]
        )
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Inclusion tags — full widgets
# ---------------------------------------------------------------------------
@register.inclusion_tag("attendance/_profile_widget.html", takes_context=True)
def attendance_profile_widget(context, user=None, days=30):
    """Widget for a user's own profile page.

    Usage:
        {% attendance_profile_widget user=profile_user %}
        {% attendance_profile_widget user=request.user days=90 %}
    """
    request = context.get("request")
    if user is None and request is not None:
        user = request.user

    ctx = {
        "att_user": user,
        "days": days,
        "stats": None,
        "recent": [],
        "today": None,
        "is_self": bool(
            request is not None
            and user is not None
            and request.user.pk == getattr(user, "pk", None)
        ),
        "visible": False,
    }
    if not user or not getattr(user, "is_authenticated", False):
        return ctx

    try:
        from attendance import services
        from attendance.models import AttendanceRecord
        from django.utils import timezone

        stats = services.attendance_stats(user, days=days)
        recent = list(
            AttendanceRecord.objects
            .filter(user=user)
            .order_by("-date")[:6]
        )
        today = AttendanceRecord.objects.filter(
            user=user, date=timezone.localdate(),
        ).first()

        ctx.update({
            "stats": stats,
            "recent": recent,
            "today": today,
            "visible": True,
        })
    except Exception:
        pass

    return ctx


@register.inclusion_tag("attendance/_dashboard_widget.html", takes_context=True)
def attendance_dashboard_widget(context):
    """Widget for the home dashboard — today + quick actions."""
    request = context.get("request")
    user = request.user if request is not None else None

    ctx = {
        "visible": False,
        "today": None,
        "stats": None,
        "is_working_day": True,
        "has_department": bool(user and user.department_id) if user else False,
    }
    if not user or not user.is_authenticated:
        return ctx

    try:
        from attendance import services
        from attendance.models import AttendanceRecord, AttendanceLocation
        from django.utils import timezone

        today = timezone.localdate()
        rec = AttendanceRecord.objects.filter(user=user, date=today).first()
        stats = services.attendance_stats(user, days=30)
        working = services.is_working_day(today)

        # Are there active locations for this user?
        locs = list(AttendanceLocation.objects.filter(is_active=True))
        applicable = [l for l in locs if l.applies_to_department(user.department_id)]
        can_check_in = bool(user.department_id and applicable and working and rec is None)

        ctx.update({
            "visible": True,
            "today": rec,
            "stats": stats,
            "is_working_day": working,
            "has_department": bool(user.department_id),
            "can_check_in": can_check_in,
        })
    except Exception:
        pass

    return ctx