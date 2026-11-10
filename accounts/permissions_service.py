"""Stage 3 — one place that answers "what can this user do, and why?".

Sources of a user's permissions (all are Django `auth.Permission` rows):

  1. role baseline        Role.permissions                       (pre-Stage 3)
  2. role groups          Role.groups[*].permissions             (Stage 3)
  3. direct groups        User.groups[*].permissions             (Stage 3)
  4. direct permissions   User.user_permissions                  (Stage 3)
  5. grant overrides      UserPermissionOverride(effect=grant)   (pre-Stage 3)
  6. revoke overrides     UserPermissionOverride(effect=revoke)  (pre-Stage 3)
  7. office/seat groups  Staff.office.groups + Department.director_groups of the
                          department the user directs           (Phase B, B9/B2)

effective = (1 ∪ 2 ∪ 3 ∪ 4 ∪ 5 ∪ 7) − 6.
Overrides that have expired (`expires_at` passed) or were revoked
(`revoked_at` set, X31 director grants) no longer count.  Superusers hold every permission.
A revoke override always wins, whatever source granted the permission.

`accounts.backends.RolePermissionBackend` reads `effective_permissions`, so
every existing `user.has_perm(...)` / `{{ perms.app.codename }}` check sees
role- and group-derived permissions without any other code changing.

Caching: results are stored on the *user instance* (attribute
`_perm_sources_cache`). Django builds a fresh `request.user` for every
request, so the cache never outlives a request and is keyed to that user by
construction. Call `invalidate(user)` after changing assignments within the
same request (the views below do).
"""
from django.contrib.auth.models import Group, Permission
from django.db.models import Q

from .permissions import can_act_on_user as _can_act_on_user, rank_of

_CACHE_ATTR = "_perm_sources_cache"
_BACKEND_CACHE_ATTR = "_rbac_perm_cache"   # used by RolePermissionBackend
_FAST_CACHE_ATTR = "_perm_effective_cache"  # v2.2 fast path, per instance (= per request)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def perm_label(perm):
    """'app_label.codename' for a Permission instance."""
    return f"{perm.content_type.app_label}.{perm.codename}"


def _labels(qs):
    return {
        f"{app}.{code}"
        for app, code in qs.values_list("content_type__app_label", "codename")
    }


def invalidate(user):
    """Forget cached permission data on this user instance."""
    for attr in (_CACHE_ATTR, _BACKEND_CACHE_ATTR, _FAST_CACHE_ATTR, "_perm_cache", "_user_perm_cache",
                 "_group_perm_cache"):
        if hasattr(user, attr):
            try:
                delattr(user, attr)
            except AttributeError:
                pass


def permission_sources(user):
    """Dict of label sets per source (see module docstring). Cached per instance."""
    cached = getattr(user, _CACHE_ATTR, None)
    if cached is not None:
        return cached
    empty = {k: set() for k in ("role", "role_groups", "user_groups", "user_direct", "grants", "revokes",
                                "office_groups")}
    if user is None or not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        return empty
    src = dict(empty)
    if user.role_id:
        src["role"] = _labels(Permission.objects.filter(roles=user.role_id))
        src["role_groups"] = _labels(Permission.objects.filter(group__roles=user.role_id))
    src["user_groups"] = _labels(Permission.objects.filter(group__user=user))
    src["user_direct"] = _labels(user.user_permissions.all())
    from django.utils import timezone
    now = timezone.now()
    overrides = user.permission_overrides.filter(revoked_at__isnull=True).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    src["grants"] = _labels(Permission.objects.filter(
        pk__in=overrides.filter(effect="grant").values("permission")))
    src["revokes"] = _labels(Permission.objects.filter(
        pk__in=overrides.filter(effect="revoke").values("permission")))
    src["office_groups"] = _labels(Permission.objects.filter(
        group__offices__staff__user=user, group__offices__staff__is_deleted=False))
    # v2.2: offices are units in the Department list; their member groups apply
    # to everyone posted to the unit.
    src["office_groups"] |= _labels(Permission.objects.filter(
        group__member_of_departments__staff__user=user, group__member_of_departments__staff__is_deleted=False))
    # Phase B (B2): groups that come with a director seat (Department.director_groups).
    src["office_groups"] |= _labels(Permission.objects.filter(group__director_of_departments__director=user))
    setattr(user, _CACHE_ATTR, src)
    return src


# ---------------------------------------------------------------------------
# public resolver API
# ---------------------------------------------------------------------------
def effective_permissions(user) -> set:
    """Every permission label the user effectively holds."""
    if user is None or not getattr(user, "is_active", False) or getattr(user, "is_anonymous", True):
        return set()
    if user.is_superuser:
        return _labels(Permission.objects.all())
    s = getattr(user, _CACHE_ATTR, None)
    if s is None:
        # v2.2 fast path (every request runs this): the same sources as
        # permission_sources(), in ONE union query plus one for revokes,
        # instead of nine round-trips to the database.
        cached = getattr(user, _FAST_CACHE_ATTR, None)
        if cached is None:
            cached = _effective_fast(user)
            setattr(user, _FAST_CACHE_ATTR, cached)
        return set(cached)
    granted = (s["role"] | s["role_groups"] | s["user_groups"] | s["user_direct"] | s["grants"]
               | s.get("office_groups", set()))
    return granted - s["revokes"]


def _effective_fast(user) -> set:
    from django.utils import timezone

    cols = ("content_type__app_label", "codename")
    now = timezone.now()
    overrides = user.permission_overrides.filter(revoked_at__isnull=True).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    parts = [
        Permission.objects.filter(group__user=user),
        Permission.objects.filter(user=user),
        Permission.objects.filter(pk__in=overrides.filter(effect="grant").values("permission")),
        Permission.objects.filter(group__offices__staff__user=user, group__offices__staff__is_deleted=False),
        Permission.objects.filter(group__member_of_departments__staff__user=user,
                                  group__member_of_departments__staff__is_deleted=False),
        Permission.objects.filter(group__director_of_departments__director=user),
    ]
    if user.role_id:
        parts += [Permission.objects.filter(roles=user.role_id),
                  Permission.objects.filter(group__roles=user.role_id)]
    parts = [q.order_by().values_list(*cols) for q in parts]
    granted = {f"{app}.{code}" for app, code in parts[0].union(*parts[1:]).order_by()}
    revokes = _labels(Permission.objects.filter(pk__in=overrides.filter(effect="revoke").values("permission")))
    return granted - revokes


def inherited_permissions(user) -> set:
    """Only what the user's role gives them (role baseline + role groups)."""
    s = permission_sources(user)
    return (s["role"] | s["role_groups"]) - s["revokes"]


def direct_permissions(user) -> set:
    """Only what is assigned on the user: direct permissions + direct groups.
    (Grant overrides are a separate, older mechanism; see permission_sources.)"""
    s = permission_sources(user)
    return (s["user_direct"] | s["user_groups"]) - s["revokes"]


def office_permissions(user) -> set:
    """Only what the user's office posting gives them (B9)."""
    s = permission_sources(user)
    return s.get("office_groups", set()) - s["revokes"]


def effective_groups(user):
    """Groups assigned directly to the user, carried by their role, or by
    their office (B9)."""
    if user is None or not getattr(user, "pk", None):
        return Group.objects.none()
    q = (Q(user=user) | Q(offices__staff__user=user, offices__staff__is_deleted=False)
         | Q(member_of_departments__staff__user=user, member_of_departments__staff__is_deleted=False)
         | Q(director_of_departments__director=user))
    if user.role_id:
        q |= Q(roles=user.role_id)
    return Group.objects.filter(q).distinct().order_by("name")


def role_permissions(role) -> set:
    """Everything a role grants: its baseline plus every group it carries."""
    if role is None:
        return set()
    return (_labels(Permission.objects.filter(roles=role))
            | _labels(Permission.objects.filter(group__roles=role)))


def group_permissions(group) -> set:
    return _labels(group.permissions.all())


# ---------------------------------------------------------------------------
# rank / ownership rules (Stage 3 §1g) — enforced server-side by every view
# ---------------------------------------------------------------------------
def can_act_on_user(actor, target_user) -> bool:
    """Reuses the H3 helper unchanged (accounts.permissions.can_act_on_user)."""
    return _can_act_on_user(actor, target_user)


def can_act_on_role(actor, role) -> bool:
    """Same seniority rule for roles: you may not change a role ranked above you."""
    if actor is None or not getattr(actor, "is_authenticated", False):
        return False
    if actor.is_superuser:
        return True
    return (role.rank or 0) <= rank_of(actor)


def _may_assign(actor):
    return bool(actor and getattr(actor, "is_authenticated", False) and actor.is_active
                and (actor.is_superuser or actor.has_perm("accounts.assign_groups")))


def _target_ok(actor, target):
    from .models import Role
    if target is None:
        return True
    if isinstance(target, Role):
        return can_act_on_role(actor, target)
    return can_act_on_user(actor, target)


def can_assign_group(actor, target, group) -> bool:
    """May `actor` add/remove `group` on `target` (a User or a Role)?

    * actor needs accounts.assign_groups (superusers always may)
    * actor may not act on a target that outranks them
    * actor must already hold every permission the group grants
    """
    if not _may_assign(actor):
        return False
    if actor.is_superuser:
        return True
    if not _target_ok(actor, target):
        return False
    return group_permissions(group) <= effective_permissions(actor)


def can_assign_permission(actor, target, perm) -> bool:
    """May `actor` add/remove a direct permission on `target`? Same rules."""
    if not _may_assign(actor):
        return False
    if actor.is_superuser:
        return True
    if not _target_ok(actor, target):
        return False
    label = perm if isinstance(perm, str) else perm_label(perm)
    return label in effective_permissions(actor)


def can_edit_group_permission(actor, perm) -> bool:
    """When creating/editing a group, a non-superuser may only add or remove
    permissions they hold themselves (otherwise a group becomes a way to
    mint access the editor doesn't have)."""
    if actor is None or not getattr(actor, "is_authenticated", False):
        return False
    if actor.is_superuser:
        return True
    label = perm if isinstance(perm, str) else perm_label(perm)
    return label in effective_permissions(actor)


def group_outranks(actor, group) -> bool:
    """True if `group` is held by someone (directly or via a role) who
    outranks `actor` — editing or deleting it would act on that person."""
    if actor.is_superuser:
        return False
    mine = rank_of(actor)
    from .models import User
    if group.roles.filter(rank__gt=mine).exists():
        return True
    holders = User.objects.filter(groups=group).select_related("role")
    return any(u.is_superuser or rank_of(u) > mine for u in holders)
