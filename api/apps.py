from django.apps import AppConfig


class ApiConfig(AppConfig):
    """Phase 9: REST API for the Digital NBTE mobile app (see api/README.md)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "api"
    verbose_name = "Mobile API"

    def ready(self):
        from . import schema  # noqa: F401  (OpenAPI extension for the auth class)
