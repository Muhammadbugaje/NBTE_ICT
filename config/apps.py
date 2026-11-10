from django.apps import AppConfig


class ConfigConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "config"

    def ready(self):
        from . import signals  # noqa: F401
        from .appearance import KEY_VALIDATORS, validate_entry
        from .models import register_key_validator

        for key in KEY_VALIDATORS:  # v2.1 A9: colour + choice validators
            register_key_validator(key, validate_entry)
