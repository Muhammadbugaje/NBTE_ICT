"""Phase 9: record every sign-in (both front doors) as a signed-in device and
close the row on sign-out. Never breaks a sign-in."""
import logging

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

logger = logging.getLogger(__name__)


@receiver(user_logged_in, dispatch_uid="p9_record_device_login")
def record_device_login(sender, request, user, **kwargs):
    if request is None:
        return
    try:
        from .sessions_service import record_login

        record_login(request, user)
    except Exception:
        logger.exception("Could not record the signed-in device for user %s", getattr(user, "pk", None))


@receiver(user_logged_out, dispatch_uid="p9_record_device_logout")
def record_device_logout(sender, request, user, **kwargs):
    if request is None or getattr(request, "session", None) is None:
        return
    try:
        from .sessions_service import mark_logged_out

        mark_logged_out(request.session.session_key)
    except Exception:
        logger.exception("Could not close the signed-in device row")
