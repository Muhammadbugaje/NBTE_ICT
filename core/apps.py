import logging

from django.apps import AppConfig, apps


logger = logging.getLogger(__name__)


def _sqlite_pragmas(sender, connection, **_kwargs):
    """Concurrency settings for every new SQLite connection (dev).

    WAL lets readers proceed while a writer holds the file, and a busy
    timeout makes a competing writer wait briefly instead of raising
    'database is locked' - which previously blew up session saves on the
    notification poll as SessionInterrupted 400s. WAL is persistent on the
    database file, but we set it idempotently here so fresh copies (test
    databases, restored backups) get it too.
    """
    if getattr(connection, "vendor", "") != "sqlite":
        return
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=15000")


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"

    def ready(self):
        logger.debug("core.apps: ready() starting")

        from django.db.backends.signals import connection_created

        connection_created.connect(_sqlite_pragmas, dispatch_uid="core_sqlite_pragmas")

        from . import fastcache
        fastcache.install()  # signal-driven cache invalidation for the QA area

        # 1. Register file-owning models with the cleanup signals.
        from .files_register import install_all
        install_all()

        # 2. Register audit signals for every AUDITED model.
        from . import audit
        from . import signals  # noqa: F401  (side-effect import)

        count = 0
        for model in apps.get_models():
            if getattr(model, "AUDITED", False):
                audit.register(model)
                count += 1

        # 3. Phase 8 admin theme: any ModelAdmin still on the stock base class
        #    (apps this project does not edit) gets the unfold look too.
        try:
            from .admin_base import theme_registry
            themed = theme_registry()
            from django.contrib import admin as _admin
            _admin.site.index_title = "Dashboard"
            if themed:
                logger.debug("core.apps: themed %d stock ModelAdmin(s)", themed)
        except Exception:  # the admin theme must never stop the site starting
            logger.exception("core.apps: admin theme registry pass failed")

        # 4. v2.1 Phase B: sign-in signal (session ages, optional e-mail code),
        #    seed data sets (<app>/seed_sets.py) and import diff specs (<app>/diffspecs.py).
        from . import twofactor  # noqa: F401
        from django.utils.module_loading import autodiscover_modules
        autodiscover_modules("seed_sets")
        autodiscover_modules("diffspecs")

        logger.debug("core.apps: ready() complete — %d audited model(s)", count)