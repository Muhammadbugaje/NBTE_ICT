"""Department scoping for querysets (fail closed).

    qs = scope_to_department(Staff.all_objects.all(), request.user)

* superusers / `accounts.access_all_departments` -> everything
* a user with a department -> only rows of that department
* a user with NO department and no all-departments right -> nothing
"""


def scope_to_department(queryset, user, field="department"):
    if not user.is_authenticated:
        return queryset.none()
    if user.is_superuser or user.has_perm("accounts.access_all_departments"):
        return queryset
    if user.department_id is None:
        return queryset.none()
    return queryset.filter(**{f"{field}_id": user.department_id})
