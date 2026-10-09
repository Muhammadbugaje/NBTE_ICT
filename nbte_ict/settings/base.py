"""Settings shared by every environment. Secrets and deployment identity come
from environment variables only (doc 30 §5.3 / doc 31 §10) - never config_registry.

`.env` is loaded at import time if present (silently no-ops if absent, so
production running purely on real env vars is unaffected)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "accounts",
    "staff",
    "config",
    "notifications",
    "messaging",
    "workflow",
    "assets",
    "tickets",
    "tasks",
    "portal",
    "attendance",
    "dashboards"
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.ImpersonationMiddleware",
    "core.middleware.CurrentUserMiddleware",
    "accounts.middleware.ForcePasswordChangeMiddleware",
    "core.middleware.DrainHtmxMessagesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "nbte_ict.urls"
WSGI_APPLICATION = "nbte_ict.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site",
                "core.context_processors.sidebar_counts",
                "config.context_processors.site_info", 
            ],
        },
    },
]

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.RolePermissionBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "core:landing"  # ← changed from "login" (send to public landing)

PDF_LOGO_PATH = BASE_DIR / "static" / "img" / "NBTE_logo.png"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- Upload size limits ------------------------------------------------------
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024

# --- File storage (doc 34 §2.1) ----------------------------------------------
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))
PROTECTED_MEDIA_ROOT = Path(os.environ.get("PROTECTED_MEDIA_ROOT", BASE_DIR / "protected_media"))
STORAGES = {
    "default": {"BACKEND": "core.storage.PublicMediaStorage"},
    "restricted": {"BACKEND": "core.storage.RestrictedMediaStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SITE_NAME = os.environ.get("SITE_NAME", "NBTE ICT Directorate")
CONFIG_CACHE_TTL = 300

PROTECTED_MEDIA_URL = "/protected/"
USE_X_ACCEL_REDIRECT = os.environ.get("USE_X_ACCEL_REDIRECT", "False") == "True"

# --- Celery (doc 31 §6) ------------------------------------------------------
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_EAGER_PROPAGATES = False
CELERY_TASK_TRACK_STARTED = True

# --- Email delivery engine (doc 30 §5.4) -------------------------------------
# Two providers, tried in order: Brevo (API), then Gmail (SMTP).
# Read by core.emails.send() — not by Django's EMAIL_BACKEND, so ticket and
# transactional emails behave identically in every environment.
#
# Credentials come from environment variables only. See .env.example.
ANYMAIL = {
    "BREVO_API_KEY": os.environ.get("BREVO_API_KEY", ""),
}

EMAIL_PROVIDERS = {
    "brevo": {
        "BACKEND": "anymail.backends.brevo.EmailBackend",
        "OPTIONS": {},
    },
    "gmail": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": "smtp.gmail.com",
            "port": 587,
            "use_tls": True,
            "username": os.environ.get("GMAIL_USER", ""),
            "password": os.environ.get("GMAIL_APP_PASSWORD", ""),
        },
    },
}

# --- Email branding ----------------------------------------------------------
# Default logo URL for email templates. Can be overridden via the config
# registry key `email.logo_url` from the admin UI.
EMAIL_LOGO_URL = os.environ.get(
    "EMAIL_LOGO_URL",
    "https://res.cloudinary.com/viepz5yp/image/upload/v1791034670/NBTE_logo.png",
)
