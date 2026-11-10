"""Who may manage HR settings (leave types, public holidays) - v2.1 Phase D.

    Super admin ......... always.
    Permission .......... ``leave.manage_leave_types`` (grantable from the
                          permissions screens; ``portal.manage_holiday`` also
                          opens the holidays page).
    HR department ....... active staff of the departments in the config key
                          ``leave.hr_department_codes`` (default ["hr"]).
"""
import json

PERM_TYPES = "leave.manage_leave_types"
PERM_HOLIDAYS = "portal.manage_holiday"


def _hr_codes():
    try:
        from config.registry import get
        raw = get("leave.hr_department_codes", ["hr"])
    except Exception:
        raw = ["hr"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = [raw]
    return {str(c).strip().lower() for c in (raw or []) if str(c).strip()}


def in_hr(user):
    dept = getattr(user, "department", None) if getattr(user, "department_id", None) else None
    return bool(dept and (dept.code or "").strip().lower() in _hr_codes())


def _super(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    import org
    return org.is_super_admin(user)


def can_manage_leave_types(user):
    return user.is_authenticated and (_super(user) or user.has_perm(PERM_TYPES) or in_hr(user))


def can_manage_holidays(user):
    return user.is_authenticated and (_super(user) or user.has_perm(PERM_TYPES) or user.has_perm(PERM_HOLIDAYS)
                                      or in_hr(user))
