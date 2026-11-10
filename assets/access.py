"""Who may see which assets.

Single source of truth, used by the assets views, labels, exports, the scan
page, global search, the dashboards/action queue, the workflow asset picker
and the attachment download check, so the rules can never drift apart.

    Super admin ............ every asset, automatically (superuser flag or the
                             `super-admin` role).
    All assets ............. needs `assets.view_all_assets`. Seeded on the
                             Executive Secretary role; grant it to anyone else
                             (HR head, ICT head, ...) from the permissions
                             screens.
    Own department ......... a director / deputy director / office head, or
                             anyone holding `assets.view_department_assets`,
                             sees the assets of their OWN department. An asset
                             "belongs to" a department when
                               - its `owner_department` is that department, or
                               - it is currently held (custodian / open
                                 assignment) by someone in that department.
    Whole department ....... (K1) an asset assigned to a department is seen by
                             every member of that department.
    Temporary grant ........ (X5) a live AccessGrant of scope "asset" (the
                             assignee of an open ticket on the asset).
    Everyone else .......... only the assets currently assigned to them
                             personally (they are the custodian or the holder
                             on the open assignment). Not other assets of their
                             department, and not assets they handed back.

Reading and writing are separate: seeing an asset never lets you edit it.
Editing still needs `assets.change_asset` (see `can_manage_asset`).

This deliberately does NOT use `accounts.access_all_departments`: every
director role carries it, so it made every director see every department.
"""
from __future__ import annotations

from django.db.models import Exists, OuterRef, Q

from accounts.access import is_department_lead, is_super_admin

PERM_ALL = "assets.view_all_assets"
PERM_DEPARTMENT = "assets.view_department_assets"
PERM_CHANGE = "assets.change_asset"


def can_view_all_assets(user):
    if not getattr(user, "is_authenticated", False):
        return False
    return is_super_admin(user) or user.has_perm(PERM_ALL)


def is_department_asset_viewer(user):
    """Director / deputy / office head of a department, or an explicit holder
    of `view_department_assets` who belongs to a department."""
    if not getattr(user, "is_authenticated", False) or not user.department_id:
        return False
    return is_department_lead(user) or user.has_perm(PERM_DEPARTMENT)


def asset_scope(user):
    """"all", "department" or "own" — what the user may see."""
    if can_view_all_assets(user):
        return "all"
    if is_department_asset_viewer(user):
        return "department"
    return "own"


def _base():
    from .models import Asset
    return Asset.objects.select_related(
        "category", "owner_department", "current_custodian",
    )


def _native_q(user):
    """The Q of assets the user sees by their own rights (no grants)."""
    from .models import AssetAssignment

    held_by_me = Exists(
        AssetAssignment.objects.filter(
            asset=OuterRef("pk"), assigned_to=user, returned_at__isnull=True,
        )
    )
    q = Q(current_custodian=user) | Q(held_by_me)
    if user.department_id:
        # K1: an asset assigned to the whole department is seen by all its members.
        q |= Q(Exists(AssetAssignment.objects.filter(
            asset=OuterRef("pk"), returned_at__isnull=True, assigned_to__isnull=True,
            department_id=user.department_id)))

    if is_department_asset_viewer(user):
        dept_id = user.department_id
        held_in_dept = Exists(
            AssetAssignment.objects.filter(
                asset=OuterRef("pk"), returned_at__isnull=True,
                assigned_to__department_id=dept_id,
            )
        )
        q |= (
            Q(owner_department_id=dept_id)
            | Q(current_custodian__department_id=dept_id)
            | Q(held_in_dept)
        )
    return q


def native_assets_queryset(user):
    """Assets the user may see by their own rights (no temporary grants)."""
    from .models import Asset

    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return Asset.objects.none()
    base = _base()
    if can_view_all_assets(user):
        return base
    return base.filter(_native_q(user))


def visible_assets_queryset(user):
    """Every asset this user is permitted to see: their own rights plus live
    AccessGrants of scope ``asset`` (X5: a ticket assignee reads the linked
    asset while the ticket is open). Fails closed: anonymous or inactive
    users get nothing."""
    from .models import Asset

    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return Asset.objects.none()
    base = _base()
    if can_view_all_assets(user):
        return base
    q = _native_q(user)
    granted = granted_asset_ids(user)
    if granted:
        q |= Q(pk__in=granted)
    return base.filter(q)


def granted_asset_ids(user):
    try:
        from assignments.grants import granted_ids
        from .models import Asset
        return [int(x) for x in granted_ids(user, Asset, "asset") if str(x).isdigit()]
    except Exception:
        return []


def can_view_asset_natively(user, asset):
    if asset is None or not getattr(user, "is_authenticated", False):
        return False
    return native_assets_queryset(user).filter(pk=asset.pk).exists()


def can_view_asset(user, asset):
    """Object-level check. Use for any code that already has an Asset."""
    if asset is None or not getattr(user, "is_authenticated", False):
        return False
    return visible_assets_queryset(user).filter(pk=asset.pk).exists()


def can_manage_asset(user, asset):
    """Write access (edit / assign / return / change state / attach).

    Needs the real `assets.change_asset` permission AND the right to see the
    asset. On top of that, only holders of "view all" may touch assets outside
    their own department; everyone else only their own department's.
    Super admin always may.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if is_super_admin(user):
        return True
    if not user.has_perm(PERM_CHANGE):
        return False
    if not can_view_asset_natively(user, asset):  # a temporary grant never gives write access
        return False
    if can_view_all_assets(user):
        return True
    return bool(user.department_id) and asset.owner_department_id == user.department_id


def can_export_assets(user):
    """May download the whole asset register as a Data Hub workbook (assets list page).

    Only people who already see AND may edit every asset: super admin, or
    `assets.view_all_assets` together with `assets.change_asset`. A department-
    scoped manager does not get it: the workbook holds every department's assets.
    Importing is not covered here: it stays in the Data Hub (superusers only)."""
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    if is_super_admin(user):
        return True
    return can_view_all_assets(user) and user.has_perm(PERM_CHANGE)


def can_import_assets(user):
    """The import itself runs in the Data Hub, which is for superusers only."""
    from core.datahub.views import can_use_hub
    return can_use_hub(user)


def can_view_all_departments_filter(user):
    """May the list page offer the department filter?"""
    return asset_scope(user) == "all"
