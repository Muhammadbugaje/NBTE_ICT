"""Production: database + secrets strictly from environment variables (doc 34 §2).
DATABASE_URL selects PostgreSQL or MySQL; no DB-specific features are used in code."""
import os

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa


def _require(name):
    value = os.environ.get(name)
    if not value:
        raise ImproperlyConfigured(f"Environment variable {name} is required in production.")
    return value


def _require_one_of(*names):
    """Require at least one of the given env vars (used for email providers -
    the engine works with either alone, but silently broken email is worse than
    a hard startup failure)."""
    for name in names:
        if os.environ.get(name):
            return
    raise ImproperlyConfigured(
        f"At least one of {', '.join(names)} must be set in production."
    )


DEBUG = False
SECRET_KEY = _require("SECRET_KEY")
ALLOWED_HOSTS = [h.strip() for h in _require("ALLOWED_HOSTS").split(",")]
# QA portal subdomain (Master Plan §3): served by this deployment too.
if QA_PORTAL_HOST and QA_PORTAL_HOST.split(":")[0] not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(QA_PORTAL_HOST.split(":")[0])
SITE_URL = _require("SITE_URL")

DATABASES = {
    "default": dj_database_url.parse(_require("DATABASE_URL"), conn_max_age=600)
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _require("REDIS_URL"),
        "KEY_PREFIX": "nbte_ict",
    }
}

CELERY_BROKER_URL = _require("CELERY_BROKER_URL")
CELERY_TASK_ALWAYS_EAGER = False
USE_X_ACCEL_REDIRECT = os.environ.get("USE_X_ACCEL_REDIRECT", "True") == "True"
DEFAULT_FROM_EMAIL = _require("DEFAULT_FROM_EMAIL")

# Refuse to start in prod without at least one complete email provider.
if not os.environ.get("BREVO_API_KEY") and not (
    os.environ.get("GMAIL_USER") and os.environ.get("GMAIL_APP_PASSWORD")
):
    raise ImproperlyConfigured(
        "Configure BREVO_API_KEY or both GMAIL_USER and GMAIL_APP_PASSWORD "
        "for production email delivery."
    )

# --- Security ----------------------------------------------------------------
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = False          # HTMX reads the CSRF token from the cookie
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "True") == "True"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# QA payments: the in-process simulator is a development tool only.
QA_PAYMENTS_SIMULATE = False
