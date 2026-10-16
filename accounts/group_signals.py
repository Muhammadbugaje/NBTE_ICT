"""Stage 3 — audit every change to groups and group/permission assignments,
whichever path made it (the new pages, Django admin, the shell).

Uses the same API Stage 1 used for role-permission changes:

    core.audit.log(action, instance, actor=None, changes=None, note="")

The new views write one richer entry per save (actor, target, before/after
permission sets) and switch these signal entries off for the duration with
`views_are_logging()`, so nothing is recorded twice.

Redaction (review M8): these entries only ever contain group names and
permission labels such as "staff.view_staff" — never field values — so no
`AUDIT_EXCLUDE_FIELDS` rule applies.
"""
import contextlib
import contextvars

from django.contrib.auth.models import Group, Permission
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from core import audit

from .models import Role, User

_views_logging = contextvars.ContextVar("groups_views_logging", default=False)


@contextlib.contextmanager
def views_are_logging():
    token = _views_logging.set(True)
    try:
        yield
    finally:
        _views_logging.reset(token)


def _labels(pks):
    return sorted(
        f"{app}.{code}"
        for app, code in Permission.objects.filter(pk__in=pks or ())
        .values_list("content_type__app_label", "codename")
    )


def _names(pks):
    return sorted(Group.objects.filter(pk__in=pks or ()).values_list("name", flat=True))


_KEYS = {"post_add": "added", "post_remove": "removed", "post_clear": "cleared"}


def _m2m(kind, describe):
    def handler(sender, instance, action, pk_set, reverse, **kwargs):
        if _views_logging.get() or action not in _KEYS:
            return
        if reverse:
            # e.g. group.user_set.add(user) — log against the group.
            return
        audit.log("update", instance, changes={f"{kind}_{_KEYS[action]}": describe(pk_set)})
    return handler


_role_groups = _m2m("groups", _names)
_user_groups = _m2m("groups", _names)
_user_perms = _m2m("permissions", _labels)
_group_perms = _m2m("permissions", _labels)

m2m_changed.connect(_role_groups, sender=Role.groups.through, dispatch_uid="stage3-role-groups", weak=False)
m2m_changed.connect(_user_groups, sender=User.groups.through, dispatch_uid="stage3-user-groups", weak=False)
m2m_changed.connect(_user_perms, sender=User.user_permissions.through, dispatch_uid="stage3-user-perms", weak=False)
m2m_changed.connect(_group_perms, sender=Group.permissions.through, dispatch_uid="stage3-group-perms", weak=False)


@receiver(post_save, sender=Group, dispatch_uid="stage3-group-save")
def _group_saved(sender, instance, created, raw=False, **kwargs):
    if raw or _views_logging.get():
        return
    audit.log("create" if created else "update", instance, changes={"name": instance.name})


@receiver(post_delete, sender=Group, dispatch_uid="stage3-group-delete")
def _group_deleted(sender, instance, **kwargs):
    if _views_logging.get():
        return
    audit.log("delete", instance, note="group deleted")
