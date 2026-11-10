"""v2.3: decide only after opening the file.

Accept / return / approve buttons stay locked until the person has opened
the document (a preview or a download). The server checks it too, from the
access events every protected file view already records, so the rule cannot
be skipped by a crafted request.

Setting ``qa.review.require_preview`` (default on) switches it off.
Director bulk approval of items an officer already reviewed is exempt
(the officer opened them; the director chose to trust the recommendation).
"""
from django.core.exceptions import ValidationError

OPEN_ACTIONS = ("preview", "download")


def enabled():
    import config

    value = config.get("qa.review.require_preview", True)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def has_opened(user, obj):
    if user is None or obj is None or not getattr(obj, "pk", None):
        return False
    from django.contrib.contenttypes.models import ContentType

    from assignments.models import AccessEvent

    return AccessEvent.objects.filter(
        user=user, object_ct=ContentType.objects.get_for_model(obj, for_concrete_model=False),
        object_id=str(obj.pk), action__in=OPEN_ACTIONS).exists()


def opened_ids(user, model, ids):
    """{pk} of ``model`` rows among ``ids`` this user has opened (one query)."""
    from django.contrib.contenttypes.models import ContentType

    from assignments.models import AccessEvent

    if user is None or not ids:
        return set()
    rows = AccessEvent.objects.filter(user=user, object_ct=ContentType.objects.get_for_model(
        model, for_concrete_model=False), object_id__in=[str(i) for i in ids], action__in=OPEN_ACTIONS
    ).values_list("object_id", flat=True)
    return {int(x) for x in rows if str(x).isdigit()}


def require_opened(user, obj, what="the document"):
    if enabled() and obj is not None and getattr(obj, "file", None) and not has_opened(user, obj):
        raise ValidationError(f"Open {what} first (Preview or Download), then decide.")
