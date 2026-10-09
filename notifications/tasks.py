from celery import shared_task
from django.utils import timezone

from . import engine
from .models import EmailLog

MAX_RETRIES = 5

# User-facing categories (credentials, reset) must not wait 30 minutes on a bad
# day - the user is staring at a login screen. Everything else uses a slower,
# more economical backoff. Quota exhaustion always waits an hour: the counter
# resets at local midnight, so nothing else helps.
_USER_FACING = ("credentials", "reset")
_USER_FACING_BASE_SECONDS = 15
_DEFAULT_BASE_SECONDS = 60
_QUOTA_BASE_SECONDS = 3600


@shared_task(bind=True, max_retries=MAX_RETRIES)
def send_email_task(self, log_id, subject, recipients, html, text):
    """Bodies travel in the task args (broker), not the database. One-shot
    delivery with failover happens inside engine.send; this layer retries with
    backoff when every provider fails or the day's quota is gone."""
    log = EmailLog.objects.get(pk=log_id)
    log.attempts += 1
    trail = []
    try:
        provider = engine.send(subject, recipients, html, text, category=log.category, trail=trail)
    except engine.DeliveryError as exc:
        log.last_error = str(exc)[:2000]
        final = self.request.retries >= MAX_RETRIES
        log.status = EmailLog.FAILED if final else EmailLog.RETRYING
        log.attempt_trail = (log.attempt_trail or []) + [dict(t, attempt=log.attempts) for t in trail]
        log.save(update_fields=["attempts", "last_error", "status", "attempt_trail"])
        if final:
            return "failed"
        if isinstance(exc, engine.QuotaExhausted):
            base = _QUOTA_BASE_SECONDS
        elif log.category in _USER_FACING:
            base = _USER_FACING_BASE_SECONDS
        else:
            base = _DEFAULT_BASE_SECONDS
        raise self.retry(exc=exc, countdown=base * (2 ** self.request.retries))
    log.status, log.provider, log.sent_at, log.last_error = EmailLog.SENT, provider, timezone.now(), ""
    log.attempt_trail = (log.attempt_trail or []) + [dict(t, attempt=log.attempts) for t in trail]
    log.save(update_fields=["attempts", "status", "provider", "sent_at", "last_error", "attempt_trail"])
    return provider