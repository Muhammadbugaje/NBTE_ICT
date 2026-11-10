"""Phase B data: new permissions, CONTEDISS grade-level placeholders, proposed
role minimum grades, ES/super-admin explicit rights, the Division Head group
and the X31 allow-list (all idempotent; see org/seed.py)."""
from django.db import migrations


def forward(apps, schema_editor):
    from org import seed
    for _name, func in seed.DATA_SETS:
        if func is seed.seed_finance_seat_group:
            continue  # done with the qa-finance-director removal (0004)
        func(get_model=apps.get_model)


class Migration(migrations.Migration):
    dependencies = [
        ("org", "0001_initial"),
        ("staff", "0015_phase_b_director_division_grade"),
        ("accounts", "0018_phase_b_role_min_grade_grants"),
        ("tasks", "0003_view_all_tasks"),
        ("inspection", "0003_view_all_ssq"),
        ("config", "0003_qa_value_types_dual_control"),
        ("contenttypes", "0002_remove_content_type_name"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
