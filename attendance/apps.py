from django.apps import AppConfig


class AttendanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "attendance"
    verbose_name = "Attendance"

    def ready(self):
        from . import signals
        signals.connect()
        # v2.2: the cached holiday lookup follows every holiday change.
        from django.db.models.signals import m2m_changed, post_delete, post_save

        from portal.models import Holiday

        from .services import bump_holidays

        post_save.connect(bump_holidays, sender=Holiday, dispatch_uid="attendance_holidays_save")
        post_delete.connect(bump_holidays, sender=Holiday, dispatch_uid="attendance_holidays_delete")
        m2m_changed.connect(bump_holidays, sender=Holiday.roles.through, dispatch_uid="attendance_holidays_roles")
