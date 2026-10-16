from django.apps import AppConfig


class ShowcaseConfig(AppConfig):
    """Admin-uploadable images for the sign-in background, the landing hero
    and the dashboard banner (UI overhaul, Phase 4)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "showcase"
    verbose_name = "Showcase images"

    def ready(self):
        # Delete the file from disk when an image row is deleted or replaced
        # (same lifecycle as every other uploaded file in the project).
        from core.files import register_file_cleanup

        from .models import ShowcaseImage

        register_file_cleanup(ShowcaseImage, ["image"])
