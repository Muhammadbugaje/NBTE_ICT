"""Write path for Settings → QA (Master Plan §14).

* `editable_by` decides who may edit a row (rows the user cannot edit are
  shown locked).
* `dual_control` rows are never written directly: saving creates a
  PendingAction (`config.update_setting`) that a different officer with
  `qa_core.edit_qa_dual_control_settings` confirms.
* Locked rows ("Fixed by policy") have no edit path at all; `ConfigEntry.save()`
  refuses them too.
"""
import json

from django.core.exceptions import PermissionDenied, ValidationError

from config.models import LOCKED_PERMISSION, ConfigEntry

DEFAULT_EDIT_PERMISSION = "qa_core.edit_qa_settings"


def qa_entries():
    # v2.2: the honoraria.* rows belong to QA too (they were editable nowhere).
    from django.db.models import Q

    return ConfigEntry.objects.filter(Q(key__startswith="qa.") | Q(key__startswith="honoraria."), scope="global")


def edit_permission(entry):
    return entry.editable_by or DEFAULT_EDIT_PERMISSION


def can_edit(user, entry):
    """(allowed, reason)."""
    if entry is None:
        return False, "No such setting."
    if entry.is_locked or entry.editable_by == LOCKED_PERMISSION:
        return False, "Fixed by policy."
    if user is None or not user.is_authenticated or getattr(user, "user_type", "staff") != "staff":
        return False, "Only NBTE staff can edit settings."
    if not user.has_perm(edit_permission(entry)):
        return False, f"Needs {edit_permission(entry)}."
    return True, ""


def control_label(entry):
    if entry.is_locked:
        return "locked"
    if entry.dual_control:
        return "dual"
    return "direct"


def display_value(value_type, raw):
    """Human rendering of a stored value for tables and diffs."""
    raw = "" if raw is None else str(raw)
    if value_type == "bool":
        return "Yes" if raw.strip().lower() in ("1", "true", "yes", "on") else "No"
    if value_type == "formula":
        try:
            return json.loads(raw).get("expr", raw)
        except (ValueError, AttributeError):
            return raw
    if value_type == "json":
        try:
            return json.dumps(json.loads(raw), ensure_ascii=False)
        except ValueError:
            return raw
    if value_type == "decimal":
        try:
            from decimal import Decimal
            value = Decimal(raw)
            return f"{value:,}"
        except Exception:
            return raw
    return raw if raw != "" else "—"


def normalise_input(entry, form_value, *, formula_vars=None, formula_returns=None):
    """Turn what the form posted into the stored text for `entry`."""
    value = (form_value or "")
    if entry.value_type == "bool":
        return "true" if str(value).strip().lower() in ("1", "true", "yes", "on") else "false"
    if entry.value_type == "formula":
        current = entry.parsed()
        vars_ = formula_vars if formula_vars is not None else current.get("vars", [])
        returns = formula_returns or current.get("returns", "decimal")
        return json.dumps({"expr": value.strip(), "vars": vars_, "returns": returns})
    if entry.value_type in ("int", "decimal", "date"):
        return value.strip()
    if entry.value_type == "json":
        text = value.strip()
        try:
            return json.dumps(json.loads(text), ensure_ascii=False)
        except ValueError:
            return text  # clean() reports the error
    return value


def save_setting(user, entry, new_value, *, reason="", impersonating=False):
    """Returns ("saved", entry) or ("pending", PendingAction). Raises
    PermissionDenied / ValidationError with a user-facing message."""
    from . import actions

    allowed, why = can_edit(user, entry)
    if not allowed:
        raise PermissionDenied(why)
    if impersonating:
        raise PermissionDenied("You cannot change settings while impersonating another user.")
    if new_value == entry.value:
        raise ValidationError("The new value is the same as the current one.")
    if entry.dual_control:
        pa = actions.initiate("config.update_setting", actor=user, target=entry,
                              payload={"value": new_value}, reason=reason)
        return "pending", pa
    entry.value = new_value
    entry.full_clean()
    entry.save()
    return "saved", entry
