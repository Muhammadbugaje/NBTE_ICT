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
@transaction.atomic
def assign_asset(*, asset, to_user, by_user, condition="good",
                 location="", notes="", expected_return_at=None):
    """Open a new AssetAssignment for `asset` and set the current custodian.

    Refuses if the asset is already held by someone else, or if it's not in
    a state that permits hand-off.
    """
    if asset.state not in ("new", "in_service"):
        raise ValidationError(
            f"Asset {asset.asset_tag} is in state '{asset.get_state_display()}' "
            f"and cannot be assigned."
        )
    if asset.open_assignment() is not None:
        current = asset.open_assignment()
        raise ValidationError(
            f"Asset {asset.asset_tag} is already assigned to "
            f"{current.assigned_to.get_full_name() or current.assigned_to.email}."
        )

    assignment = AssetAssignment.objects.create(
        asset=asset,
        assigned_to=to_user,
        assigned_by=by_user,
        condition_on_assign=condition,
        location_on_assign=location,
        notes=notes,
    )
    asset.current_custodian = to_user
    asset.save(update_fields=["current_custodian", "updated_at"])
    audit.log("assign", asset, actor=by_user,
              changes={"current_custodian_id": {"new": to_user.pk}})
    return assignment


@transaction.atomic
def return_asset(*, asset, by_user, condition_on_return="good", notes=""):
    """Close the open assignment and clear the current custodian."""
    assignment = asset.open_assignment()
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
    if condition_on_return in ("poor",):
        new_state = "damaged"
    elif condition_on_return == "damaged":
        new_state = "damaged"

    asset.current_custodian = None
    asset.state = new_state
    asset.save(update_fields=["current_custodian", "state", "updated_at"])
    audit.log("return", asset, actor=by_user,
              changes={"current_custodian_id": {"old": assignment.assigned_to_id, "new": None},
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
    if issue.asset.state not in ("retired", "lost", "disposed"):
        issue.asset.state = restore_state
        issue.asset.save(update_fields=["state", "updated_at"])
    return issue