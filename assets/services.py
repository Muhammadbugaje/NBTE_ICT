"""Asset service layer.

The single entry point for all asset operations. Ensures the denormalized
`current_custodian` on `Asset` stays in sync with the open `AssetAssignment`
row, and that every action is audited.
"""
import logging
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core import audit

from .models import Asset, AssetAssignment, AssetIssue

logger = logging.getLogger(__name__)


# ===========================================================================
# Asset tag generation
# ===========================================================================
def _unique_asset_tag(category, department=None):
    """Return a unique tag like `ICT-PC-00042`.

    Scheme: `{DEPT}-{CATEGORY}-{5-digit-sequence}`. The sequence is per
    prefix, and includes soft-deleted rows so deleting an asset doesn't
    cause a collision on the next allocation.
    """
    dept_code = (department.code.upper() if department else "NBTE")[:6]
    cat_code = (category.code.upper() if category else "ASSET")[:6]
    prefix = f"{dept_code}-{cat_code}-"

    last = (
        Asset.all_objects
        .select_for_update()
        .filter(asset_tag__startswith=prefix)
        .order_by("-asset_tag")
        .first()
    )
    seq = 1
    if last:
        try:
            seq = int(last.asset_tag.rsplit("-", 1)[1]) + 1
        except (IndexError, ValueError):
            pass
    return f"{prefix}{seq:05d}"


# ===========================================================================
# Create
# ===========================================================================
@transaction.atomic
def create_asset(*, category, created_by=None, owner_department=None,
                 asset_tag=None, **fields):
    """Create an asset with an auto-generated tag if one isn't supplied.

    Retries the tag allocation a few times in case of race conditions.
    """
    for attempt in range(5):
        try:
            with transaction.atomic():
                tag = asset_tag or _unique_asset_tag(category, owner_department)
                asset = Asset.objects.create(
                    asset_tag=tag,
                    category=category,
                    owner_department=owner_department,
                    **fields,
                )
            audit.log("create", asset, actor=created_by)
            return asset
        except IntegrityError:
            if asset_tag:
                raise
            continue
    raise ValidationError("Could not allocate a unique asset tag. Please try again.")


# ===========================================================================
# Assign / Return
# ===========================================================================
def _sync_custodian(asset):
    """``current_custodian`` mirrors the one open individual hand-off; shared
    and department assets have no single custodian (their holders are the
    open AssetAssignment rows)."""
    custodian = None
    if asset.assignment_mode == Asset.MODE_INDIVIDUAL:
        row = asset.assignments.filter(returned_at__isnull=True, assigned_to__isnull=False).first()
        custodian = row.assigned_to if row else None
    if asset.current_custodian_id != (custodian.pk if custodian else None):
        asset.current_custodian = custodian
        asset.save(update_fields=["current_custodian", "updated_at"])


@transaction.atomic
def set_assignment_mode(*, asset, mode, by_user):
    """K1/K2: switch how the asset is held. Only while nobody holds it, so
    the custody ledger never mixes modes."""
    if mode not in dict(Asset.ASSIGNMENT_MODE_CHOICES):
        raise ValidationError("Unknown assignment mode.")
    if mode == asset.assignment_mode:
        return asset
    if asset.open_assignments().exists():
        raise ValidationError("Return the asset from every holder before changing how it is held.")
    old = asset.assignment_mode
    asset.assignment_mode = mode
    asset.save(update_fields=["assignment_mode", "updated_at"])
    audit.log("assignment_mode", asset, actor=by_user, changes={"assignment_mode": {"old": old, "new": mode}})
    return asset


@transaction.atomic
def assign_asset(*, asset, to_user=None, by_user, condition="good",
                 location="", notes="", expected_return_at=None, department=None):
    """Open a new AssetAssignment.

    * individual mode: one holder; refused while someone holds it;
    * shared mode (K2): several open rows, one per person; the same person twice is refused;
    * department mode (K1): one open row with ``department`` and no person.
    """
    if asset.state not in ("new", "in_service"):
        raise ValidationError(
            f"Asset {asset.asset_tag} is in state '{asset.get_state_display()}' "
            f"and cannot be assigned."
        )
    mode = asset.assignment_mode
    open_rows = asset.open_assignments()
    if mode == Asset.MODE_DEPARTMENT:
        department = department or (getattr(to_user, "department", None) if to_user is not None else None)
        if department is None:
            raise ValidationError("Choose the department this asset is assigned to.")
        if open_rows.exists():
            raise ValidationError(f"Asset {asset.asset_tag} is already assigned to "
                                  f"{open_rows.first().holder_label}. Return it first.")
        to_user = None
    elif to_user is None:
        raise ValidationError("Choose the person who receives the asset.")
    elif mode == Asset.MODE_SHARED:
        if open_rows.filter(assigned_to=to_user).exists():
            raise ValidationError(f"{to_user.get_full_name() or to_user.email} already shares this asset.")
        department = None
    else:
        current = open_rows.first()
        if current is not None:
            raise ValidationError(f"Asset {asset.asset_tag} is already assigned to {current.holder_label}.")
        department = None

    assignment = AssetAssignment.objects.create(
        asset=asset,
        assigned_to=to_user,
        department=department,
        assigned_by=by_user,
        expected_return_at=expected_return_at,
        condition_on_assign=condition,
        location_on_assign=location,
        notes=notes,
    )
    _sync_custodian(asset)
    audit.log("assign", asset, actor=by_user,
              changes={"holder": {"new": to_user.pk if to_user else f"department:{department.pk}"},
                       "mode": mode})
    return assignment


@transaction.atomic
def return_asset(*, asset, by_user, condition_on_return="good", notes="", assignment=None):
    """Close one open assignment (``assignment``; default: the only / first
    open one). Returning one holder's share of a shared asset leaves the
    other holders' shares open (K2)."""
    if assignment is None:
        assignment = asset.open_assignment()
    elif assignment.asset_id != asset.pk or assignment.returned_at is not None:
        raise ValidationError("That hand-off is not open on this asset.")
    if assignment is None:
        raise ValidationError(f"Asset {asset.asset_tag} is not currently assigned.")

    assignment.returned_at = timezone.now()
    assignment.returned_to = by_user
    assignment.condition_on_return = condition_on_return
    if notes:
        assignment.notes = (assignment.notes + "\n" + notes).strip()
    assignment.save(update_fields=["returned_at", "returned_to",
                                   "condition_on_return", "notes", "updated_at"])

    # Update asset state based on condition
    new_state = asset.state
    if condition_on_return in ("poor", "damaged"):
        new_state = "damaged"
    if new_state != asset.state:
        asset.state = new_state
        asset.save(update_fields=["state", "updated_at"])
    _sync_custodian(asset)
    audit.log("return", asset, actor=by_user,
              changes={"holder": {"old": assignment.assigned_to_id or f"department:{assignment.department_id}",
                                  "new": None},
                       "state": {"new": new_state}})
    return assignment


# ===========================================================================
# State transitions
# ===========================================================================
@transaction.atomic
def set_state(*, asset, new_state, by_user, reason=""):
    """Move an asset between lifecycle states. Writes an audit entry."""
    if new_state not in dict(asset._meta.get_field("state").choices):
        raise ValidationError(f"Unknown state: {new_state}")
    old = asset.state
    asset.state = new_state
    asset.save(update_fields=["state", "updated_at"])
    audit.log("state_change", asset, actor=by_user,
              changes={"state": {"old": old, "new": new_state}},
              note=reason)
    return asset


# ===========================================================================
# Issues
# ===========================================================================
@transaction.atomic
def report_issue(*, asset, reported_by, summary, description="", request=None):
    issue = AssetIssue.objects.create(
        asset=asset,
        reported_by=reported_by,
        summary=summary.strip(),
        description=description.strip(),
        request=request,
    )
    # If the asset was in service, flag it as damaged pending review.
    if asset.state == "in_service":
        asset.state = "damaged"
        asset.save(update_fields=["state", "updated_at"])
    return issue


@transaction.atomic
def resolve_issue(*, issue, resolved_by, resolution_notes="", restore_state="in_service"):
    if issue.resolved_at is not None:
        raise ValidationError("This issue is already resolved.")
    issue.resolved_at = timezone.now()
    issue.resolved_by = resolved_by
    issue.resolution_notes = resolution_notes.strip()
    issue.save(update_fields=["resolved_at", "resolved_by",
                              "resolution_notes", "updated_at"])

    # Restore the asset to a working state unless it was retired/lost.
    # Review M16: restore_state=None closes the issue but leaves the asset's
    # state alone (used when the linked request was rejected or cancelled -
    # nothing was repaired).
    if restore_state and issue.asset.state not in ("retired", "lost", "disposed"):
        issue.asset.state = restore_state
        issue.asset.save(update_fields=["state", "updated_at"])
    return issue