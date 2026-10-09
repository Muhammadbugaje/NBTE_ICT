"""Staff-side services — leadership views and lookups.

Shared by:
  - the public leadership page (public.html)
  - the custom management panel (manage_*.html)
  - the homepage preview widget (_preview.html)
"""
from __future__ import annotations

from django.db.models import Q

from .models import ExecutivePosition, ExecutiveTenure


# ============================================================================
# PUBLIC READ APIs
# ============================================================================
def current_leadership(*, scope=None, department=None, public_only=True):
    """Current tenure for every position, ordered by rank."""
    qs = (
        ExecutiveTenure.objects
        .filter(ended_on__isnull=True)
        .select_related("staff", "staff__department",
                        "position", "position__department")
        .order_by("position__order", "display_order", "started_on")
    )
    if public_only:
        qs = qs.filter(is_public=True)
    if scope:
        qs = qs.filter(position__scope=scope)
    if department:
        qs = qs.filter(position__department=department)
    return qs


def featured_leadership(limit=6, public_only=True):
    """Leadership strip for the public homepage."""
    qs = current_leadership(public_only=public_only)
    featured = qs.filter(is_featured=True)
    return (featured if featured.exists() else qs)[:limit]


def leadership_timeline(*, position=None, public_only=True):
    """Full chronology — past and present, newest first."""
    qs = (
        ExecutiveTenure.objects
        .select_related("staff", "staff__department", "position")
        .order_by("-started_on", "-ended_on", "position__order")
    )
    if public_only:
        qs = qs.filter(is_public=True)
    if position:
        qs = qs.filter(position=position)
    return qs


def positions_with_history(public_only=True):
    """Every position with its tenures, grouped. For the public timeline page."""
    positions = (
        ExecutivePosition.objects
        .filter(is_active=True)
        .order_by("order", "name")
    )
    result = []
    for p in positions:
        tenures = list(
            p.tenures.select_related("staff").order_by("-started_on")
        )
        if public_only:
            tenures = [t for t in tenures if t.is_public]
        if tenures:
            result.append({"position": p, "tenures": tenures})
    return result


# ============================================================================
# MANAGEMENT PANEL QUERIES
# ============================================================================
def all_positions(*, active_only=False):
    qs = ExecutivePosition.objects.order_by("order", "name")
    if active_only:
        qs = qs.filter(is_active=True)
    return qs


def all_tenures(*, position=None, staff=None, current_only=False, q=None):
    """Every tenure — used by the management table. Includes private records."""
    qs = (
        ExecutiveTenure.objects
        .select_related("staff", "staff__department", "position")
        .order_by("-started_on", "-ended_on", "position__order")
    )
    if position:
        qs = qs.filter(position=position)
    if staff:
        qs = qs.filter(staff=staff)
    if current_only:
        qs = qs.filter(ended_on__isnull=True)
    if q:
        qs = qs.filter(
            Q(staff__first_name__icontains=q)
            | Q(staff__last_name__icontains=q)
            | Q(staff__employee_no__icontains=q)
            | Q(position__name__icontains=q)
        )
    return qs