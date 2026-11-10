"""v2.1 Phase E (L10, T-CONS-3): one announcement system for staff.

Copies the old portal announcements addressed to staff (all staff, one
department, roles) into the announcements app and archives the originals in
the portal, which keeps only public notices for the website. Public notices
and ones addressed to individual people stay where they are. Drafts, deleted
and expired rows are left alone. Safe to run on an empty database.
"""
from django.db import migrations
from django.utils import timezone


SCOPE_TO_AUDIENCE = {"staff": "all_staff", "department": "department", "role": "roles"}


def forwards(apps, schema_editor):
    Old = apps.get_model("portal", "Announcement")
    New = apps.get_model("announcements", "Announcement")
    now = timezone.now()
    rows = Old.objects.filter(is_draft=False, archived_at__isnull=True,
                              visibility__in=list(SCOPE_TO_AUDIENCE))
    if hasattr(Old, "is_deleted"):
        rows = rows.filter(is_deleted=False)
    for old in rows.order_by("publish_at", "pk"):
        if old.expires_at is not None and old.expires_at <= now:
            continue
        audience = SCOPE_TO_AUDIENCE[old.visibility]
        if audience == "department" and not old.department_id:
            audience = "all_staff"
        body = "\n\n".join(x for x in ((old.summary or "").strip(), (old.body or "").strip()) if x) or old.title
        new = New.objects.create(
            title=old.title[:200], body=body,
            kind="urgent" if old.priority == "urgent" else "notice",
            audience=audience, department_id=old.department_id if audience == "department" else None,
            created_by_id=old.author_id, published_at=old.publish_at, pinned=old.is_pinned,
            expires_at=old.expires_at,
        )
        if audience == "roles":
            role_ids = list(old.roles.values_list("pk", flat=True))
            if role_ids:
                new.roles.set(role_ids)
            else:
                new.audience = "all_staff"
                new.save(update_fields=["audience"])
        old.archived_at = now
        old.save(update_fields=["archived_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("announcements", "0001_initial"),
        ("portal", "0006_roles_target_accounts_role"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
