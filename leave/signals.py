from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from attendance.models import AttendanceRecord

from .models import LeaveApplication
from .services import sync_attendance
from workflow.models import Request


@receiver(post_save, sender=LeaveApplication)
def sync_attendance_for_application(sender, instance, **kwargs):
    sync_attendance(instance)


@receiver(post_save, sender=Request)
def sync_attendance_for_request(sender, instance, **kwargs):
    application = LeaveApplication.objects.filter(request_id=instance.pk).first()
    if application is not None:
        sync_attendance(application)


@receiver(pre_delete, sender=LeaveApplication)
def remove_attendance_for_deleted_application(sender, instance, **kwargs):
    from .services import _restore_absence

    dates = list(
        AttendanceRecord.objects.filter(
            leave_application=instance,
            status=AttendanceRecord.ON_LEAVE,
        ).values_list("date", flat=True)
    )
    AttendanceRecord.objects.filter(
        leave_application=instance,
        status=AttendanceRecord.ON_LEAVE,
    ).delete()
    for day in dates:
        _restore_absence(instance.staff.user, day)
