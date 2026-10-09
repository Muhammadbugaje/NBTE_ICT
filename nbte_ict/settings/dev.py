"""Local development: SQLite, console email, in-process cache.

Also supports phone/live-device testing through an ngrok tunnel:
  - ALLOWED_HOSTS accepts any *.ngrok-free.app / *.ngrok-free.dev subdomain
  - CSRF_TRUSTED_ORIGINS accepts the same over HTTPS
  - SECURE_PROXY_SSL_HEADER lets request.is_secure() work behind ngrok's TLS
    termination, so secure-only cookies and redirects behave correctly.
"""
import os

from .base import *  # noqa

DEBUG = os.environ.get("DEBUG", "True") == "True"
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key")

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
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}
if os.environ.get("DATABASE_URL"):
    import dj_database_url
    DATABASES = {"default": dj_database_url.parse(os.environ["DATABASE_URL"])}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
# Temporary passwords are printed to the runserver console in dev.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "NBTE ICT <no-reply@localhost>")
SITE_URL = os.environ.get("SITE_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_TASK_ALWAYS_EAGER = os.environ.get("CELERY_EAGER", "True") == "True"

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