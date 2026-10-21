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
    "dashboards",
    "leave",
    "public",
    "showcase",   # UI overhaul: admin-uploadable sign-in / hero / banner images
    # --- NBTE Digital QA System (Master Plan §1; Phase 1 registers all six) ---
    "qa_core",        # shared engines: flows, two-step confirmation, references, formulas
    "institutions",   # Phase 2: institutions, documents, programmes, §6 actions
    "payments",       # Phase 2: bills, slips, providers, Finance queue
    "accreditation",  # Phase 2: SOP-01 onboarding (QA requests from Phase 3)
    "inspection",     # empty until Phase 5
    "complaints",     # empty until Phase 7
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # QA: requests for QA_PORTAL_HOST use the portal's own URLconf (§3, §16).
    "qa_core.middleware.QAPortalHostMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.ImpersonationMiddleware",
    # QA (Phase 2): read-only, time-boxed "view as institution" on the portal host (§6).
    "qa_core.impersonation.QAImpersonationMiddleware",
    "core.middleware.CurrentUserMiddleware",
    # QA: staff stay on the internal host, institutions and resource persons
    # on the portal (Master Plan §3). After impersonation, before password checks.
    "accounts.middleware.ExternalUserGuardMiddleware",
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
                "qa_core.context_processors.qa_nav",
                "qa_core.workspace.context",
                "qa_core.context_processors.qa_portal_link",
            ],
        },
    },
]

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["accounts.backends.RolePermissionBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "core:landing"

PDF_LOGO_PATH = BASE_DIR / "static" / "img" / "NBTE_logo.png"

# --- NBTE QA portal (Master Plan §3, §10.1 .env.example) ---------------------
# Institutions and resource persons sign in on their own subdomain, served by
# this same project and database. Hosts are compared without the port.
QA_PORTAL_HOST = os.environ.get("QA_PORTAL_HOST", "qa.nbte.gov.ng").strip().lower()
# Where staff are sent when they reach the portal (blank = same host as the
# request with the "qa." prefix removed).
INTERNAL_HOST = os.environ.get("INTERNAL_HOST", "").strip().lower()
QA_PORTAL_URLCONF = "qa_core.portal_urls"
# Unscoped queries on institution-bearing models raise in dev/test and log
# at ERROR in production (Master Plan §4).
QA_SCOPING_STRICT = os.environ.get("QA_SCOPING_STRICT", "False") == "True"
# Public link to the QA portal (landing page button and footer). Blank = built
# from QA_PORTAL_HOST with https.
QA_PORTAL_URL = os.environ.get("QA_PORTAL_URL", "").strip()
# Phase 2: QA e-mails are logged to the console and kept in the QA outbox,
# never sent. Set QA_EMAIL_DELIVERY=queue to hand them to the e-mail engine.
QA_EMAIL_DELIVERY = os.environ.get("QA_EMAIL_DELIVERY", "console").strip().lower()

# --- QA payments (Master Plan §10.1). Secrets live here, never in ConfigEntry.
REMITA_BASE_URL = os.environ.get("REMITA_BASE_URL", "https://remitademo.net/remita").rstrip("/")
REMITA_MERCHANT_ID = os.environ.get("REMITA_MERCHANT_ID", "")
REMITA_API_KEY = os.environ.get("REMITA_API_KEY", "")
REMITA_API_TOKEN = os.environ.get("REMITA_API_TOKEN", "")
REMITA_WEBHOOK_ALLOWED_IPS = [ip.strip() for ip in os.environ.get("REMITA_WEBHOOK_ALLOWED_IPS", "").split(",")
                              if ip.strip()]
PAYSTACK_BASE_URL = os.environ.get("PAYSTACK_BASE_URL", "https://api.paystack.co").rstrip("/")
PAYSTACK_PUBLIC_KEY = os.environ.get("PAYSTACK_PUBLIC_KEY", "")
PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYMENT_QR_SIGNING_KEY = os.environ.get("PAYMENT_QR_SIGNING_KEY", "")
PAYMENT_VERIFY_BASE_URL = os.environ.get("PAYMENT_VERIFY_BASE_URL", "").strip()
# Development only: when a provider's keys are missing, talk to an in-process
# simulator instead of the real API (never in production; see payments/providers).
QA_PAYMENTS_SIMULATE = os.environ.get("QA_PAYMENTS_SIMULATE", "False") == "True"

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

# `or` (not a default arg) so an empty env value falls back to the safe
# location instead of becoming Path(".") — the project root.
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT") or (BASE_DIR / "media"))
PROTECTED_MEDIA_ROOT = Path(os.environ.get("PROTECTED_MEDIA_ROOT") or (BASE_DIR / "protected_media"))

# Sanity guard: if MEDIA_ROOT ever resolves to the project root again,
# fail loudly instead of silently scanning over source code.
if MEDIA_ROOT.resolve() == BASE_DIR.resolve():
    raise RuntimeError(
        f"MEDIA_ROOT resolved to BASE_DIR ({MEDIA_ROOT}). Refusing to start. "
        "Set MEDIA_ROOT in .env to a subfolder such as 'media'."
    )

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

# =============================================================================
# Email delivery engine (doc 30 §5.4)
# =============================================================================
# Provider order (tried top-to-bottom by notifications.engine):
#   1. brevo_smtp  — Brevo SMTP relay over port 587 (preferred)
#   2. brevo       — Brevo HTTP API via django-anymail (fallback 1)
#   3. gmail       — Gmail SMTP app-password (fallback 2)
#
# The engine reads EMAIL_PROVIDERS and iterates the configured keys in the
# order returned by provider_order(). Override at runtime with the
# config-registry key "email.provider_override" if needed.
#
# Every credential comes from environment variables only. See .env.example.

ANYMAIL = {
    "BREVO_API_KEY": os.environ.get("BREVO_API_KEY", ""),
}

# Django's own default backend — used only by code that bypasses the engine
# (e.g. a direct `django.core.mail.send_mail()` call). Points at Brevo SMTP
# so those calls also go through the working transport.
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend",
)

# Shared SMTP credentials — used by both `brevo_smtp` and any direct Django send.
BREVO_SMTP_HOST = os.environ.get("BREVO_SMTP_HOST", "smtp-relay.brevo.com")
BREVO_SMTP_PORT = int(os.environ.get("BREVO_SMTP_PORT", "587"))
BREVO_SMTP_LOGIN = os.environ.get("BREVO_SMTP_LOGIN", "")
BREVO_SMTP_KEY = os.environ.get("BREVO_SMTP_KEY", "")

# Django's top-level SMTP settings — only consulted when EMAIL_BACKEND is
# Django's smtp backend and no per-provider OPTIONS are supplied.
EMAIL_HOST = BREVO_SMTP_HOST
EMAIL_PORT = BREVO_SMTP_PORT
EMAIL_USE_TLS = True
EMAIL_USE_SSL = False
EMAIL_HOST_USER = BREVO_SMTP_LOGIN
EMAIL_HOST_PASSWORD = BREVO_SMTP_KEY
EMAIL_TIMEOUT = 20

EMAIL_PROVIDERS = {
    # --- 1. Brevo SMTP relay (preferred) -------------------------------------
    "brevo_smtp": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": BREVO_SMTP_HOST,
            "port": BREVO_SMTP_PORT,
            "use_tls": True,
            "use_ssl": False,
            "username": BREVO_SMTP_LOGIN,
            "password": BREVO_SMTP_KEY,
            "timeout": 20,
        },
    },
    # --- 2. Brevo HTTP API via anymail ---------------------------------------
    "brevo": {
        "BACKEND": "anymail.backends.brevo.EmailBackend",
        "OPTIONS": {},
    },
    # --- 3. Gmail SMTP app-password ------------------------------------------
    "gmail": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": "smtp.gmail.com",
            "port": 587,
            "use_tls": True,
            "use_ssl": False,
            "username": os.environ.get("GMAIL_USER", ""),
            "password": os.environ.get("GMAIL_APP_PASSWORD", ""),
            "timeout": 20,
        },
    },
}

# --- Email branding ----------------------------------------------------------
EMAIL_LOGO_URL = os.environ.get(
    "EMAIL_LOGO_URL",
    "https://res.cloudinary.com/viepz5yp/image/upload/v1791034670/NBTE_logo.png",
)

# Email diagnostics are written to the process console.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "email": {
            "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        },
    },
    "handlers": {
        "email_console": {
            "class": "logging.StreamHandler",
            "formatter": "email",
        },
    },
    "loggers": {
        "notifications.engine": {
            "handlers": ["email_console"],
            "level": "INFO",
            "propagate": False,
        },
        "notifications.api": {
            "handlers": ["email_console"],
            "level": "INFO",
            "propagate": False,
        },
        "notifications.tasks": {
            "handlers": ["email_console"],
            "level": "INFO",
            "propagate": False,
        },
        "config.views": {
            "handlers": ["email_console"],
            "level": "INFO",
            "propagate": False,
        },
        "core.emails": {
            "handlers": ["email_console"],
            "level": "INFO",
            "propagate": False,
        },
        "accounts.user_views": {
            "handlers": ["email_console"],
            "level": "INFO",
            "propagate": False,
        },
        "core.email_utils": {
            "handlers": ["email_console"],
            "level": "INFO",
            "propagate": False,
        },
        # --- File lifecycle (moved INSIDE `loggers`) ---------------------
        "core.files": {
            "handlers": ["email_console"],
            "level": "INFO",
            "propagate": False,
        },
        "core.files_register": {
            "handlers": ["email_console"],
            "level": "INFO",
            "propagate": False,
        },
        "core.apps": {
            "handlers": ["email_console"],
            "level": "INFO",
            "propagate": False,
        },
        "core.media_scanner": {
            "handlers": ["email_console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}