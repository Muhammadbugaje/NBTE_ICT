"""Organisation rules — the one place that answers "who is the director?",
"who heads this?", "may X give Y this role / permission?" (Master Plan v2.1
§4.3-4.6, §7.3-7.5, §7.12; spec G6 X31).

Every function is importable as ``org.<name>`` (``org/__init__`` re-exports this
module). The public API is documented in docs/master_plan_v2_1/notes/org_api.md;
keep signatures stable — other modules and the HR work build on them.

Models are imported inside functions so ``import org`` is safe at any time.
"""
from __future__ import annotations

import datetime
import re

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

__all__ = [
    "OrgRuleError",
    # identity / roles
    "role_code", "role_is", "has_role", "role_q", "users_with_role", "is_es", "is_super_admin", "rank_of",
    "grade_of",
    "DEPARTMENT_LEAD_ROLES", "DIRECTOR_ROLE", "DEPUTY_ROLE", "DIVISION_HEAD_ROLE", "ES_ROLE",
    # departments and directors
    "department", "director_of", "is_director", "directed_department", "deputies_of", "heads_of",
    "is_department_lead", "assign_director", "vacate_director", "can_manage_directors",
    # divisions
    "division_of", "can_manage_divisions", "create_division", "update_division", "set_division_head",
    "move_to_division", "division_open_task_count", "division_has_capacity", "DIVISION_HEAD_GROUP",
    # offices
    "office_of", "set_office_groups", "can_manage_offices",
    # grades and roles
    "grade_for_text", "parse_grade_number", "grade_rules_enabled", "role_problems", "assign_role",
    "grantable_roles",
    # permission grants (X31)
    "grantable_permissions", "grant_permission", "revoke_permission", "active_grants",
    "expire_permission_grants", "can_manage_access",
]

DIRECTOR_ROLE = "director"
DEPUTY_ROLE = "deputy-director"
DIVISION_HEAD_ROLE = "division-head"
ES_ROLE = "executive-secretary"
SUPER_ADMIN_ROLE = "super-admin"
OFFICE_HEAD_PREFIX = "office-head"
DEPARTMENT_LEAD_ROLES = (DIRECTOR_ROLE, DEPUTY_ROLE,
                         "office-head-admin", "office-head-ts", "office-head-dev")
DIVISION_HEAD_GROUP = "Division Head"


class OrgRuleError(PermissionDenied):
    """An organisation rule refused the action; ``str(exc)`` is the reason
    to show the user."""


def _audit(action, instance, actor, changes=None, note=""):
    from core import audit
    audit.log(action, instance, actor=actor, changes=changes or {}, note=note or "")


def _invalidate(user):
    if user is None:
        return
    from accounts.permissions_service import invalidate
    invalidate(user)


# ===========================================================================
# Identity and roles
# ===========================================================================
def role_code(user) -> str:
    """The user's role code, or ''."""
    if user is None or not getattr(user, "role_id", None):
        return ""
    return getattr(user.role, "code", "") or ""


def role_is(role, code) -> bool:
    """True when a Role instance has the supplied code."""
    return bool(role is not None and getattr(role, "code", "") == code)


def has_role(user, *codes, prefix=None) -> bool:
    """True when the user's role code is one of ``codes`` (or starts with ``prefix``)."""
    code = role_code(user)
    if not code:
        return False
    return code in codes or bool(prefix and code.startswith(prefix))


def role_q(*codes, prefix=None, path="role__code"):
    """A Q matching users whose role is one of ``codes`` (or starts with ``prefix``).
    ``path`` lets you filter from another model, e.g. ``path="user__role__code"``."""
    q = Q(**{f"{path}__in": list(codes)}) if codes else Q(pk__in=[])
    if prefix:
        q |= Q(**{f"{path}__startswith": prefix})
    return q


def users_with_role(*codes, department=None, prefix=None, active=True, staff_only=False):
    """Users holding one of the roles, optionally in a department (account or
    staff-record department)."""
    from accounts.models import User
    qs = User.objects.filter(role_q(*codes, prefix=prefix))
    if active:
        qs = qs.filter(is_active=True)
    if staff_only:
        qs = qs.filter(user_type=User.STAFF)
    if department is not None:
        dept = _dept(department)
        if dept is None:
            return qs.none()
        qs = qs.filter(Q(department=dept) | Q(staff_profile__department=dept)).distinct()
    return qs


def is_super_admin(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(user.is_superuser or has_role(user, SUPER_ADMIN_ROLE))


def is_es(user) -> bool:
    """The Executive Secretary (role ``executive-secretary``)."""
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "is_active", False)
                and has_role(user, ES_ROLE))


def rank_of(user) -> int:
    """Seniority used for every rank comparison: superuser 1000, otherwise the
    role rank (ES 999, director 100, deputy 80 ...). Grade levels are a
    separate scale, see ``grade_of``."""
    from accounts.permissions import rank_of as _rank_of
    return _rank_of(user)


def _staff(user):
    if user is None or not getattr(user, "pk", None):
        return None
    try:
        return user.staff_profile
    except Exception:
        return None


def grade_of(user):
    """The user's GradeLevel (or None)."""
    staff = _staff(user)
    if staff is None or not staff.grade_id:
        return None
    return staff.grade


# ===========================================================================
# Departments and the one director
# ===========================================================================
def _dept(value):
    """Department from an instance, pk or code."""
    from staff.models import Department
    if value is None or value == "":
        return None
    if isinstance(value, Department):
        return value
    if isinstance(value, int):
        return Department.objects.filter(pk=value).first()
    return Department.objects.filter(code=str(value).strip()).first()


department = _dept


def director_of(dept):
    """THE director of a department (Department.director), or None.
    ``dept`` may be a Department, its pk or its code. An inactive account is
    treated as an empty seat."""
    d = _dept(dept)
    if d is None:
        return None
    from accounts.models import User
    # Read the seat from the database: a Department instance held by the
    # caller may be older than the last assign / vacate.
    return User.objects.filter(directs__pk=d.pk, is_active=True).select_related("role").first()


def directed_department(user):
    """The department this user directs, or None. Remembered for the life of
    one web request on this thread (the rotation page asks three times, the
    seat cache is cleared on the same events), always fresh outside a request."""
    if user is None or not getattr(user, "pk", None):
        return None
    from staff.models import Department
    if not getattr(_SEATS, "in_request", False):
        return Department.objects.filter(director_id=user.pk).first()
    memo = getattr(_SEATS, "directed", None)
    if memo is None:
        memo = _SEATS.directed = {}
    if user.pk in memo:
        return memo[user.pk]
    d = Department.objects.filter(director_id=user.pk).first()
    memo[user.pk] = d
    return d


import threading as _threading

_SEATS = _threading.local()


def _seat_map():
    """{"by_pk": {pk: director_id}, "by_code": {code: pk}} for every department,
    in ONE query. v2.2: remembered only for the life of one web request on
    this thread (cleared when a request starts and ends, and on any
    Department save/delete). Outside a request (Celery, shell, tests calling
    services directly) it is read fresh every time."""
    seats = getattr(_SEATS, "map", None)
    if seats is not None:
        return seats
    from staff.models import Department

    by_pk, by_code = {}, {}
    for pk, code, director_id in Department.objects.values_list("pk", "code", "director_id"):
        by_pk[pk] = director_id
        if code:
            by_code[code] = pk
    seats = {"by_pk": by_pk, "by_code": by_code}
    if getattr(_SEATS, "in_request", False):
        _SEATS.map = seats
    return seats


def clear_seat_cache(*_args, **_kwargs):
    _SEATS.map = None
    _SEATS.directed = None


def seat_request_started(*_args, **_kwargs):
    _SEATS.map = None
    _SEATS.directed = None
    _SEATS.in_request = True


def seat_request_finished(*_args, **_kwargs):
    _SEATS.map = None
    _SEATS.directed = None
    _SEATS.in_request = False


def is_director(user, dept=None) -> bool:
    """True when ``user`` sits in the director seat of ``dept`` (any department
    when ``dept`` is None)."""
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    from staff.models import Department
    seats = _seat_map()
    if dept is None:
        return user.pk in seats["by_pk"].values()
    if isinstance(dept, Department):
        pk = dept.pk
    elif isinstance(dept, int):
        pk = dept
    else:
        pk = seats["by_code"].get(str(dept).strip())
    return bool(pk is not None and seats["by_pk"].get(pk) == user.pk)


def deputies_of(dept):
    """Deputy directors of a department (role ``deputy-director``), senior first."""
    d = _dept(dept)
    if d is None:
        return []
    return list(users_with_role(DEPUTY_ROLE, department=d, staff_only=True)
                .order_by("-staff_profile__designation__rank", "pk"))


def heads_of(dept):
    """Everyone who leads in a department, most senior first: the director,
    deputies, division heads (Division.head) and office heads."""
    d = _dept(dept)
    if d is None:
        return []
    from accounts.models import User
    out, seen = [], set()

    def add(u):
        if u is not None and u.pk not in seen and u.is_active:
            seen.add(u.pk)
            out.append(u)

    add(director_of(d))
    for u in deputies_of(d):
        add(u)
    for u in User.objects.filter(headed_divisions__department=d, headed_divisions__is_active=True,
                                 is_active=True).distinct().order_by("pk"):
        add(u)
    for u in users_with_role(prefix=OFFICE_HEAD_PREFIX, department=d).order_by("pk"):
        add(u)
    return out


def is_department_lead(user, dept=None) -> bool:
    """Director seat, or a lead role (deputy, office head) in a department."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if is_director(user, dept):
        return True
    if not has_role(user, *DEPARTMENT_LEAD_ROLES):
        return False
    if dept is None:
        return bool(user.department_id)
    d = _dept(dept)
    return bool(d is not None and user.department_id == d.pk)


def can_manage_directors(actor) -> bool:
    """ES, super admin, or a holder of ``org.assign_director`` (e.g. HR director)."""
    return bool(actor and getattr(actor, "is_authenticated", False) and actor.is_active
                and (is_super_admin(actor) or is_es(actor) or actor.has_perm("org.assign_director")))


@transaction.atomic
def assign_director(dept, user, actor, reason=""):
    """Put ``user`` in the director seat of ``dept``. Refused while the seat is
    filled ("Vacate the current director first") or when the person already
    directs another department. The person gets the ``director`` role if they
    hold a lower one. Audited."""
    from staff.models import Department
    d = _dept(dept)
    if d is None:
        raise OrgRuleError("Department not found.")
    if actor is not None and not can_manage_directors(actor):
        raise OrgRuleError("You cannot assign directors.")
    d = Department.objects.select_for_update().get(pk=d.pk)
    if d.director_id:
        if d.director_id == user.pk:
            return d
        raise OrgRuleError("Vacate the current director first.")
    if getattr(user, "user_type", "staff") != "staff" or not user.is_active:
        raise OrgRuleError("Only an active NBTE staff account can be a director.")
    other = Department.objects.filter(director_id=user.pk).exclude(pk=d.pk).first()
    if other is not None:
        raise OrgRuleError(f"{user.get_full_name() or user.email} already directs {other.name}.")
    staff = _staff(user)
    in_dept = user.department_id == d.pk or (staff is not None and staff.department_id == d.pk)
    if not in_dept:
        raise OrgRuleError("Transfer the person to this department first.")
    from . import single_holder
    taken = single_holder.conflict(user, DIRECTOR_ROLE, d)
    if taken:  # another account still holds the director role here (Part 1a)
        raise OrgRuleError(taken)
    d.director = user
    d.save(update_fields=["director", "updated_at"])
    if user.department_id != d.pk:
        # v2.2: the seat decides the director's department everywhere (queues,
        # need-to-know scopes, finance), so the account follows the seat.
        user.department = d
        user._org_skip_seat = True
        user.save(update_fields=["department"])
        _invalidate(user)
    if not has_role(user, DIRECTOR_ROLE, ES_ROLE):
        from accounts.models import Role
        role = Role.objects.filter(code=DIRECTOR_ROLE).first()
        if role is not None:
            old = role_code(user)
            user.role = role
            user._org_skip_seat = True
            user.save(update_fields=["role"])
            _audit("role_assigned", user, actor, {"role": {"old": old, "new": role.code}},
                   note="Director seat assigned")
            _invalidate(user)
    _audit("director_assigned", d, actor, {"director": {"old": None, "new": user.email}}, note=reason)
    return d


@transaction.atomic
def vacate_director(dept, actor, reason="", *, new_role=None):
    """Empty the director seat (audited). ``new_role`` (a Role or code)
    optionally changes the former director's role, e.g. ``"deputy-director"``.
    Otherwise they keep their role but no longer count as the director."""
    from staff.models import Department
    d = _dept(dept)
    if d is None:
        raise OrgRuleError("Department not found.")
    if actor is not None and not can_manage_directors(actor):
        raise OrgRuleError("You cannot vacate director seats.")
    if not (reason or "").strip():
        raise OrgRuleError("Give a reason for vacating the seat.")
    d = Department.objects.select_for_update().get(pk=d.pk)
    former = d.director
    if former is None:
        return d
    d.director = None
    d.save(update_fields=["director", "updated_at"])
    if new_role:
        from accounts.models import Role
        role = new_role if isinstance(new_role, Role) else Role.objects.filter(code=new_role).first()
        if role is not None:
            old = role_code(former)
            former.role = role
            former.save(update_fields=["role"])
            _audit("role_assigned", former, actor, {"role": {"old": old, "new": role.code}},
                   note="Director seat vacated")
            _invalidate(former)
    _audit("director_vacated", d, actor, {"director": {"old": former.email, "new": None}}, note=reason)
    return d


# ===========================================================================
# Divisions (B6-B8)
# ===========================================================================
def division_of(user):
    """The Division the user's staff record belongs to, or None."""
    staff = _staff(user)
    if staff is None or not staff.division_id:
        return None
    return staff.division


def can_manage_divisions(actor, dept) -> bool:
    """The director of ``dept``, the ES, super admin, or ``org.manage_all_divisions``."""
    if actor is None or not getattr(actor, "is_authenticated", False) or not actor.is_active:
        return False
    return bool(is_super_admin(actor) or is_es(actor) or actor.has_perm("org.manage_all_divisions")
                or is_director(actor, dept))


def _member_of_dept(user, dept):
    staff = _staff(user)
    return bool(user is not None and (user.department_id == dept.pk
                                      or (staff is not None and staff.department_id == dept.pk)))


def _division_group():
    from django.contrib.auth.models import Group
    group, _ = Group.objects.get_or_create(name=DIVISION_HEAD_GROUP)
    return group


@transaction.atomic
def create_division(dept, name, actor, *, code="", max_open_tasks=10, head=None):
    from .models import Division
    d = _dept(dept)
    if d is None:
        raise OrgRuleError("Department not found.")
    if actor is not None and not can_manage_divisions(actor, d):
        raise OrgRuleError("Only the director of this department can create its divisions.")
    name = (name or "").strip()
    if not name:
        raise OrgRuleError("Give the division a name.")
    if Division.objects.filter(department=d, name__iexact=name).exists():
        raise OrgRuleError("This department already has a division with that name.")
    div = Division.objects.create(department=d, name=name, code=code or "",
                                  max_open_tasks=max(0, int(max_open_tasks or 0)))
    _audit("division_created", div, actor, {"department": d.code, "name": name})
    if head is not None:
        set_division_head(div, head, actor)
    return div


@transaction.atomic
def update_division(division, actor, *, name=None, max_open_tasks=None, is_active=None):
    if actor is not None and not can_manage_divisions(actor, division.department):
        raise OrgRuleError("Only the director of this department can change its divisions.")
    changes = {}
    if name is not None and name.strip() and name.strip() != division.name:
        from .models import Division
        if Division.objects.filter(department=division.department, name__iexact=name.strip()) \
                .exclude(pk=division.pk).exists():
            raise OrgRuleError("This department already has a division with that name.")
        changes["name"] = {"old": division.name, "new": name.strip()}
        division.name = name.strip()
    if max_open_tasks is not None and int(max_open_tasks) != division.max_open_tasks:
        changes["max_open_tasks"] = {"old": division.max_open_tasks, "new": int(max_open_tasks)}
        division.max_open_tasks = max(0, int(max_open_tasks))
    if is_active is not None and bool(is_active) != division.is_active:
        changes["is_active"] = {"old": division.is_active, "new": bool(is_active)}
        division.is_active = bool(is_active)
        if not division.is_active and division.head_id:
            set_division_head(division, None, actor)
    if changes:
        division.save()
        _audit("division_updated", division, actor, changes)
    return division


def _sync_division_head_group(user):
    """The user is in the Division Head group exactly while they head an
    active division (B7)."""
    if user is None:
        return
    from .models import Division
    group = _division_group()
    heads = Division.objects.filter(head=user, is_active=True).exists()
    if heads:
        user.groups.add(group)
    else:
        user.groups.remove(group)
    _invalidate(user)


@transaction.atomic
def set_division_head(division, user, actor):
    """Appoint (or with ``user=None`` remove) the head. The head must be staff
    of the division's department. Grants / revokes the Division Head group."""
    dept = division.department
    if actor is not None and not can_manage_divisions(actor, dept):
        raise OrgRuleError("Only the director of this department can appoint division heads.")
    if user is not None:
        if not user.is_active or getattr(user, "user_type", "staff") != "staff":
            raise OrgRuleError("Only an active NBTE staff account can head a division.")
        if not _member_of_dept(user, dept):
            raise OrgRuleError("The head must be a member of this department.")
        if not division.is_active:
            raise OrgRuleError("Reactivate the division first.")
    old = division.head
    if (old.pk if old else None) == (user.pk if user else None):
        return division
    division.head = user
    division.save(update_fields=["head", "updated_at"])
    if user is not None:
        staff = _staff(user)
        if staff is not None and staff.division_id != division.pk:
            staff.division = division
            staff.save(update_fields=["division"])
    _sync_division_head_group(old)
    _sync_division_head_group(user)
    _audit("division_head_set", division, actor,
           {"head": {"old": old.email if old else None, "new": user.email if user else None}})
    return division


@transaction.atomic
def move_to_division(staff, division, actor):
    """Put a staff record in a division of their department (None = no division)."""
    if division is not None:
        if staff.department_id != division.department_id:
            raise OrgRuleError("The person is not in this division's department.")
        dept = division.department
    else:
        dept = staff.department
    if actor is not None and (dept is None or not can_manage_divisions(actor, dept)):
        raise OrgRuleError("Only the director of this department can move people between divisions.")
    old = staff.division
    if (old.pk if old else None) == (division.pk if division else None):
        return staff
    staff.division = division
    staff.save(update_fields=["division"])
    if old is not None and old.head_id and old.head_id == staff.user_id:
        set_division_head(old, None, actor)
    _audit("division_member", staff, actor,
           {"division": {"old": old.name if old else None, "new": division.name if division else None}})
    return staff


def division_open_task_count(division) -> int:
    """Open group tasks routed to the division (Phase C, H23/B8): routed and
    waiting for the head, or accepted, and not yet completed or cancelled.
    Individual tasks of the members are counted per person, not here."""
    from tasks.models import Task
    return (Task.objects.filter(division=division, division_state__in=[Task.DIV_PENDING, Task.DIV_ACCEPTED])
            .exclude(state__in=list(Task.CLOSED_STATES)).count())


def division_has_capacity(division) -> bool:
    """B8: False when the division already holds ``max_open_tasks`` open tasks."""
    if not division.max_open_tasks:
        return True
    return division_open_task_count(division) < division.max_open_tasks


# ===========================================================================
# Offices (B9)
# ===========================================================================
def office_of(user):
    """v2.2: the unit (Department of any kind) the user is posted to."""
    staff = _staff(user)
    if staff is None or not staff.department_id:
        return None
    return staff.department


def can_manage_offices(actor) -> bool:
    return bool(actor and getattr(actor, "is_authenticated", False) and actor.is_active
                and (is_super_admin(actor) or is_es(actor) or actor.has_perm("org.manage_offices")))


@transaction.atomic
def set_office_groups(office, groups, actor):
    """Replace the unit's member permission groups (v2.2: Department.member_groups). A non-ES actor may only attach
    or detach groups whose permissions they hold (C10 subset rule)."""
    from accounts.permissions_service import effective_permissions, group_permissions
    if actor is not None and not can_manage_offices(actor):
        raise OrgRuleError("You cannot manage offices.")
    new = {g.pk: g for g in groups}
    rel = office.member_groups if hasattr(office, "member_groups") else office.groups
    old = {g.pk: g for g in rel.all()}
    changed = [new.get(pk) or old.get(pk) for pk in set(new) ^ set(old)]
    if actor is not None and not (is_super_admin(actor) or is_es(actor)):
        mine = effective_permissions(actor)
        for g in changed:
            if not group_permissions(g) <= mine:
                raise OrgRuleError(f"You do not hold every permission in '{g.name}'.")
    rel.set(list(new.values()))
    if changed:
        _audit("office_groups", office, actor,
               {"groups": {"old": sorted(g.name for g in old.values()),
                           "new": sorted(g.name for g in new.values())}})
    return office


# ===========================================================================
# Grade levels and roles (C7-C12)
# ===========================================================================
_GRADE_RE = re.compile(r"(\d{1,2})")


def parse_grade_number(text):
    """'GL 08', 'CONTEDISS 8', '08/2', '8' -> 8. None when no level is found."""
    if text is None:
        return None
    raw = str(text).strip().upper()
    if not raw:
        return None
    m = _GRADE_RE.search(raw)
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 17 else None


def grade_for_text(text):
    """The GradeLevel for a free-text grade, or None (unmapped)."""
    n = parse_grade_number(text)
    if n is None:
        return None
    from .models import GradeLevel
    return GradeLevel.objects.filter(number=n).first()


def grade_rules_enabled() -> bool:
    import config
    value = config.get("hr.grade_rules_enabled", False)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _role_obj(role):
    from accounts.models import Role
    if role is None or isinstance(role, Role):
        return role
    return Role.objects.filter(code=str(role)).first() or (
        Role.objects.filter(pk=role).first() if str(role).isdigit() else None)


def role_problems(actor, target, role):
    """Every rule ``assign_role`` would break, as a list of plain sentences
    (empty = allowed). ES and super admin are not exempt here: the override is
    explicit in ``assign_role``."""
    from accounts.permissions import can_act_on_user
    from accounts.permissions_service import effective_permissions, role_permissions
    problems = []
    role = _role_obj(role)
    from . import single_holder
    taken = single_holder.conflict(target, role) if role is not None else ""
    if taken:  # Part 1a: holds for everyone, the system included
        problems.append(taken)
    if actor is None:
        return problems  # system (migration, seed)
    if not getattr(actor, "is_authenticated", False) or not actor.is_active:
        return ["You must be signed in."]
    privileged = actor.is_superuser
    if not (privileged or is_es(actor) or actor.has_perm("accounts.change_user")
            or is_director(actor, getattr(target, "department_id", None))):
        problems.append("You cannot change roles.")
    if not can_act_on_user(actor, target):
        problems.append("This person outranks you.")
    if role is not None:
        if not privileged and (role.rank or 0) > rank_of(actor):
            problems.append(f"'{role.name}' is more senior than your own role.")
        if not privileged:
            missing = role_permissions(role) - effective_permissions(actor)
            if missing:
                problems.append(f"'{role.name}' includes {len(missing)} permission(s) you do not hold "
                                f"(e.g. {sorted(missing)[0]}).")
        if grade_rules_enabled() and role.min_grade:
            grade = grade_of(target)
            if grade is None:
                problems.append(f"'{role.name}' needs grade level {role.min_grade:02d} or above; "
                                "this person has no grade level.")
            elif grade.number < role.min_grade:
                problems.append(f"'{role.name}' needs grade level {role.min_grade:02d} or above; "
                                f"this person is on {grade.display}.")
    # Department scope (C11): only cross-department actors may act on others' staff.
    if not (privileged or is_es(actor) or actor.has_perm("accounts.view_all_users")):
        target_dept = getattr(target, "department_id", None)
        if not target_dept or target_dept != actor.department_id:
            problems.append("You can only manage staff of your own department.")
    return problems


@transaction.atomic
def assign_role(actor, target, role, reason="", *, override=False, source="web"):
    """Give ``target`` the ``role`` (a Role, code, pk or None to clear).

    Rules (C9-C12): the role's minimum grade (while ``hr.grade_rules_enabled``),
    the actor must hold every permission of the role (HR subset rule) and not be
    outranked, and a non-ES actor acts only inside their own department. The ES
    (or a superuser) may pass ``override=True`` with a reason to bypass the
    rules; that is audited as ``role_override``. ``actor=None`` is the system
    (seeds, migrations). Raises OrgRuleError with every reason."""
    role = _role_obj(role)
    old_code = role_code(target)
    if (role.pk if role else None) == target.role_id:
        return target
    from . import single_holder
    taken = single_holder.conflict(target, role)
    if taken:  # Part 1a: one director per department, one ES — no override bypasses it
        raise OrgRuleError(taken)
    # NBTE internal roles never go to portal accounts (and portal roles never
    # to NBTE staff). Not overridable: it is a separation rule, not a rank rule.
    from accounts.audience import role_problem
    mismatch = role_problem(role, getattr(target, "user_type", "staff")) if role else ""
    if mismatch:
        raise OrgRuleError(mismatch)
    problems = role_problems(actor, target, role)
    action = "role_assigned"
    if problems:
        if override and actor is not None and (is_es(actor) or actor.is_superuser):
            if not (reason or "").strip():
                raise OrgRuleError("An override needs a reason.")
            action = "role_override"
        else:
            raise OrgRuleError(" ".join(problems))
    target.role = role
    target.save(update_fields=["role"])
    _invalidate(target)
    _audit(action, target, actor, {"role": {"old": old_code or None, "new": role.code if role else None},
                                   "source": source, **({"overridden": problems} if action == "role_override" else {})},
           note=reason)
    return target


def grantable_roles(actor, target=None):
    """Roles ``actor`` may give (to ``target`` when given), for pickers."""
    from accounts.audience import roles_for
    from accounts.models import Role
    base = Role.objects.all()
    if target is not None and getattr(target, "user_type", None):
        base = roles_for(target.user_type, base)   # only roles meant for this kind of account
    roles = list(base.order_by("-rank", "name"))
    if actor is None or getattr(actor, "is_superuser", False) or is_es(actor):
        return roles  # the ES may override any rule (audited) — see assign_role
    probe = target
    if probe is None:
        class _Probe:  # same department as the actor, no grade, rank 0
            is_superuser = False
            role_id = None
            role = None
            department_id = getattr(actor, "department_id", None)
            pk = None
        probe = _Probe()
    out = []
    for r in roles:
        problems = [p for p in role_problems(actor, probe, r) if "outranks" not in p]
        if target is None:
            problems = [p for p in problems if "grade level" not in p]
        if not problems:
            out.append(r)
    return out


# ===========================================================================
# Director-granted permissions (X31)
# ===========================================================================
def _perm(perm):
    from django.contrib.auth.models import Permission
    if isinstance(perm, Permission):
        return perm
    app, _, codename = str(perm).partition(".")
    return Permission.objects.filter(content_type__app_label=app, codename=codename).select_related(
        "content_type").first()


def _label(perm):
    return f"{perm.content_type.app_label}.{perm.codename}"


def grantable_permissions(actor=None):
    """Active allow-list rows (GrantablePermission), optionally only those
    ``actor`` may grant (holds the permission and meets the rank)."""
    from .models import GrantablePermission
    rows = list(GrantablePermission.objects.filter(is_active=True)
                .select_related("permission__content_type"))
    if actor is None or is_super_admin(actor) or is_es(actor):
        return rows
    return [g for g in rows if actor.has_perm(_label(g.permission)) and rank_of(actor) >= g.min_grantor_rank]


def can_manage_access(actor, target) -> bool:
    """May ``actor`` grant/revoke allow-listed permissions on ``target``?
    The director of the target's department, the ES, or super admin."""
    if actor is None or not getattr(actor, "is_authenticated", False) or not actor.is_active:
        return False
    if target is None or target.pk == actor.pk:
        return False
    if is_super_admin(actor) or is_es(actor):
        return True
    dept_id = getattr(target, "department_id", None)
    return bool(dept_id and is_director(actor, dept_id))


def _default_days():
    import config
    try:
        return int(config.get("org.grant.max_days_default", 90))
    except (TypeError, ValueError):
        return 90


@transaction.atomic
def grant_permission(director, staff_user, perm, *, scope="department", days=None, reason=""):
    """X31: a director grants an allow-listed permission they hold to one of
    their own staff, for at most the allowed number of days. Returns the
    UserPermissionOverride. Audited; permission cache invalidated."""
    from accounts.models import UserPermissionOverride
    from .models import GrantablePermission
    p = _perm(perm)
    if p is None:
        raise OrgRuleError("Unknown permission.")
    if not can_manage_access(director, staff_user):
        raise OrgRuleError("Only the director of this person's department can grant them permissions.")
    privileged = is_super_admin(director) or is_es(director)
    gp = GrantablePermission.objects.filter(permission=p, is_active=True).first()
    if gp is None and not privileged:
        raise OrgRuleError(f"{_label(p)} is not on the list of permissions directors may grant.")
    if not privileged:
        if not director.has_perm(_label(p)):
            raise OrgRuleError(f"You can only grant permissions you hold yourself ({_label(p)}).")
        if rank_of(director) < gp.min_grantor_rank:
            raise OrgRuleError("Your rank is too low to grant this permission.")
    if not (reason or "").strip():
        raise OrgRuleError("Give a reason for the grant.")
    limit = (gp.max_duration_days if gp is not None and gp.max_duration_days else _default_days())
    if days in ("", None):
        days = None if privileged and gp is None else limit
    days = int(days) if days is not None else None
    if days is not None and days < 1:
        raise OrgRuleError("A grant lasts at least one day.")
    if days is not None and not privileged and days > limit:
        raise OrgRuleError(f"This permission can be granted for at most {limit} days.")
    if scope not in (UserPermissionOverride.SCOPE_DEPARTMENT, UserPermissionOverride.SCOPE_DIVISION):
        raise OrgRuleError("Unknown scope.")
    existing = UserPermissionOverride.objects.filter(user=staff_user, permission=p).first()
    if existing is not None and existing.effect == UserPermissionOverride.REVOKE and existing.is_current:
        raise OrgRuleError("An administrator has blocked this permission for this person.")
    expires = timezone.now() + datetime.timedelta(days=days) if days else None
    if existing is None:
        existing = UserPermissionOverride(user=staff_user, permission=p)
    existing.effect = UserPermissionOverride.GRANT
    existing.reason = reason.strip()[:255]
    existing.granted_by = director
    existing.expires_at = expires
    existing.granted_scope = scope
    existing.revoked_at = None
    existing.revoked_by = None
    existing.save()
    _invalidate(staff_user)
    _audit("permission_granted", staff_user, director,
           {"permission": _label(p), "expires_at": expires.isoformat() if expires else None, "scope": scope},
           note=reason)
    _notify_grant(staff_user, director, p, expires, granted=True)
    return existing


@transaction.atomic
def revoke_permission(actor, override, reason=""):
    """Revoke a director grant (marks it revoked; history is kept)."""
    if override.revoked_at is not None:
        return override
    if not can_manage_access(actor, override.user):
        raise OrgRuleError("Only the director of this person's department can revoke their permissions.")
    if override.effect != "grant":
        raise OrgRuleError("Only grants can be revoked here.")
    if not (is_super_admin(actor) or is_es(actor)) and not override.granted_scope:
        raise OrgRuleError("This permission was given by an administrator; ask them to remove it.")
    override.revoked_at = timezone.now()
    override.revoked_by = actor
    override.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
    _invalidate(override.user)
    _audit("permission_revoked", override.user, actor, {"permission": _label(override.permission)}, note=reason)
    _notify_grant(override.user, actor, override.permission, None, granted=False)
    return override


def active_grants(user=None, department=None):
    """Current director grants (not revoked, not expired)."""
    from accounts.models import UserPermissionOverride
    now = timezone.now()
    qs = (UserPermissionOverride.objects.filter(effect="grant", revoked_at__isnull=True)
          .exclude(granted_scope="").filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
          .select_related("permission__content_type", "user", "granted_by"))
    if user is not None:
        qs = qs.filter(user=user)
    if department is not None:
        d = _dept(department)
        qs = qs.filter(user__department=d) if d is not None else qs.none()
    return qs.order_by("expires_at", "pk")


def expire_permission_grants(now=None, *, dry_run=False):
    """Mark grants whose expiry has passed as revoked (nightly). Expired rows
    already stop counting at read time; this keeps the record tidy and
    audited. Returns the number of rows expired."""
    from accounts.models import UserPermissionOverride
    now = now or timezone.now()
    qs = UserPermissionOverride.objects.filter(effect="grant", revoked_at__isnull=True,
                                               expires_at__isnull=False, expires_at__lte=now)
    n = 0
    for o in qs.select_related("permission__content_type", "user"):
        n += 1
        if dry_run:
            continue
        o.revoked_at = now
        o.save(update_fields=["revoked_at", "updated_at"])
        _audit("permission_expired", o.user, None, {"permission": _label(o.permission)})
    return n


def _notify_grant(user, actor, perm, expires, *, granted):
    """Tell the staff member (best effort; never blocks the grant)."""
    try:
        from notifications.services import notify
    except Exception:
        return
    try:
        title = (f"You were given '{perm.name}'" + (f" until {timezone.localtime(expires):%d %b %Y}" if expires else "")
                 if granted else f"'{perm.name}' was removed from your access")
        notify(user, title=title, url="/accounts/profile/", actor=actor)
    except Exception:
        pass
