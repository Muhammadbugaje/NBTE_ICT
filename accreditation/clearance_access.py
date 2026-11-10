"""Who may decide an onboarding departmental clearance (Phase 0: O10, T-SEC-7).

Departmental clearance is reserved for:

* the **director of the clearance department** — ``org.director_of`` (the
  ``Department.director`` seat, Phase B);
* a **ClearanceDelegate** the director (or the ES) named for that department;
* the **Executive Secretary**;
* superusers.

Officers no longer hold ``accreditation.decide_departmental_clearance``
(data migration ``accreditation.0009``). A delegate who does not hold the
permission through a role receives it as a ``UserPermissionOverride`` while
the delegation is active, because the onboarding flow's ``clear``/``return``
transitions still name that permission as their guard.
"""
# scoped-by-caller
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

DECIDE_PERM = "accreditation.decide_departmental_clearance"
DIRECTOR_ROLE = "director"
ES_ROLE = "executive-secretary"
OVERRIDE_REASON = "Clearance delegate"


def _role(user):
    import org
    return org.role_code(user)


def _is_staff(user):
    return bool(user is not None and getattr(user, "is_authenticated", False)
                and getattr(user, "user_type", "staff") == "staff" and user.is_active)


def department_directors(department):
    """Active staff who count as the director of ``department``."""
    import org

    if department is None:
        return []
    director = org.director_of(department)
    return [director] if director is not None and getattr(director, "user_type", "staff") == "staff" else []


def is_department_director(user, department):
    import org
    if not _is_staff(user) or department is None:
        return False
    return org.is_director(user, department)


def is_executive_secretary(user):
    import org
    return _is_staff(user) and org.is_es(user)


def live_delegations(department=None, user=None):
    from django.db.models import Q

    from .models import ClearanceDelegate

    qs = ClearanceDelegate.objects.filter(active=True).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())).select_related("department", "user")
    if department is not None:
        qs = qs.filter(department=department)
    if user is not None:
        qs = qs.filter(user=user)
    return qs


def is_clearance_delegate(user, department):
    if not _is_staff(user) or department is None:
        return False
    return live_delegations(department, user).exists()


def may_decide_for(user, department):
    """(allowed, reason) — the person rule only (state checks are the caller's)."""
    if not _is_staff(user):
        return False, "Only NBTE staff decide clearances."
    if user.is_superuser or is_executive_secretary(user):
        return True, ""
    if is_department_director(user, department) or is_clearance_delegate(user, department):
        return True, ""
    name = getattr(department, "name", "the department")
    return False, (f"Only the director of {name} (or someone the director delegated) can clear or return "
                   "this application.")


def deciders_for(department):
    """Directors and live delegates of ``department`` (for notifications)."""
    people = {u.pk: u for u in department_directors(department)}
    for d in live_delegations(department):
        if d.user.is_active:
            people.setdefault(d.user_id, d.user)
    return list(people.values())


def can_manage_delegates(user, department):
    return bool(_is_staff(user) and (user.is_superuser or is_executive_secretary(user)
                                     or is_department_director(user, department)))


def _decide_permission():
    from django.contrib.auth.models import Permission

    return Permission.objects.filter(content_type__app_label="accreditation",
                                     codename="decide_departmental_clearance").first()


def _sync_override(user, *, granted_by=None):
    """Keep the delegate's permission override in step with their delegations."""
    from accounts.models import User, UserPermissionOverride

    perm = _decide_permission()
    if perm is None:
        return
    override = UserPermissionOverride.objects.filter(user=user, permission=perm).first()
    if live_delegations(user=user).exists():
        fresh = User.objects.get(pk=user.pk)
        if override is None and not fresh.has_perm(DECIDE_PERM):
            UserPermissionOverride.objects.create(user=user, permission=perm, effect=UserPermissionOverride.GRANT,
                                                  reason=OVERRIDE_REASON, granted_by=granted_by)
    elif override is not None and override.effect == UserPermissionOverride.GRANT \
            and override.reason == OVERRIDE_REASON:
        override.delete()


@transaction.atomic
def add_delegate(department, user, *, granted_by, expires_at=None, note=""):
    from .models import ClearanceDelegate

    if not can_manage_delegates(granted_by, department):
        raise PermissionDenied("Only the director of the department or the Executive Secretary can delegate "
                               "clearance.")
    if not _is_staff(user):
        raise ValidationError("Choose an active NBTE staff member.")
    if user.department_id != department.pk:
        raise ValidationError(f"The delegate must work in {department.name}.")
    existing = ClearanceDelegate.objects.filter(department=department, user=user, active=True).first()
    if existing is not None:
        existing.expires_at, existing.note = expires_at, note or existing.note
        existing.save(update_fields=["expires_at", "note", "updated_at"])
        delegate = existing
    else:
        delegate = ClearanceDelegate.objects.create(department=department, user=user, granted_by=granted_by,
                                                    expires_at=expires_at, note=note)
    _sync_override(user, granted_by=granted_by)
    return delegate


@transaction.atomic
def revoke_delegate(delegate, *, by):
    if not can_manage_delegates(by, delegate.department):
        raise PermissionDenied("Only the director of the department or the Executive Secretary can revoke a "
                               "delegation.")
    delegate.active = False
    delegate.revoked_at, delegate.revoked_by = timezone.now(), by
    delegate.save(update_fields=["active", "revoked_at", "revoked_by", "updated_at"])
    _sync_override(delegate.user)
    return delegate
