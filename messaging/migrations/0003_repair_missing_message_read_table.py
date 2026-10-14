from django.db import migrations


def ensure_message_read_table(apps, schema_editor):
    MessageRead = apps.get_model("messaging", "MessageRead")
    table_name = MessageRead._meta.db_table
    existing_tables = schema_editor.connection.introspection.table_names()
    if table_name not in existing_tables:
        schema_editor.create_model(MessageRead)


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0002_thread_message_features"),
    ]

    operations = [
        migrations.RunPython(ensure_message_read_table, migrations.RunPython.noop),
    ]
