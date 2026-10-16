import logging

from django.apps import AppConfig, apps


logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"

    def ready(self):
        logger.info("core.apps: ready() starting")

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

        logger.info("core.apps: ready() complete — %d audited model(s)", count)