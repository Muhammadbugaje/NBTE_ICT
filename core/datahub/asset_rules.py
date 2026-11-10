"""Data Hub rules for the Assets category (registry: Category "assets").

The engine is generic; these hooks hold what only assets need:

* FILLS   - before a row is matched: generate a blank asset tag the way the
            New asset page does (``assets.services._unique_asset_tag``), or
            match it on a unique serial number; give a blank hand-off date the
            date of the person's open hand-off (so re-imports never duplicate).
* CHECKS  - the holding rules of ``assets.services`` (K1/K2): individual and
            shared hand-offs name a person, department hand-offs a department;
            one open hand-off for individual/department assets, one per person
            for shared ones; the mode cannot change while the asset is held.
* AFTER   - keep ``Asset.current_custodian`` (denormalised) in step with the
            open hand-offs, as ``services._sync_custodian`` does.
* ALIASES - other spellings a link accepts: a staff number for a person, a
            category code, a department name, a unique serial number.
"""
import os

ASSET_SHEET = "assets.Asset"
ASSIGNMENT_SHEET = "assets.AssetAssignment"


def _error(msg):
    from .engine import CellError
    return CellError(msg)


def _blank(values, name):
    v = values.get(name)
    return v is None or (isinstance(v, str) and not v.strip())


def _check_lengths(model, parsed):
    from django.db import models
    for name, value in parsed.items():
        f = model._meta.get_field(name)
        if isinstance(f, models.CharField) and f.max_length and isinstance(value, str) \
                and len(value) > f.max_length:
            raise _error(f"{name}: at most {f.max_length} characters (this one has {len(value)})")


# ---------------------------------------------------------------------------
# Fills
# ---------------------------------------------------------------------------
def fill_asset(run, sd, parsed, values):
    from assets.models import Asset, AssetCategory
    from staff.models import Department

    tag = str(parsed.get("asset_tag") or "").strip()
    if tag:
        pk = run.keys.lookup(Asset, tag)
        if pk is not None:
            # Tags never change: keep the stored spelling.
            parsed["asset_tag"] = Asset.all_objects.filter(pk=pk).values_list("asset_tag", flat=True).first()
        else:
            if len(tag) > 40:
                raise _error(f"asset_tag: at most 40 characters ('{tag}')")
            parsed["asset_tag"] = tag.upper()          # as the New asset form does
        return
    serial = str(parsed.get("serial_number") or "").strip()
    if serial:
        hits = list(Asset.all_objects.filter(serial_number__iexact=serial).values_list("asset_tag", flat=True)[:2])
        if len(hits) > 1:
            raise _error(f"asset_tag is blank and serial_number '{serial}' matches several assets: "
                         "give the asset_tag of the one to update.")
        if hits:
            parsed["asset_tag"] = hits[0]
            return
    if not parsed.get("category"):
        raise _error("category is required to generate an asset tag")
    from assets.services import _unique_asset_tag
    category = AssetCategory.objects.get(pk=parsed["category"])
    dept_pk = parsed.get("owner_department")
    department = Department._base_manager.filter(pk=dept_pk).first() if dept_pk else None
    parsed["asset_tag"] = _unique_asset_tag(category, department)


def fill_assignment(run, sd, parsed, values):
    from assets.models import AssetAssignment
    if _blank(values, "assigned_at") and parsed.get("asset"):
        row = (AssetAssignment.objects
               .filter(asset_id=parsed["asset"], returned_at__isnull=True,
                       assigned_to_id=parsed.get("assigned_to"), department_id=parsed.get("department"))
               .order_by("-assigned_at").values_list("assigned_at", flat=True).first())
        if row is not None:
            parsed["assigned_at"] = row
        # else: the field default (now) from the parser stays.


def fill_issue(run, sd, parsed, values):
    from assets.models import AssetIssue
    if _blank(values, "reported_at") and parsed.get("asset") and parsed.get("summary"):
        when = (AssetIssue.objects.filter(asset_id=parsed["asset"], summary__iexact=parsed["summary"].strip())
                .order_by("-reported_at").values_list("reported_at", flat=True).first())
        if when is not None:
            parsed["reported_at"] = when


def fill_attachment(run, sd, parsed, values):
    if not str(parsed.get("file") or "").strip():
        raise _error("file is required (the stored file path, e.g. assets/attachments/….jpg)")
    if "original_name" not in parsed or not str(parsed.get("original_name") or "").strip():
        parsed["original_name"] = os.path.basename(str(parsed["file"]))


FILLS = {
    "asset": fill_asset,
    "asset_assignment": fill_assignment,
    "asset_issue": fill_issue,
    "asset_attachment": fill_attachment,
}


# ---------------------------------------------------------------------------
# Row checks
# ---------------------------------------------------------------------------
def _mode_label(mode):
    from assets.models import Asset
    return dict(Asset.ASSIGNMENT_MODE_CHOICES).get(mode, mode)


def check_asset(parsed, obj):
    from assets.models import Asset
    _check_lengths(Asset, parsed)
    mode = parsed.get("assignment_mode") or (obj.assignment_mode if obj is not None else Asset.MODE_INDIVIDUAL)
    dept = parsed["owner_department"] if "owner_department" in parsed else getattr(obj, "owner_department_id", None)
    if mode == Asset.MODE_DEPARTMENT and not dept:
        raise _error("owner_department is required when assignment_mode is "
                     f"'{_mode_label(mode)}' (the department code)")
    if obj is not None and mode != obj.assignment_mode and obj.open_assignments().exists():
        raise _error(f"assignment_mode cannot change from '{_mode_label(obj.assignment_mode)}' to "
                     f"'{_mode_label(mode)}' while the asset is held: return it from every holder first "
                     "(fill returned_at on the Asset assignments sheet and import that first).")


def check_assignment(parsed, obj):
    from accounts.models import User
    from assets.models import Asset, AssetAssignment

    _check_lengths(AssetAssignment, parsed)

    def cur(name, attname=None):
        return parsed[name] if name in parsed else getattr(obj, attname or f"{name}_id", None)

    asset = Asset.all_objects.filter(pk=cur("asset")).first()
    if asset is None:
        raise _error("asset is required")
    person, dept = cur("assigned_to"), cur("department")
    mode = asset.assignment_mode
    label = _mode_label(mode)
    if mode == Asset.MODE_DEPARTMENT:
        if not dept or person:
            raise _error(f"{asset.asset_tag} is held as '{label}': give the department (code) and leave "
                         "assigned_to blank.")
    else:
        if not person or dept:
            raise _error(f"{asset.asset_tag} is held as '{label}': give the person in assigned_to (e-mail or "
                         "staff number) and leave department blank.")
        utype = User._base_manager.filter(pk=person).values_list("user_type", flat=True).first()
        if utype != "staff":
            raise _error("assigned_to: assets are held by NBTE staff accounts only")
    assigned_at, returned_at = cur("assigned_at", "assigned_at"), cur("returned_at", "returned_at")
    if returned_at and assigned_at and returned_at < assigned_at:
        raise _error("returned_at is before assigned_at")
    if returned_at is None:
        others = AssetAssignment.objects.filter(asset=asset, returned_at__isnull=True)
        if obj is not None:
            others = others.exclude(pk=obj.pk)
        if mode == Asset.MODE_SHARED:
            if others.filter(assigned_to_id=person).exists():
                raise _error(f"{asset.asset_tag}: this person already holds a share of it (an open row).")
        else:
            other = others.first()
            if other is not None:
                raise _error(f"{asset.asset_tag} ('{label}') is already held by {other.holder_label} "
                             f"since {other.assigned_at:%Y-%m-%d}: close that row (returned_at) first.")


CHECKS = {
    "asset": check_asset,
    "asset_assignment": check_assignment,
}


# ---------------------------------------------------------------------------
# After the run
# ---------------------------------------------------------------------------
def sync_custody(run):
    """``current_custodian`` = the open individual hand-off (as services._sync_custodian)."""
    from assets.models import Asset, AssetAssignment
    ids = set(run.touched.get(ASSET_SHEET, ()))
    rows = run.touched.get(ASSIGNMENT_SHEET)
    if rows:
        ids |= set(AssetAssignment.objects.filter(pk__in=rows).values_list("asset_id", flat=True))
    for asset in Asset.all_objects.filter(pk__in=ids).only("pk", "assignment_mode", "current_custodian"):
        custodian = None
        if asset.assignment_mode == Asset.MODE_INDIVIDUAL:
            custodian = (asset.assignments.filter(returned_at__isnull=True, assigned_to__isnull=False)
                         .values_list("assigned_to_id", flat=True).first())
        if asset.current_custodian_id != custodian:
            Asset.all_objects.filter(pk=asset.pk).update(current_custodian_id=custodian)


AFTER = {"asset_custody": sync_custody}


# ---------------------------------------------------------------------------
# Link aliases
# ---------------------------------------------------------------------------
def _one(qs):
    hits = list(qs[:2])
    return hits[0] if len(hits) == 1 else None


def _user_by_staff_number(text):
    from staff.models import Staff
    return _one(Staff.objects.filter(employee_no__iexact=text, user__isnull=False).values_list("user_id", flat=True))


def _category_by_code(text):
    from assets.models import AssetCategory
    return _one(AssetCategory.objects.filter(code__iexact=text).values_list("pk", flat=True))


def _department_by_name(text):
    from staff.models import Department
    return _one(Department._base_manager.filter(name__iexact=text).values_list("pk", flat=True))


def _asset_by_serial(text):
    from assets.models import Asset
    if not text:
        return None
    return _one(Asset.all_objects.filter(serial_number__iexact=text).values_list("pk", flat=True))


ALIASES = {
    "accounts.User": _user_by_staff_number,
    "assets.AssetCategory": _category_by_code,
    "staff.Department": _department_by_name,
    "assets.Asset": _asset_by_serial,
}
