"""Register every file-owning model with `core.files`.

Called once at startup from `CoreConfig.ready()`. Every entry is a
model + list of FileField / ImageField names.

If you add a new file field to the project later, add it here so the
cleanup signals cover it. The `scan_media` command will warn about
models that have file fields the registry doesn't know about.
"""
import logging

logger = logging.getLogger(__name__)


def install_all():
    from core.files import register_file_cleanup

    # ---- staff --------------------------------------------------------
    try:
        from staff.models import Staff, StaffAttachment, ExecutiveTenure
        register_file_cleanup(Staff, ["photo"])
        register_file_cleanup(StaffAttachment, ["file"])
        register_file_cleanup(ExecutiveTenure, ["photo"])
    except Exception:
        logger.exception("file cleanup: staff registration failed")

    # ---- portal -------------------------------------------------------
    try:
        from portal.models import (
            Announcement, Event, Document, DocumentVersion,
            Award, GalleryAlbum, GalleryItem,
        )
        register_file_cleanup(Announcement, ["image"])
        register_file_cleanup(Event, ["image"])
        register_file_cleanup(Document, ["cover_image"])
        register_file_cleanup(DocumentVersion, ["file"])
        register_file_cleanup(Award, ["image", "certificate"])
        register_file_cleanup(GalleryAlbum, ["cover_image"])
        register_file_cleanup(GalleryItem, ["file"])
    except Exception:
        logger.exception("file cleanup: portal registration failed")

    # ---- assets -------------------------------------------------------
    try:
        from assets.models import AssetAttachment
        register_file_cleanup(AssetAttachment, ["file"])
    except Exception:
        logger.exception("file cleanup: assets registration failed")

    # ---- tasks --------------------------------------------------------
    try:
        from tasks.models import TaskAttachment
        register_file_cleanup(TaskAttachment, ["file"])
    except Exception:
        logger.exception("file cleanup: tasks registration failed")

    # ---- tickets ------------------------------------------------------
    try:
        from tickets.models import TicketAttachment
        register_file_cleanup(TicketAttachment, ["file"])
    except Exception:
        logger.exception("file cleanup: tickets registration failed")

    # ---- workflow -----------------------------------------------------
    try:
        from workflow.models import Request, RequestAttachment
        register_file_cleanup(Request, ["record_pdf"])
        register_file_cleanup(RequestAttachment, ["file"])
    except Exception:
        logger.exception("file cleanup: workflow registration failed")

    # ---- messaging ----------------------------------------------------
    try:
        from messaging.models import MessageAttachment
        register_file_cleanup(MessageAttachment, ["file"])
    except Exception:
        logger.exception("file cleanup: messaging registration failed")