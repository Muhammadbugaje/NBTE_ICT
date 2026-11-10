"""Single-holder roles (v2.1 Part 1a): one director per department, one ES.

Setting ``org.single_holder_roles`` (JSON): role code -> scope, where scope is
``"department"`` (one holder per department; for ``director`` the holder is
the department's seat, ``Department.director``, a one-to-one field) or
``"global"`` (one holder in the whole organisation). The Directors of Finance
and ICT are department directors, so the ``director`` rule covers them.

* ``conflict(user, role, department)`` — the refusal message, or "" — is
  checked by ``org.assign_role`` (no override bypasses it), ``assign_director``
  and staff account provisioning; every role screen goes through those.
* ``duplicates()`` / ``apply()`` clean existing data: every holder of a
  duplicated seat loses the role (it falls back to ``org.vacated_role``) and
  the seat is left empty, audited per person; the ES and super admin are told
  in the app. ``manage.py fix_duplicate_directors --dry-run|--apply``.
"""
from __future__ import annotations

import json

DEFAULT_RULES = {"director": "department", "executive-secretary": "global"}
SETTING = "org.single_holder_roles"
FALLBACK_SETTING = "org.vacated_role"
DEFAULT_FALLBACK = "staff"


def rules():
    import config
    value = config.get(SETTING, None)
    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else None
        except ValueError:
            value = None
    if isinstance(value, list):
        value = {code: ("department" if code == "director" else "global") for code in value}
    if not isinstance(value, dict) or not value:
        value = DEFAULT_RULES
    return {str(k): ("department" if str(v).lower().startswith("dep") else "global") for k, v in value.items()}


def _code(role):
    if role is None:
        return ""
    return role if isinstance(role, str) else getattr(role, "code", "") or ""


def _holders(code, department_id=None, *, exclude=None):
    from accounts.models import User
    qs = User.objects.filter(role__code=code, is_active=True, user_type="staff")
    if department_id is not None:
        qs = qs.filter(department_id=department_id)
    if exclude is not None:
        qs = qs.exclude(pk=getattr(exclude, "pk", exclude))
    return qs


def _name(user):
    return user.get_full_name() or user.email


def conflict(user, role, department=None):
    """Why ``user`` cannot hold ``role`` (in ``department``; default theirs), or ""."""
    code = _code(role)
    scope = rules().get(code)
    if not scope:
        return ""
    if scope == "global":
        other = _holders(code, exclude=user).first()
        if other is None:
            return ""
        label = getattr(role, "name", None) or code
        return f"{label} is already held by {_name(other)}. Vacate the seat first."
    dept_id = getattr(department, "pk", department) if department is not None else getattr(user, "department_id", None)
    if not dept_id:
        return ""
    from staff.models import Department
    dept = Department.objects.filter(pk=dept_id).select_related("director").first()
    if dept is None:
        return ""
    if code == "director":
        seat = dept.director
        other = seat if seat is not None and seat.pk != getattr(user, "pk", None) else None
    else:
        other = _holders(code, dept_id, exclude=user).first()
    if other is None:
        return ""
    what = "a director" if code == "director" else f"a {getattr(role, 'name', code)}"
    return f"{dept.name} already has {what}: {_name(other)}. Vacate the seat first."


# ---------------------------------------------------------------------------
# Clean-up of existing data
# ---------------------------------------------------------------------------
def duplicates():
    """[{"role", "scope", "department", "holders": [User], "seat": User|None}] where
    more than one active staff account holds a single-holder role."""
    from staff.models import Department
    out = []
    for code, scope in rules().items():
        holders = list(_holders(code).select_related("department").order_by("department_id", "pk"))
        if scope == "global":
            if len(holders) > 1:
                out.append({"role": code, "scope": scope, "department": None, "holders": holders, "seat": None})
            continue
        by_dept = {}
        for u in holders:
            if u.department_id:
                by_dept.setdefault(u.department_id, []).append(u)
        for dept in Department.objects.filter(pk__in=list(by_dept)).select_related("director").order_by("name"):
            group = by_dept[dept.pk]
            seat = dept.director if code == "director" else None
            if seat is not None and seat not in group:
                group = [seat] + group
            if len(group) > 1:
                out.append({"role": code, "scope": scope, "department": dept, "holders": group, "seat": seat})
    return out


def report_text(rows, *, applied=False):
    from django.utils import timezone
    lines = ["# Duplicate single-holder roles", "",
             f"Run {timezone.localtime():%Y-%m-%d %H:%M} — {'APPLIED' if applied else 'dry run (nothing changed)'}.",
             f"Rule: `{SETTING}` = `{json.dumps(rules())}`. Every holder of a duplicated seat loses the role "
             f"(it becomes `{fallback_code()}`) and the seat is left empty for the ES or super admin to fill.", ""]
    if not rows:
        lines.append("No duplicates found.")
    for r in rows:
        where = r["department"].name if r["department"] is not None else "Whole organisation"
        lines += [f"## {where} — `{r['role']}` ({len(r['holders'])} holders)", ""]
        for u in r["holders"]:
            seat = " (in the seat)" if r["seat"] is not None and u.pk == r["seat"].pk else ""
            lines.append(f"- {_name(u)} <{u.email}>{seat}")
        lines.append("")
    return "\n".join(lines)


def fallback_code():
    import config
    return str(config.get(FALLBACK_SETTING, DEFAULT_FALLBACK) or DEFAULT_FALLBACK).strip()


def apply(actor=None, rows=None):
    """Remove the role from every holder of a duplicated seat and vacate the
    seat. Returns the rows handled. Audited; ES and super admin notified."""
    from django.db import transaction

    from accounts.models import Role
    from core import audit
    from staff.models import Department

    rows = duplicates() if rows is None else rows
    fallback = Role.objects.filter(code=fallback_code()).first()
    with transaction.atomic():
        for r in rows:
            dept = r["department"]
            if dept is not None and r["role"] == "director" and dept.director_id:
                former = dept.director
                Department.objects.filter(pk=dept.pk).update(director=None)
                from qa_core.departments import clear_cache

                clear_cache()   # v2.2: director-seat memo
                audit.log("director_vacated", dept, actor=actor,
                          changes={"director": {"old": former.email, "new": None}},
                          note="Duplicate directors: seat vacated (fix_duplicate_directors)")
            for u in r["holders"]:
                old = u.role.code if u.role_id else None
                u.role = fallback
                u._org_skip_seat = True
                u.save(update_fields=["role"])
                audit.log("role_assigned", u, actor=actor, changes={"role": {"old": old, "new": _code(fallback) or None}},
                          note=f"Duplicate {r['role']} removed (fix_duplicate_directors)")
                from org.services import _invalidate
                _invalidate(u)
        if rows:
            transaction.on_commit(lambda: _notify(rows))
    return rows


def _notify(rows):
    try:
        import org
        from accounts.models import User
        from notifications.services import notify
        people = {u.pk: u for u in User.objects.filter(is_active=True, user_type="staff")
                  if org.is_es(u) or org.is_super_admin(u)}
        for r in rows:
            where = r["department"].name if r["department"] is not None else "the organisation"
            names = ", ".join(_name(u) for u in r["holders"])
            for p in people.values():
                notify(p, title=f"Seat vacated: {where}",
                       body=f"{where} had more than one {r['role']} ({names}). The seat was vacated; please appoint "
                            "one director." if r["role"] == "director" else
                       f"{where} had more than one {r['role']} ({names}). The role was removed from all of them; "
                       "please appoint one.", url="/org/")
    except Exception:  # pragma: no cover
        import logging
        logging.getLogger(__name__).exception("Could not notify about vacated seats")
