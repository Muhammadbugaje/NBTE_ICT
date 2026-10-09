from django.apps import AppConfig


class StaffConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "staff"

    def ready(self):
        # Importing the specs module registers StaffImport / StaffExport with
        # the shared importexport registry.
        from . import imports  # noqa: F401