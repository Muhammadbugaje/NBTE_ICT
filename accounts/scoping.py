"""Department scoping for querysets (fail closed).

    qs = scope_to_department(Staff.all_objects.all(), request.user)

* superusers / `accounts.access_all_departments` -> everything
* a user with a department -> only rows of that department
* a user with NO department and no all-departments right -> nothing

QA (Master Plan §4) adds the sibling `scope_to_institution`, which mirrors the
same fail-closed contract for anything that belongs to a TVET institution.
"""
from django.db.models import Q


def scope_to_department(queryset, user, field="department"):
    if not user.is_authenticated:
        return queryset.none()
    if user.is_superuser or user.has_perm("accounts.access_all_departments"):
        return queryset
    if user.department_id is None:
        return queryset.none()
    return queryset.filter(**{f"{field}_id": user.department_id})


# ---------------------------------------------------------------------------
# Institution scoping (QA, Master Plan §4)
# ---------------------------------------------------------------------------
# Resource persons see the institutions of their ACTIVE engagements. The
# engagement model (inspection.QAMapping) arrives in Phase 5, which registers
# a resolver here; until then a resource person sees nothing (fail closed).
_engagement_resolvers = []


def register_engagement_resolver(func):
    """`func(user) -> iterable of institution ids` for a resource person."""
    if func not in _engagement_resolvers:
        _engagement_resolvers.append(func)
    return func


def _engaged_institution_ids(user):
    ids = set()
    for resolver in _engagement_resolvers:
        ids.update(resolver(user) or ())
    return ids


def _install_ssq_ids_memo():
    """v2.4 speed pass: `assignments.access.ssq_accessible_ids` is asked once
    per scoped SSQ-family queryset, so one SSQ page re-computed the same
    allow-list 5×. Wrap it with a per-user, signal-invalidated copy (the
    core.fastcache HOME namespace is bumped by every write in the QA apps,
    including SSQ and WorkAssignment; TTL is the safety net). Same rows, same
    order — a page can no longer interleave the same two queries five times."""
    global _ssq_ids_memo_installed
    if _ssq_ids_memo_installed:
        return
    _ssq_ids_memo_installed = True
    from assignments.access import FULL, ssq_accessible_ids as _real

    def memoized(user):
        if user is None or not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
            return _real(user)
        from core import fastcache

        def build():
            ids = _real(user)
            return "FULL" if ids is FULL else sorted(ids)

        try:
            # The key carries the user id, so lists are never shared across users.
            ids = fastcache.cached(fastcache.HOME, f"ssq-ids:{user.pk}", build, ttl=20, user=user)
        except Exception:  # noqa: BLE001 — a cache outage must not change the rows
            return _real(user)
        return FULL if ids == "FULL" else set(ids)

    from assignments import access as _access

    _access.ssq_accessible_ids = memoized


_ssq_ids_memo_installed = False


def scope_to_institution(queryset, user, field="institution"):
    """Return `queryset` limited to the institutions `user` may see.

    - not authenticated                     -> none()
    - user_type 'institution'               -> their own institution (none() if NULL)
    - user_type 'resource_person'           -> institutions of their ACTIVE engagements
    - user_type 'staff', qa.privacy.mode == "department" (default):
        superuser / accounts.access_all_departments / qa_core.view_all_institutions -> all
        else -> institutions handled by their department (none() if no department)
    - user_type 'staff', qa.privacy.mode == "assignment" (v2.1 X1–X4):
        assignments.access.scope_queryset — full viewers everything; the ICT
        supervisor the supervised institutions in full; department leads the
        department's part (rows with DEPARTMENT_PATH = their department);
        everyone else only assigned / granted institutions and records.

    `field` is the path from the queryset's model to `Institution`
    (e.g. "institution", "programme__institution"); pass `None` or ""
    when the queryset is of `Institution` itself.
    """
    result = _scope_to_institution(queryset, user, field)
    # Querysets of QA models refuse to evaluate until scoped (qa_core.scoping);
    # passing through here is exactly what scoping means.
    mark = getattr(result, "_mark_scoped", None)
    if mark is not None:
        mark()
    return result


def _scope_to_institution(queryset, user, field):
    """Implementation of scope_to_institution (see its docstring)."""
    _install_ssq_ids_memo()
    if user is None or not user.is_authenticated or not user.is_active:
        return queryset.none()

    user_type = getattr(user, "user_type", "staff")
    if user_type == "staff":
        from assignments.access import ssq_queryset_scope

        ssq_scoped = ssq_queryset_scope(queryset, user)
        if ssq_scoped is not None:
            return ssq_scoped

    if user_type == "institution":
        if not getattr(user, "institution_id", None):
            return queryset.none()
        return queryset.filter(**{(f"{field}_id" if field else "pk"): user.institution_id})

    if user_type == "resource_person":
        ids = _engaged_institution_ids(user)
        if not ids:
            return queryset.none()
        return queryset.filter(**{(f"{field}_id__in" if field else "pk__in"): ids})

    if user_type == "professional_body":
        # v2.1 X43: a body sees programmes under it through institutions.pb_portal
        # only (name, level, institution, public stage) - never institution records.
        return queryset.none()

    # v2.1 X1–X4 (Phase P): need-to-know. With qa.privacy.mode = "assignment"
    # staff see institutions they are assigned to / granted, department leads
    # their department's part, full viewers everything (assignments.access).
    from assignments import access as qa_access
    if qa_access.assignment_mode():
        return qa_access.scope_queryset(queryset, user, field)

    # "department" mode (today's rule): institution scope is derived from
    # department scope.
    if (user.is_superuser
            or user.has_perm("accounts.access_all_departments")
            or user.has_perm("qa_core.view_all_institutions")):
        return queryset
    # The full viewers of X2 (ES, super admin, Director of ICT) see every
    # journey in BOTH modes — otherwise the Director of ICT could not even open
    # the institutions he must appoint an ICT supervisor for (X19).
    from assignments import access as qa_access
    if qa_access.full_viewer_everywhere(user):
        return queryset
    if field:
        department_q = Q(**{f"{field}__handling_department_id": user.department_id}) \
            if user.department_id else Q(pk__in=[])
        scoped = department_q
        if queryset.model._meta.label_lower in ("institutions.institutiondocument", "institutions.documentreview"):
            scoped |= qa_access.assigned_rows_q(user, queryset.model)
        return queryset.filter(scoped)
    department_q = Q(handling_department_id=user.department_id) if user.department_id else Q(pk__in=[])
    return queryset.filter(department_q | Q(pk__in=qa_access.assigned_institution_ids(user)))
