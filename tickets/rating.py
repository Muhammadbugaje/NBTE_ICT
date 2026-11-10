"""Ticket rating by the opener (v2.1 Phase C, I4).

    can_rate(ticket, user)          the opener, on a resolved ticket or a closed one not yet rated
    rate_ticket(ticket, user, rating, comment="")   resolved -> closed with the rating; closed -> rating added
    pending_ratings(user)           the opener's resolved/closed tickets without a rating (dashboard prompt)
    send_rating_reminders(now=None) daily: remind openers (setting tickets.rating.reminder_days,
                                    tickets.rating.max_reminders); e-mail behind tickets.email.rating_reminder
"""
from __future__ import annotations

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from notifications.models import Notification
from notifications.services import notify

from .models import Ticket


def _cfg_int(key, default):
    try:
        import config
        return int(config.get(key, default))
    except Exception:
        return default


def can_rate(ticket, user) -> bool:
    return bool(getattr(user, "pk", None) and ticket.raised_by_id == user.pk
                and ticket.state in (Ticket.RESOLVED, Ticket.CLOSED) and not ticket.satisfaction_rating)


def _clean_rating(rating):
    try:
        r = int(rating)
    except (TypeError, ValueError):
        raise ValidationError("Choose 1 to 5 stars.")
    if not 1 <= r <= 5:
        raise ValidationError("Choose 1 to 5 stars.")
    return r


@transaction.atomic
def rate_ticket(ticket, user, rating, comment=""):
    if not can_rate(ticket, user):
        raise PermissionDenied("Only the person who opened the ticket can rate it, once, after it is resolved.")
    r = _clean_rating(rating)
    from .services import _notify_staff_on_change, close_ticket, log_activity
    if ticket.state == Ticket.RESOLVED:
        close_ticket(ticket, user, rating=r, comment=comment)
    else:
        ticket.satisfaction_rating = r
        ticket.satisfaction_comment = (comment or "").strip()[:2000]
        ticket.rated_at = timezone.now()
        ticket.save(update_fields=["satisfaction_rating", "satisfaction_comment", "rated_at", "updated_at"])
        _notify_staff_on_change(ticket, user, f"Ticket rated {r}/5", "rated")
    log_activity(ticket, "rated", actor=user, message=f"Rated {r}/5 by the opener.", metadata={"rating": r})
    return ticket


def pending_ratings(user, limit=5):
    if not getattr(user, "is_authenticated", False):
        return Ticket.objects.none()
    return (Ticket.objects.filter(raised_by=user, state__in=(Ticket.RESOLVED, Ticket.CLOSED),
                                  satisfaction_rating__isnull=True, resolved_at__isnull=False)
            .order_by("-resolved_at")[:limit])


def send_rating_reminders(now=None):
    """Remind each opener of resolved, unrated tickets. Returns the count sent."""
    now = now or timezone.now()
    days = max(1, _cfg_int("tickets.rating.reminder_days", 2))
    cap = _cfg_int("tickets.rating.max_reminders", 3)
    if cap <= 0:
        return 0
    due = now - datetime.timedelta(days=days)
    qs = (Ticket.objects.filter(state__in=(Ticket.RESOLVED, Ticket.CLOSED), satisfaction_rating__isnull=True,
                                resolved_at__isnull=False, resolved_at__lte=due, rating_reminders_sent__lt=cap)
          .filter(Q(last_rating_reminder_at__isnull=True) | Q(last_rating_reminder_at__lte=due))
          .select_related("raised_by"))
    sent = 0
    from .services import _email_event, log_activity
    for t in qs:
        if not t.raised_by.is_active:
            continue
        notify(t.raised_by, title=f"Please rate {t.reference}",
               body=f"How did we do on “{t.subject}”? Rate it from 1 to 5 stars.",
               url=f"/tickets/{t.pk}/#rate", category=Notification.REQUEST, level=Notification.INFO,
               metadata={"ticket_id": t.pk, "rating_reminder": True})
        _email_event(t, "rating_reminder", [t.raised_by])
        t.rating_reminders_sent += 1
        t.last_rating_reminder_at = now
        t.save(update_fields=["rating_reminders_sent", "last_rating_reminder_at", "updated_at"])
        log_activity(t, "rating_reminder", actor=None, message="The opener was reminded to rate the ticket.",
                     metadata={"n": t.rating_reminders_sent})
        sent += 1
    return sent
