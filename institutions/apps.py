from django.apps import AppConfig


class InstitutionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "institutions"
    verbose_name = "Institutions (QA)"

    def ready(self):
        from . import handlers  # noqa: F401  (registers the §6 action handlers)
