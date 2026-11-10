"""v2.1 Phase D signal wiring: holidays reconcile the register (F8) and QA
visit teams write "On official duty" days (G7)."""
import logging

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _holiday_model():
    from portal.models import Holiday
    return Holiday


def connect():
    from inspection.models import Inspection, InspectionTeamMember

    Holiday = _holiday_model()
    pre_save.connect(_holiday_pre_save, sender=Holiday, dispatch_uid="att_holiday_pre")
    post_save.connect(_holiday_saved, sender=Holiday, dispatch_uid="att_holiday_saved")
    post_delete.connect(_holiday_deleted, sender=Holiday, dispatch_uid="att_holiday_deleted")
    post_save.connect(_inspection_saved, sender=Inspection, dispatch_uid="att_inspection_saved")
    post_save.connect(_member_changed, sender=InspectionTeamMember, dispatch_uid="att_member_saved")
    post_delete.connect(_member_changed, sender=InspectionTeamMember, dispatch_uid="att_member_deleted")


def _holiday_pre_save(sender, instance, **kwargs):
    from .holidays import occurrence_days

    old = sender.objects.filter(pk=instance.pk).first() if instance.pk else None
    instance._att_previous_days = occurrence_days(old) if old is not None else []


def _holiday_saved(sender, instance, **kwargs):
    from .holidays import reconcile_holiday

    try:
        reconcile_holiday(instance, getattr(instance, "_att_previous_days", ()))
    except Exception:  # never block saving a holiday
        logger.exception("holiday reconcile failed for %s", instance.pk)


def _holiday_deleted(sender, instance, **kwargs):
    from .holidays import occurrence_days, reconcile_days

    try:
        reconcile_days(occurrence_days(instance))
    except Exception:
        logger.exception("holiday reconcile failed after delete")


def _inspection_saved(sender, instance, **kwargs):
    from .duty import sync_for_inspection_id

    try:
        sync_for_inspection_id(instance.pk)
    except Exception:
        logger.exception("visit duty sync failed for inspection %s", instance.pk)


def _member_changed(sender, instance, **kwargs):
    from .duty import sync_for_inspection_id

    try:
        sync_for_inspection_id(instance.inspection_id)
    except Exception:
        logger.exception("visit duty sync failed for inspection %s", instance.inspection_id)
