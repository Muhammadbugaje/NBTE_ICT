"""Cache invalidation for assignments.access (P.2): anything that can move
who sees which institution bumps the global access version."""
from django.db.models.signals import m2m_changed, post_delete, post_save

WATCHED = (
    "assignments.WorkAssignment", "assignments.AccessGrant",
    "accreditation.DepartmentalClearance", "accreditation.OnboardingProgrammeReview", "accreditation.QACase",
    "complaints.Complaint", "institutions.Institution", "institutions.DocumentReview", "staff.Department", "payments.PaymentObligation",
    "config.ConfigEntry", "accounts.User",
)


def _bump(sender, instance=None, **kwargs):
    if sender._meta.label == "config.ConfigEntry":
        key = str(getattr(instance, "key", ""))
        if not key.startswith(("qa.privacy.", "qa.access.")):
            return
        if key == "qa.privacy.mode" and str(instance.value).strip().lower() == "assignment" \
                and kwargs.get("signal") is post_save:
            # P.4: the switch narrows qa_core.view_all_institutions (dry run: privacy_mode --show).
            from .access import bump_global_version
            bump_global_version()
            from .view_all import apply
            apply()
    from .access import bump_global_version
    bump_global_version()


def connect():
    from django.apps import apps
    for label in WATCHED:
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        post_save.connect(_bump, sender=model, dispatch_uid=f"asg_access_bump_{label}")
        post_delete.connect(_bump, sender=model, dispatch_uid=f"asg_access_bump_del_{label}")
    # Permission changes (delegation of view_department_qa, view_all_journeys…).
    from django.contrib.auth.models import Group
    User = apps.get_model("accounts.User")
    Role = apps.get_model("accounts.Role")
    for through, uid in ((User.user_permissions.through, "user_perms"), (User.groups.through, "user_groups"),
                         (Group.permissions.through, "group_perms"), (Role.permissions.through, "role_perms")):
        m2m_changed.connect(_bump_m2m, sender=through, dispatch_uid=f"asg_access_bump_{uid}")


def _bump_m2m(sender, action=None, **kwargs):
    if action in ("post_add", "post_remove", "post_clear"):
        from .access import bump_global_version
        bump_global_version()
