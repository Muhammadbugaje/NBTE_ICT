"""Shared queryset sorting helper.

Used by every list view that exposes clickable column headers. Sort
fields must be explicitly allowlisted per view — passing a raw user
value into `queryset.order_by()` is a security hole (arbitrary field
access, expensive joins, information leaks via ordering).

Public API:
    apply_sort(queryset, sort_param, direction_param, allowlist, default)
        -> (queryset, resolved_sort_key, resolved_direction)
"""
DEFAULT_DIRECTION = "desc"


def apply_sort(queryset, sort_param, direction_param, allowlist, default):
    """Apply an allowlisted sort to a queryset.

    `allowlist` maps query-string sort keys to model field names:
        {"reference": "reference", "created": "created_at"}

    `default` must be a key present in the allowlist.

    Unknown keys and directions silently fall back to the defaults —
    never raise, never pass arbitrary user input to `order_by`.
    """
    key = (sort_param or "").strip()
    if key not in allowlist:
        key = default

    direction = (direction_param or "").strip().lower()
    if direction not in ("asc", "desc"):
        direction = DEFAULT_DIRECTION

    field = allowlist[key]
    if direction == "desc":
        field = f"-{field}"

    # Secondary sort on pk keeps pagination stable when values tie.
    return queryset.order_by(field, "-pk"), key, direction