"""P.5: weekly access digest for the Director of ICT (and the ES): grants
given and ended in the last 7 days, grants live longer than
``qa.access.long_grant_days``, work assignments opened and closed. Counts and
links only; the grants log page has the detail."""
from __future__ import annotations

import datetime

from django.utils import timezone


def build(now=None):
    from .grants import long_grant_days
    from .models import AccessGrant, WorkAssignment
    now = now or timezone.now()
    week = now - datetime.timedelta(days=7)
    return {
        "granted": AccessGrant.objects.filter(granted_at__gte=week).count(),
        "ended": AccessGrant.objects.filter(revoked_at__gte=week).count(),
        "live": AccessGrant.objects.live(now=now).count(),
        "long": AccessGrant.objects.live(now=now).filter(
            granted_at__lte=now - datetime.timedelta(days=long_grant_days())).count(),
        "assigned": WorkAssignment.objects.filter(assigned_at__gte=week).count(),
        "completed": WorkAssignment.objects.filter(completed_at__gte=week).count(),
    }


def send(now=None):
    """Notify the Director of ICT (qa.privacy.ict_department). Returns the numbers."""
    import org

    from .access import ict_department_code
    data = build(now)
    director = org.director_of(ict_department_code())
    if director is not None:
        from django.urls import reverse
        from notifications.services import notify
        notify(director, title="Weekly access digest",
               body=(f"{data['granted']} temporary access grant(s) given and {data['ended']} ended this week; "
                     f"{data['live']} live, {data['long']} older than the limit. {data['assigned']} work "
                     f"assignment(s) opened, {data['completed']} completed."),
               url=reverse("assignments:grants"))
    return data
