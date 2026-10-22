from django.apps import AppConfig


class AnnouncementsConfig(AppConfig):
    """Phase 9: the official one-way "NBTE Announcements" channel."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "announcements"
    verbose_name = "Announcements"

    def ready(self):
        # Delete the stored file when an attachment row is hard-deleted
        # (registered here so core/files_register.py stays untouched).
        try:
            from core.files import register_file_cleanup

            from .models import AnnouncementAttachment

            register_file_cleanup(AnnouncementAttachment, ["file"])
        except Exception:  # cleanup wiring must never stop startup
            import logging

            logging.getLogger(__name__).exception("announcements: file cleanup registration failed")
