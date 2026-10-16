"""Review M8: redact sensitive values already written to the audit log.

New entries are redacted at write time through each model's
AUDIT_EXCLUDE_FIELDS. This migration applies the same redaction to rows
written before that change. It cannot be reversed (the values are gone);
the reverse operation is a no-op.
"""
from django.db import migrations

REDACTED = "***"

SENSITIVE = {
    "messaging.Memo": ("subject", "body"),
    "tickets.TicketComment": ("body",),
    "attendance.LocationCheckInCode": ("code",),
    "staff.Staff": (
        "phone", "emergency_contact_name", "emergency_contact_phone",
        "birth_month", "birth_day",
    ),
}


def redact(apps, schema_editor):
    AuditLogEntry = apps.get_model("core", "AuditLogEntry")
    for label, fields in SENSITIVE.items():
        rows = AuditLogEntry.objects.filter(model_label=label).only("pk", "changes")
        for row in rows.iterator():
            changes = row.changes or {}
            touched = False
            for field in fields:
                entry = changes.get(field)
                if not isinstance(entry, dict):
                    continue
                for side in ("old", "new"):
                    if side in entry and entry[side] not in (None, "", REDACTED):
                        entry[side] = REDACTED
                        touched = True
            if touched:
                # .update() bypasses AuditLogEntry.save(), which refuses edits.
                AuditLogEntry.objects.filter(pk=row.pk).update(changes=changes)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_alter_contactmessage_options_and_more"),
    ]

    operations = [
        migrations.RunPython(redact, migrations.RunPython.noop),
    ]
