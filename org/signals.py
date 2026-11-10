"""Keep the organisation model consistent when accounts change.

* Seat an empty director seat (B1): when a staff account gets the ``director``
  role (or moves department while holding it) and its department has no
  director, the person takes the seat. An occupied seat is never touched —
  a second director-role holder stays without the seat (the one-director
  rule). Switch off with the setting ``org.auto_seat_director = false``.
* Office/department moves invalidate the per-request permission cache.
"""
from django.conf import settings
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver


def _auto_seat_enabled():
    import config
    value = config.get("org.auto_seat_director", True)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


@receiver(pre_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="org_user_pre_save")
def _remember_role(sender, instance, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    if update_fields is not None and not ({"role", "role_id", "department", "department_id",
                                            "is_active"} & set(update_fields)):
        instance._org_check = False
        return
    instance._org_check = True
    if instance.pk:
        old = sender._base_manager.filter(pk=instance.pk).values("role_id", "department_id").first()
        instance._org_old = (old["role_id"], old["department_id"]) if old else (None, None)
    else:
        instance._org_old = (None, None)


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="org_user_post_save")
def _seat_director(sender, instance, created, raw=False, **kwargs):
    if raw or not getattr(instance, "_org_check", False) or getattr(instance, "_org_skip_seat", False):
        return
    if instance.user_type != "staff" or not instance.is_active or not instance.department_id:
        return
    if (instance.role_id, instance.department_id) == getattr(instance, "_org_old", (None, None)):
        return
    if not instance.role_id or instance.role.code != "director":
        return
    if not _auto_seat_enabled():
        return
    from staff.models import Department
    if Department.objects.filter(director_id=instance.pk).exists():
        return
    updated = Department.objects.filter(pk=instance.department_id, director__isnull=True) \
        .update(director=instance)
    if updated:
        from qa_core.departments import clear_cache

        clear_cache()   # v2.2: director-seat memo (bulk update sends no signal)
        from core import audit
        dept = Department.objects.get(pk=instance.department_id)
        audit.log("director_assigned", dept, changes={"director": {"old": None, "new": instance.email}},
                  note="Seat was empty; filled when the director role was given")
