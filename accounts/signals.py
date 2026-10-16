"""User lifecycle signals.

Two responsibilities:

1. `create_staff_profile` — whenever a User is created, ensure a linked Staff
   record exists so profile completeness has something to compute against
   and the directory doesn't drift from the user list. Every user gets one,
   including superusers — otherwise they can't edit their own profile.

2. `audit_role_permission_changes` — grants/revokes at the role level must be
   audited. M2M changes don't fire post_save, so we log them here.
"""
import logging

from django.contrib.auth.models import Permission
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from core import audit

from .models import Role, User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Staff profile auto-creation
# ---------------------------------------------------------------------------
def _unique_employee_no(seed_pk):
    """Build a placeholder employee number based on the user's pk. If a
    collision exists (e.g. a manually created Staff with the same number),
    append a counter until unique."""
    from staff.models import Staff

    base = f"USR-{seed_pk:05d}"
    candidate = base
    n = 1
    while Staff.objects.filter(employee_no=candidate).exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


@receiver(post_save, sender=User)
def create_staff_profile(sender, instance, created, **kwargs):
    if not created:
        return

    try:
        from staff.models import Staff
    except Exception:
        logger.exception("Could not import Staff model")
        return

    # Already linked? Nothing to do.
    if Staff.objects.filter(user=instance).exists():
        return

    # Review H5: if exactly one unlinked directory record already carries
    # this email (HR created the staff record first), link it instead of
    # creating a second record - that duplicate made the follow-up link
    # fail with a UNIQUE error.
    if instance.email:
        matches = list(
            Staff.objects.filter(user__isnull=True, email__iexact=instance.email)[:2]
        )
        if len(matches) == 1:
            match = matches[0]
            match.user = instance
            match.save(update_fields=["user"])
            return

    try:
        Staff.objects.create(
            user=instance,
            employee_no=_unique_employee_no(instance.pk),
            first_name=instance.first_name or "—",
            last_name=instance.last_name or "—",
            email=instance.email or "",
            department=instance.department,
        )
    except Exception:
        logger.exception("Could not create Staff record for user %s", instance.pk)


# ---------------------------------------------------------------------------
# Role permission audit
# ---------------------------------------------------------------------------
@receiver(m2m_changed, sender=Role.permissions.through)
def audit_role_permission_changes(sender, instance, action, pk_set, reverse, **kwargs):
    """Grants/revokes at role level must be audited (doc 30 §5.1); M2M changes
    don't fire post_save, so log them here."""
    if reverse or action not in ("post_add", "post_remove", "post_clear"):
        return

    names = []
    if pk_set:
        names = sorted(
            f"{ct}.{code}"
            for ct, code in Permission.objects.filter(pk__in=pk_set)
            .values_list("content_type__app_label", "codename")
        )
    key = {
        "post_add": "permissions_added",
        "post_remove": "permissions_removed",
        "post_clear": "permissions_cleared",
    }[action]
    audit.log("update", instance, changes={key: names})