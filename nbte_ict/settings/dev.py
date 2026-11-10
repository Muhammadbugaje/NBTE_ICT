"""Local development: SQLite, console Django fallback mail, in-process cache.

Also supports phone/live-device testing through an ngrok tunnel:
  - ALLOWED_HOSTS accepts any *.ngrok-free.app / *.ngrok-free.dev subdomain
  - CSRF_TRUSTED_ORIGINS accepts the same over HTTPS
  - SECURE_PROXY_SSL_HEADER lets request.is_secure() work behind ngrok's TLS
    termination, so secure-only cookies and redirects behave correctly.
Transactional email uses the Brevo-first provider engine configured in base.py.
"""
import os

from .base import *  # noqa

DEBUG = os.environ.get("DEBUG", "True") == "True"
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key")

# Phase 9: mobile API - browsable API pages and the X-NBTE-Door header only
# while DEBUG is on (production stays JSON only, doors by host only).
API_DEV_DOOR_HEADER = DEBUG
if DEBUG:
    REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_RENDERER_CLASSES": [  # noqa: F405
        "rest_framework.renderers.JSONRenderer", "rest_framework.renderers.BrowsableAPIRenderer"]}

# With DEBUG=False, `runserver` stops serving /static/ and /media/, so pages
# lose their styling (and the styled 404/500 pages look broken). In
# development we keep serving them so DEBUG=False behaves like production.
# Production (settings.prod) leaves this off: nginx serves the files.
SERVE_FILES_LOCALLY = os.environ.get("SERVE_FILES_LOCALLY", "True") == "True"

# ---------------------------------------------------------------------------
# ALLOWED_HOSTS
# ---------------------------------------------------------------------------
# Parse the env var; if absent, fall back to a permissive dev default.
_env_hosts = os.environ.get(
    "ALLOWED_HOSTS",
    "localhost,127.0.0.1,[::1],.localhost",
).strip()
_allowed = [h.strip() for h in _env_hosts.split(",") if h.strip()]

# Always allow common dev hosts + any ngrok subdomain, regardless of .env.
for host in (
    "localhost",
    "127.0.0.1",
    "[::1]",
    ".ngrok-free.app",     # any <slug>.ngrok-free.app
    ".ngrok-free.dev",     # any <slug>.ngrok-free.dev
    ".ngrok.io",           # legacy
    ".trycloudflare.com",  # Cloudflare quick tunnels (nice alternative)
):
    if host not in _allowed:
        _allowed.append(host)

ALLOWED_HOSTS = _allowed

# ---------------------------------------------------------------------------
# QA portal (Master Plan §3). In development the portal answers on
# qa.localhost (any *.localhost resolves to 127.0.0.1 in modern browsers), the
# internal system on localhost. Override with QA_PORTAL_HOST / INTERNAL_HOST.
# ---------------------------------------------------------------------------
QA_PORTAL_HOST = os.environ.get("QA_PORTAL_HOST", "qa.localhost").strip().lower()
INTERNAL_HOST = os.environ.get("INTERNAL_HOST", "localhost").strip().lower()
if QA_PORTAL_HOST and QA_PORTAL_HOST.split(":")[0] not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(QA_PORTAL_HOST.split(":")[0])
# Unscoped QA queries raise in development and tests (Master Plan §4).
QA_SCOPING_STRICT = os.environ.get("QA_SCOPING_STRICT", "True") == "True"
# Remita / Paystack keys are usually absent on a developer machine: use the
# in-process payment simulator unless real sandbox keys are set.
QA_PAYMENTS_SIMULATE = False
QA_PORTAL_URL = os.environ.get("QA_PORTAL_URL", "http://qa.localhost:8000").strip()

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
from django.core.exceptions import ImproperlyConfigured

_database_backend = os.environ.get("DATABASE_BACKEND", "").strip().lower()
_database_url = os.environ.get("DATABASE_URL", "").strip()
if not _database_backend:
    _database_backend = "url" if _database_url else "sqlite"

if _database_backend == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            # SQLite answers "database is locked" the moment a second writer
            # arrives while one write holds the file (dev runs stage tasks
            # inline and the bell polls at the same time). ``timeout`` makes a
            # competing connection WAIT up to 30 s for the lock instead of
            # failing immediately. WAL mode + a per-connection busy timeout
            # are set in core/apps.py on every connection.
            "OPTIONS": {"timeout": 30},
        }
    }
elif _database_backend == "url":
    if not _database_url:
        raise ImproperlyConfigured("DATABASE_BACKEND=url requires DATABASE_URL.")
    import dj_database_url

    DATABASES = {"default": dj_database_url.parse(_database_url, conn_max_age=600)}
else:
    raise ImproperlyConfigured("DATABASE_BACKEND must be 'sqlite' or 'url'.")

# v2.3: find out what is actually running on this machine. REDIS_URL may be
# set (copied from the server's .env) while no Redis runs locally: probe it
# once at start-up (a fast socket check) and fall back to an in-memory cache
# instead of failing every page with "Connection refused".
def _redis_reachable(url, timeout=0.4):
    import socket
    from urllib.parse import urlparse

    try:
        u = urlparse(url)
        if u.scheme == "unix":
            return os.path.exists(u.path)
        with socket.create_connection((u.hostname or "localhost", u.port or 6379), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


_redis_url = os.environ.get("REDIS_URL", "").strip()
REDIS_AVAILABLE = bool(_redis_url) and _redis_reachable(_redis_url)
if REDIS_AVAILABLE:
    CACHES = {"default": {"BACKEND": "core.cache_backends.ResilientRedisCache",
                          "LOCATION": _redis_url, "KEY_PREFIX": "nbte", "TIMEOUT": 300,
                          "OPTIONS": {"socket_connect_timeout": 1, "socket_timeout": 1}}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    if _redis_url and os.environ.get("RUN_MAIN") != "true":  # say it once, not per autoreload
        import sys

        print(f"[dev] Redis at {_redis_url.split('@')[-1]} is not running: using the in-memory cache "
              "and running background tasks inline.", file=sys.stderr)

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
# QA e-mail delivery is controlled by QA_EMAIL_DELIVERY (provider delivery by
# default; set it to "console" explicitly for a log-only local run).
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "NBTE ICT <no-reply@localhost>")
SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_EAGER", "True") == "True"
_broker = os.environ.get("CELERY_BROKER_URL", "").strip() or _redis_url or "redis://localhost:6379/0"
if not CELERY_TASK_ALWAYS_EAGER and _broker.startswith("redis") and not _redis_reachable(_broker):
    CELERY_TASK_ALWAYS_EAGER = True   # no broker running here: run tasks inline instead of hanging

# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
# We serve over plain HTTP locally, so the session cookie isn't "Secure".
# ngrok terminates TLS and forwards HTTP to us, so we tell Django to trust
# the X-Forwarded-Proto header when deciding whether the request is secure.
CSRF_COOKIE_SECURE = False
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"

CSRF_TRUSTED_ORIGINS = [
    # Loopback (HTTP)
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    # ngrok (HTTPS) — leading dot = any subdomain
    "https://*.ngrok-free.app",
    "https://*.ngrok-free.dev",
    "https://*.ngrok.io",
    # Cloudflare quick tunnels
    "https://*.trycloudflare.com",
]

# Allow env to add more (comma-separated).
_extra_origins = os.environ.get("CSRF_TRUSTED_ORIGINS", "")
if _extra_origins:
    CSRF_TRUSTED_ORIGINS += [
        o.strip() for o in _extra_origins.split(",") if o.strip()
    ]

# ---------------------------------------------------------------------------
# Behind ngrok's TLS-terminating proxy
# ---------------------------------------------------------------------------
# ngrok forwards with X-Forwarded-Proto: https. Without this, Django thinks
# the request is plain HTTP and refuses to issue Secure-only cookies, or
# worse, redirects you to http://<ngrok>/… in a loop.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# Dev only — never enable in production.
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
# v2.1 X33: without FIELD_ENCRYPTION_KEY in .env, development (and the test
# runner, which forces DEBUG off) derives a key from SECRET_KEY. Production
# refuses to start without the real key (prod.py REQUIRED_ENV).
FIELD_ENCRYPTION_DEV_FALLBACK = True
