"""Shared user querysets (Phase 0 / H22, M9, T-SEC-2).

Every picker that lists NBTE people (task assignees and reviewers, memo and
message recipients, ticket assignees, workflow requesters, asset custodians,
attendance staff …) starts from :func:`staff_users`, so institution users and
resource persons never appear in an internal picker.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model


def staff_users(queryset=None, *, include_inactive=False):
    """Active NBTE staff accounts (``user_type == "staff"``).

    ``queryset`` narrows an existing User queryset instead of starting from
    ``User.objects``; ``include_inactive`` keeps deactivated staff (history
    filters only, never pickers for new work).
    """
    User = get_user_model()
    qs = User.objects.all() if queryset is None else queryset
    qs = qs.filter(user_type=User.STAFF)
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return qs


def is_staff_account(user):
    """True for an NBTE staff account (not an institution user or RP)."""
    return bool(user) and getattr(user, "user_type", None) == get_user_model().STAFF
