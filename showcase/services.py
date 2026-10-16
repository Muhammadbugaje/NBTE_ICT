"""Read helpers for showcase images. Decorative content: no permission
checks — anything active, in its time window and tagged for the placement
is shown to everyone who sees that page."""
from django.db.models import Q
from django.utils import timezone

from .models import ShowcaseImage


def _placement_q(placement):
    # Exact code match inside the comma-separated list ("hero" must not
    # match a hypothetical "superhero"). Regex works on SQLite and PostgreSQL.
    return Q(placements__regex=r"(^|,)%s(,|$)" % placement)


def active_images(placement, now=None):
    """Images for `placement`, active and inside their schedule, in display
    order (order, newest first)."""
    if placement not in ShowcaseImage.PLACEMENT_CODES:
        return ShowcaseImage.objects.none()
    now = now or timezone.now()
    return (
        ShowcaseImage.objects.filter(is_active=True)
        .filter(_placement_q(placement))
        .filter(Q(start_at__isnull=True) | Q(start_at__lte=now))
        .filter(Q(end_at__isnull=True) | Q(end_at__gt=now))
        .exclude(image="")
        .order_by("order", "-created_at")
    )


def is_publicly_visible(image, now=None):
    """True if the image appears in any placement right now."""
    now = now or timezone.now()
    if not image.is_active or not image.image:
        return False
    if image.start_at and image.start_at > now:
        return False
    if image.end_at and image.end_at <= now:
        return False
    return True
