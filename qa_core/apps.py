from django.apps import AppConfig


class QaCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "qa_core"
    verbose_name = "QA core engines"

    def ready(self):
        # Formula-typed settings are validated by the QA evaluator on save
        # (Master Plan §14). `config` exposes a hook so it never imports qa_core.
        from config.models import register_value_validator

        from .formula import validate_formula_setting

        register_value_validator("formula", validate_formula_setting)

        from . import signals  # noqa: F401  (side-effect import)
