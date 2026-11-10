"""Production: database + secrets strictly from environment variables (doc 34 §2).
DATABASE_URL selects PostgreSQL or MySQL; no DB-specific features are used in code."""
import os

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa


# Phase 0 (X8 slice, S-27): fail fast, and name EVERY missing variable at
# once (see .env.example), instead of one per restart.
REQUIRED_ENV = (
    "SECRET_KEY", "ALLOWED_HOSTS", "DATABASE_URL", "SITE_URL", "DEFAULT_FROM_EMAIL",
    "REDIS_URL", "CELERY_BROKER_URL", "FIELD_ENCRYPTION_KEY",
)
_missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
if _missing:
    raise ImproperlyConfigured(
        "Missing required environment variable(s) for production: " + ", ".join(_missing)
        + ". Copy .env.example to .env (or set them in the service environment)."
    )
_secret = os.environ["SECRET_KEY"].strip()
if len(_secret) < 32 or _secret.startswith(("django-insecure", "dev-only", "change-me")):
    raise ImproperlyConfigured(
        "SECRET_KEY is too weak for production: use at least 32 random characters "
        "(python -c 'import secrets; print(secrets.token_urlsafe(50))')."
    )


def _require(name):
    value = os.environ.get(name, "").strip()
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
# Extra trusted origins for CSRF (comma-separated, with scheme), e.g. the
# portal host when it is served from a different domain.
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

# --- Database (v2.2: tuned for a small server and a remote managed Postgres) ---
# * Persistent connections (DB_CONN_MAX_AGE, default 300 s) with a health check
#   before reuse, so a connection the provider dropped never fails a request.
# * TLS required for a remote Postgres unless the URL already says otherwise.
# * A statement timeout (DB_STATEMENT_TIMEOUT_MS, default 30 s): one runaway
#   query cannot pin a worker and a connection for minutes.
# * DB_POOLER=True when connecting through PgBouncer in transaction mode
#   (disables server-side cursors, which a pooler cannot carry).
DATABASES = {
    "default": dj_database_url.parse(_require("DATABASE_URL"),
                                     conn_max_age=int(os.environ.get("DB_CONN_MAX_AGE", "300")),
                                     conn_health_checks=True)
}
if DATABASES["default"]["ENGINE"].endswith("postgresql"):
    _db_opts = DATABASES["default"].setdefault("OPTIONS", {})
    _host = DATABASES["default"].get("HOST") or ""
    if "sslmode" not in _db_opts and _host not in ("", "localhost", "127.0.0.1") \
            and os.environ.get("DB_SSLMODE", "require") != "disable":
        _db_opts["sslmode"] = os.environ.get("DB_SSLMODE", "require")
    _db_opts.setdefault("connect_timeout", int(os.environ.get("DB_CONNECT_TIMEOUT", "10")))
    _timeout_ms = int(os.environ.get("DB_STATEMENT_TIMEOUT_MS", "30000"))
    if _timeout_ms > 0:
        _db_opts["options"] = (_db_opts.get("options", "") + f" -c statement_timeout={_timeout_ms}").strip()
    if os.environ.get("DB_POOLER", "False") == "True":
        DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

# --- Cache (v2.2): Redis that fails fast and degrades instead of failing the site.
CACHES = {
    "default": {
        "BACKEND": "core.cache_backends.ResilientRedisCache",
        "LOCATION": _require("REDIS_URL"),
        "KEY_PREFIX": "nbte_ict",
        "TIMEOUT": 300,
        "OPTIONS": {
            "socket_connect_timeout": 2,
            "socket_timeout": 2,
            "retry_on_timeout": False,
            "health_check_interval": 30,
            "max_connections": int(os.environ.get("REDIS_MAX_CONNECTIONS", "20")),
        },
    }
}
# Sessions are read on every request: keep them in Redis, written through to
# the database (nobody is signed out if Redis restarts).
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

CELERY_BROKER_URL = _require("CELERY_BROKER_URL")
CELERY_TASK_ALWAYS_EAGER = False
# v2.2: lean Celery for a small server and a small Redis.
CELERY_TASK_IGNORE_RESULT = True              # no result keys piling up in Redis
CELERY_RESULT_BACKEND = None
CELERY_WORKER_CONCURRENCY = int(os.environ.get("CELERY_CONCURRENCY", "2"))
CELERY_WORKER_PREFETCH_MULTIPLIER = 1         # long tasks don't hoard the queue
CELERY_TASK_ACKS_LATE = True                  # a task survives a worker restart
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_MAX_TASKS_PER_CHILD = 200       # recycle children (memory creep)
CELERY_WORKER_MAX_MEMORY_PER_CHILD = int(os.environ.get("CELERY_MAX_MEMORY_KB", "250000"))
CELERY_TASK_SOFT_TIME_LIMIT = 600
CELERY_TASK_TIME_LIMIT = 900
CELERY_BROKER_POOL_LIMIT = 4
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600, "socket_timeout": 10,
                                   "socket_connect_timeout": 5, "retry_on_timeout": True}
CELERY_WORKER_SEND_TASK_EVENTS = False
CELERY_TASK_SEND_SENT_EVENT = False

# Client address (rate limits, admin IP guard, Remita webhook check). Render
# sits behind Cloudflare, which sets CF-Connecting-IP and overwrites any
# client copy; nginx (deploy/nginx.conf) sets X-Real-IP.
CLIENT_IP_HEADER = os.environ.get(
    "CLIENT_IP_HEADER", "HTTP_CF_CONNECTING_IP" if os.environ.get("RENDER") else "HTTP_X_REAL_IP")
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
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# Cached template loading is automatic with DEBUG off; upload sizes stay as base.py.
# QA payments: the in-process simulator is a development tool only.
QA_PAYMENTS_SIMULATE = False
# v2.1 F.4 (T-SEC-8): fail fast when a gateway named in PAYMENTS_GATEWAYS has
# no keys (the qa.payment.<provider>.enabled switches are checked by the
# payments.E001/E002 system check on migrate/check, since they live in the DB).
_GATEWAY_ENV = {"remita": ("REMITA_MERCHANT_ID", "REMITA_API_KEY", "REMITA_BASE_URL"),
                "paystack": ("PAYSTACK_PUBLIC_KEY", "PAYSTACK_SECRET_KEY", "PAYSTACK_BASE_URL")}
_unknown = [g for g in PAYMENTS_GATEWAYS if g not in _GATEWAY_ENV]  # noqa: F405
if _unknown:
    raise ImproperlyConfigured(f"PAYMENTS_GATEWAYS names unknown gateway(s): {', '.join(_unknown)} "
                               "(use remita, paystack).")
_gw_missing = [name for g in PAYMENTS_GATEWAYS for name in _GATEWAY_ENV[g]  # noqa: F405
               if not os.environ.get(name, "").strip()]
if _gw_missing:
    raise ImproperlyConfigured("Payment gateway keys missing for production: " + ", ".join(_gw_missing)
                               + ". See .env.example and PAYMENTS_LIVE_TEST.md.")

# v2.1 Phase A (V7, T-CONS-7): hashed static file names in production, so
# templates no longer carry hand-edited ?v= cache-busters. Development and the
# test suite keep the plain StaticFilesStorage from base.py (no manifest
# needed). Run `python manage.py collectstatic` on every deploy.
STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "core.storage.NBTEManifestStaticFilesStorage"},
}

# v2.2 (Phase G): serve /static/ from Django with long cache headers when no
# nginx is in front (e.g. Render). WhiteNoise serves the hashed files from
# STATIC_ROOT with "Cache-Control: max-age=31536000, immutable". Set
# WHITENOISE=False when nginx serves /static/ itself (deploy/nginx.conf).
if os.environ.get("WHITENOISE", "True") == "True":
    try:
        import whitenoise  # noqa: F401

        MIDDLEWARE = list(MIDDLEWARE)  # noqa: F405
        MIDDLEWARE.insert(MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,
                          "whitenoise.middleware.WhiteNoiseMiddleware")
        WHITENOISE_MAX_AGE = 31536000
    except ImportError:  # pragma: no cover - whitenoise not installed
        pass
