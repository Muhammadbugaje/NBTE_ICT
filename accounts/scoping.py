"""Department scoping for querysets (fail closed).

    qs = scope_to_department(Staff.all_objects.all(), request.user)

* superusers / `accounts.access_all_departments` -> everything
* a user with a department -> only rows of that department
* a user with NO department and no all-departments right -> nothing

QA (Master Plan §4) adds the sibling `scope_to_institution`, which mirrors the
same fail-closed contract for anything that belongs to a TVET institution.
"""


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


def scope_to_institution(queryset, user, field="institution"):
    """Return `queryset` limited to the institutions `user` may see.

    - not authenticated                     -> none()
    - user_type 'institution'               -> their own institution (none() if NULL)
    - user_type 'resource_person'           -> institutions of their ACTIVE engagements
    - user_type 'staff':
        superuser / accounts.access_all_departments / qa_core.view_all_institutions -> all
        else -> institutions handled by their department (none() if no department)

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
    if user is None or not user.is_authenticated or not user.is_active:
        return queryset.none()

    user_type = getattr(user, "user_type", "staff")

    if user_type == "institution":
        if not getattr(user, "institution_id", None):
            return queryset.none()
        return queryset.filter(**{(f"{field}_id" if field else "pk"): user.institution_id})

    if user_type == "resource_person":
        ids = _engaged_institution_ids(user)
        if not ids:
            return queryset.none()
        return queryset.filter(**{(f"{field}_id__in" if field else "pk__in"): ids})

    # Staff: institution scope is derived from department scope.
    if (user.is_superuser
            or user.has_perm("accounts.access_all_departments")
            or user.has_perm("qa_core.view_all_institutions")):
        return queryset
    if user.department_id is None:
        return queryset.none()
    lookup = f"{field}__handling_department_id" if field else "handling_department_id"
    return queryset.filter(**{lookup: user.department_id})
