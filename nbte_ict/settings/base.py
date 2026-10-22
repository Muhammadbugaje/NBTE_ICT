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
    # Admin theme (Phase 8): django-unfold must load before django.contrib.admin.
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
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
    "announcements",  # Phase 9: announcements (official staff channel)
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
    "backups",        # Phase 8 (Master Plan §22): backup and restore of the whole system
    # Phase 9: mobile API (Flutter app) - see api/README.md
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Phase 9: /api/ responses get X-NBTE-Min-App-Version and JSON error bodies.
    "api.middleware.ApiResponseMiddleware",
    # QA: requests for QA_PORTAL_HOST use the portal's own URLconf (§3, §16).
    "qa_core.middleware.QAPortalHostMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Phase 9: last-seen time for "Signed-in devices" (cheap; before impersonation swaps the user).
    "accounts.middleware.LastSeenMiddleware",
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
# Remita's JSON API sits under this path on the base URL (RRR generation and the
# status query); the browser checkout (/ecomm/finalize.reg) does not.
REMITA_API_PATH = "/" + os.environ.get("REMITA_API_PATH", "/exapp/api/v1/send/api").strip("/")
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

# Folders (relative to a media root) that hold generated or served assets.
# The orphan scanner never treats a file inside them as an orphan. Use
# "public:<folder>" or "protected:<folder>" to limit an entry to one root.
# More can be added at runtime with the `files.orphan_protected_folders`
# setting (see MEDIA_STORAGE.md).
MEDIA_SCAN_PROTECTED_FOLDERS = [
    "qa/guides",        # institution guides (generate_institution_guides)
    "guides",
    "docs",
    "user-guides",
    "showcase",         # login showcase / gallery images
    "import-tmp",       # import previews (swept hourly by their own task)
    "staff/imports",    # nominal-roll uploads held in the session
]

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

# --- Phase 9: voice notes (see VOICE_NOTES.md) --------------------------------
# "" = auto: faster_whisper when the package is installed, otherwise "none".
# Other values: "faster_whisper" | "none" | "dummy" (tests only).
VOICE_TRANSCRIBE_BACKEND = os.environ.get("VOICE_TRANSCRIBE_BACKEND", "")
# Whisper model size ("tiny", "base", "small", ...) or a local model path.
VOICE_WHISPER_MODEL = os.environ.get("VOICE_WHISPER_MODEL", "small")
VOICE_MAX_SECONDS = 900  # 15 minutes

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

# ---------------------------------------------------------------------------
# Django admin theme (Phase 8): django-unfold + NBTE look.
# Brand green scale from static/css/tokens.css (--brand-*; 500 is the official
# NBTE green). "base" is a soft, slightly warm grey so surfaces read as milky
# off-white rather than stark white. Callbacks live in core/admin_dashboard.py.
# ---------------------------------------------------------------------------
from django.templatetags.static import static as _static  # noqa: E402
from django.urls import reverse_lazy as _rl  # noqa: E402


def _admin_link(title, icon, app, model, badge=None):
    perm = f"{app}.view_{model}"
    item = {
        "title": title,
        "icon": icon,
        "link": _rl(f"admin:{app}_{model}_changelist"),
        "permission": (lambda request, p=perm: request.user.is_superuser or request.user.has_perm(p)
                       or request.user.has_perm(p.replace(".view_", ".change_"))),
    }
    if badge:
        item["badge"] = f"core.admin_dashboard.{badge}"
    return item


UNFOLD = {
    "SITE_TITLE": "NBTE Admin",
    "SITE_HEADER": "NBTE Admin",
    "SITE_SUBHEADER": "ICT System · Digital QA",
    "SITE_URL": "/home/",
    "SITE_LOGO": lambda request: _static("img/NBTE_logo.png"),
    "SITE_ICON": lambda request: _static("img/NBTE_logo.png"),
    "SITE_SYMBOL": "verified",
    "SITE_FAVICONS": [
        {"rel": "icon", "sizes": "any", "type": "image/png", "href": lambda request: _static("img/NBTE_logo.png")},
    ],
    "SITE_DROPDOWN": [
        {"icon": "home", "title": "Staff system", "link": "/home/"},
        {"icon": "monitoring", "title": "Super Admin dashboard", "link": "/dashboards/superadmin/"},
        {"icon": "fact_check", "title": "QA settings", "link": "/qa/settings/"},
    ],
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "SHOW_BACK_BUTTON": True,
    "ENVIRONMENT": "core.admin_dashboard.environment_callback",
    "DASHBOARD_CALLBACK": "core.admin_dashboard.dashboard_callback",
    "BORDER_RADIUS": "10px",
    "STYLES": [lambda request: _static("admin-theme/nbte-admin.css") + "?v=2"],
    "SCRIPTS": [lambda request: _static("admin-theme/nbte-admin.js") + "?v=3"],
    "COLORS": {
        "base": {
            "50": "#fafaf8", "100": "#f4f4f1", "200": "#e8e8e3", "300": "#d5d5ce", "400": "#a3a39b",
            "500": "#74746c", "600": "#57574f", "700": "#43433d", "800": "#2a2a26", "900": "#1b1b19",
            "950": "#0f0f0e",
        },
        "primary": {
            "50": "#f0f7f2", "100": "#d9ebde", "200": "#b3d7bd", "300": "#86bd95", "400": "#4fa66d",
            "500": "#2d8a56", "600": "#1f6b3e", "700": "#195730", "800": "#134326", "900": "#0d301a",
            "950": "#061c0e",
        },
        "font": {
            "subtle-light": "var(--color-base-500)",
            "subtle-dark": "var(--color-base-400)",
            "default-light": "var(--color-base-600)",
            "default-dark": "var(--color-base-300)",
            "important-light": "var(--color-base-900)",
            "important-dark": "var(--color-base-100)",
        },
    },
    "COMMAND": {"search_models": True, "show_history": True},
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Overview",
                "items": [
                    {"title": "Dashboard", "icon": "space_dashboard", "link": _rl("admin:index")},
                    {"title": "Back to the staff system", "icon": "arrow_back", "link": "/home/"},
                ],
            },
            {
                "title": "People & accounts",
                "separator": True,
                "items": [
                    _admin_link("Users", "group", "accounts", "user"),
                    _admin_link("Roles", "admin_panel_settings", "accounts", "role"),
                    _admin_link("Groups", "groups", "auth", "group"),
                    _admin_link("Permissions", "key", "auth", "permission"),
                ],
            },
            {
                "title": "Staff & HR",
                "separator": True,
                "collapsible": True,
                "items": [
                    _admin_link("Staff", "badge", "staff", "staff"),
                    _admin_link("Departments", "corporate_fare", "staff", "department"),
                    _admin_link("Offices", "meeting_room", "staff", "office"),
                    _admin_link("Designations", "military_tech", "staff", "designation"),
                    _admin_link("Promotions", "trending_up", "staff", "promotion"),
                    _admin_link("Transfers", "swap_horiz", "staff", "stafftransfer"),
                    _admin_link("Leadership", "workspace_premium", "staff", "executivetenure"),
                    _admin_link("Attendance", "how_to_reg", "attendance", "attendancerecord"),
                    _admin_link("Attendance sites", "location_on", "attendance", "attendancelocation"),
                    _admin_link("Leave", "beach_access", "leave", "leaveapplication"),
                    _admin_link("Nominal roll imports", "upload_file", "staff", "importsession"),
                ],
            },
            {
                "title": "Tasks & tickets",
                "separator": True,
                "collapsible": True,
                "items": [
                    _admin_link("Tasks", "task_alt", "tasks", "task", badge="open_tasks_badge"),
                    _admin_link("Task categories", "category", "tasks", "taskcategory"),
                    _admin_link("Tickets", "support_agent", "tickets", "ticket", badge="open_tickets_badge"),
                    _admin_link("Ticket categories", "label", "tickets", "ticketcategory"),
                    _admin_link("Requests", "approval", "workflow", "request"),
                    _admin_link("Request types", "list_alt", "workflow", "requesttype"),
                    _admin_link("Approval chains", "account_tree", "workflow", "approvalchain"),
                ],
            },
            {
                "title": "Communication",
                "separator": True,
                "collapsible": True,
                "items": [
                    _admin_link("Notifications", "notifications", "notifications", "notification"),
                    _admin_link("Email log", "mail", "notifications", "emaillog"),
                    _admin_link("Message threads", "forum", "messaging", "thread"),
                    _admin_link("Contact messages", "inbox", "core", "contactmessage", badge="contact_badge"),
                    _admin_link("Announcements", "campaign", "portal", "announcement"),
                    _admin_link("Events", "event", "portal", "event"),
                    _admin_link("Reminders", "alarm", "portal", "reminder"),
                ],
            },
            {
                "title": "Assets",
                "separator": True,
                "collapsible": True,
                "items": [
                    _admin_link("Assets", "devices", "assets", "asset"),
                    _admin_link("Assignments", "assignment_ind", "assets", "assetassignment"),
                    _admin_link("Issues", "build", "assets", "assetissue"),
                    _admin_link("Categories", "category", "assets", "assetcategory"),
                ],
            },
            {
                "title": "Portal & public site",
                "separator": True,
                "collapsible": True,
                "items": [
                    _admin_link("Documents", "description", "portal", "document"),
                    _admin_link("Gallery", "photo_library", "portal", "galleryalbum"),
                    _admin_link("Awards", "emoji_events", "portal", "award"),
                    _admin_link("Holidays", "calendar_month", "portal", "holiday"),
                    _admin_link("Showcase images", "imagesmode", "showcase", "showcaseimage"),
                ],
            },
            {
                "title": "QA — institutions",
                "separator": True,
                "collapsible": True,
                "items": [
                    _admin_link("Institutions", "account_balance", "institutions", "institution"),
                    _admin_link("Programmes", "school", "institutions", "programme"),
                    _admin_link("Institution documents", "folder_shared", "institutions", "institutiondocument"),
                    _admin_link("Institution users", "manage_accounts", "institutions", "institutionuser"),
                    _admin_link("View-as sessions", "visibility", "institutions", "impersonationsession"),
                ],
            },
            {
                "title": "QA — applications & requests",
                "separator": True,
                "collapsible": True,
                "items": [
                    _admin_link("Onboarding applications", "app_registration", "accreditation", "application"),
                    _admin_link("Departmental clearance", "rule", "accreditation", "departmentalclearance"),
                    _admin_link("QA requests", "assignment", "accreditation", "qarequest"),
                    _admin_link("SSQs", "checklist", "accreditation", "ssq"),
                    _admin_link("Queries", "help", "accreditation", "query"),
                    _admin_link("Institution requests", "move_to_inbox", "institutions", "institutionrequest"),
                ],
            },
            {
                "title": "QA — cases & inspection",
                "separator": True,
                "collapsible": True,
                "items": [
                    _admin_link("QA cases", "folder_open", "accreditation", "qacase", badge="open_cases_badge"),
                    _admin_link("Cost sheets", "request_quote", "accreditation", "costsheet"),
                    _admin_link("Fund releases", "savings", "accreditation", "fundrelease"),
                    _admin_link("Inspections", "travel_explore", "inspection", "inspection"),
                    _admin_link("QA mappings", "hub", "inspection", "qamapping"),
                    _admin_link("Post-visit reports", "summarize", "inspection", "postvisitreport"),
                    _admin_link("Resource persons", "person_search", "inspection", "resourceperson"),
                ],
            },
            {
                "title": "QA — payments & complaints",
                "separator": True,
                "collapsible": True,
                "items": [
                    _admin_link("Payment verifications", "payments", "payments", "paymentverification",
                                badge="payments_badge"),
                    _admin_link("Bills", "receipt_long", "payments", "paymentobligation"),
                    _admin_link("Refunds", "currency_exchange", "payments", "refund"),
                    _admin_link("Complaints", "report", "complaints", "complaint", badge="complaints_badge"),
                    _admin_link("Complaint layers", "layers", "complaints", "complaintlayer"),
                ],
            },
            {
                "title": "QA — settings & flows",
                "separator": True,
                "collapsible": True,
                "items": [
                    _admin_link("Flows", "schema", "qa_core", "flowdefinition"),
                    _admin_link("Two-step confirmations", "verified_user", "qa_core", "pendingaction"),
                    _admin_link("Action policies", "policy", "qa_core", "actionpolicy"),
                    _admin_link("Reference formats", "tag", "qa_core", "referenceformat"),
                    _admin_link("QA timeline", "timeline", "qa_core", "timelineevent"),
                    _admin_link("QA types", "category", "accreditation", "qatype"),
                    _admin_link("Fee schedules", "price_change", "payments", "feeschedule"),
                ],
            },
            {
                "title": "System",
                "separator": True,
                "collapsible": True,
                "items": [
                    _admin_link("Settings", "tune", "config", "configentry"),
                    _admin_link("Audit log", "history", "core", "auditlogentry"),
                    _admin_link("Email quota", "speed", "notifications", "emailquotatracker"),
                    {"title": "File storage", "icon": "folder", "link": "/settings/files/",
                     "permission": lambda request: request.user.is_superuser},
                ],
            },
        ],
    },
}


# =============================================================================
# Phase 9: mobile API
# =============================================================================
# One `api` app serves both front doors at /api/v1/ (internal host: staff;
# portal host: institutions and resource persons). JWT only - no session
# authentication, so no CSRF. dev.py adds the browsable renderer when DEBUG.
from datetime import timedelta as _timedelta  # noqa: E402

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["api.authentication.ApiJWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "api.permissions.CorrectDoor",
        "api.permissions.PasswordChangeCompleted",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "api.exceptions.handler",
    "DEFAULT_PAGINATION_CLASS": "api.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "30/min", "user": "300/min", "login": "10/min"},
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": _timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": _timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "AUTH_HEADER_TYPES": ("Bearer",),
    # Adds the custom `door` and `user_type` claims (api/tokens.py).
    "TOKEN_OBTAIN_SERIALIZER": "api.tokens.DoorTokenObtainPairSerializer",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Digital NBTE API",
    "DESCRIPTION": "REST API for the Digital NBTE mobile app (staff and QA portal doors).",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # Swagger UI is served from our own static files (pinned 5.17.14) so the
    # docs page works without internet access.
    "SWAGGER_UI_DIST": "/" + STATIC_URL.strip("/") + "/vendor/swagger-ui",
    "SWAGGER_UI_FAVICON_HREF": "/" + STATIC_URL.strip("/") + "/vendor/swagger-ui/favicon-32x32.png",
    "ENUM_NAME_OVERRIDES": {
        "DoorEnum": ["staff", "portal"],
        "MemoStatusEnum": ["draft", "awaiting", "returned", "published", "rejected", "cancelled"],
        "MemoVisibilityEnum": ["specific_user", "all_staff", "all_directors", "department", "role", "individuals"],
        "ComplaintMessageVisibilityEnum": ["public", "internal"],
        # --- qa_staff ---
        "QAFirstPassDecisionEnum": ["approved", "rejected", "needs_info"],
        "QASecondCheckDecisionEnum": ["approved", "rejected"],
        "QAFinalDecisionEnum": ["confirmed", "rejected"],
        "QAClearDecisionEnum": ["clear", "return"],
        "QASSQDecisionEnum": ["accept", "return"],
        # --- end qa_staff ---
    },
}

# Oldest app version the server still supports (header on every API response).
API_MIN_APP_VERSION = "1.0.0"
# Development only: `X-NBTE-Door: portal` on /api/ requests selects the portal
# door (qa_core/hosts.py). Ignored unless DEBUG is also on; dev.py turns it on.
API_DEV_DOOR_HEADER = False
