"""B2: there is no "Director of Finance" role any more. The Director of
Finance is the director of the department named in
qa.payment.finance_department (default "finance").

* holders of qa-finance-director get the plain `director` role (department
  kept; Finance if they had none);
* the role's permissions move into the "Finance – Approvals" group, which is
  attached to the Finance department's director seat (Department.director_groups);
* every reference to the role is repointed to `director`; the role is deleted;
* payment flow transitions check the seat (`qa.payment.finance_department`)
  instead of the role setting, which is removed.
"""
from django.db import migrations

OLD = "qa-finance-director"


def forward(apps, schema_editor):
    from org import seed
    Role = apps.get_model("accounts", "Role")
    User = apps.get_model("accounts", "User")
    Department = apps.get_model("staff", "Department")
    ConfigEntry = apps.get_model("config", "ConfigEntry")
    Group = apps.get_model("auth", "Group")
    FlowTransition = apps.get_model("qa_core", "FlowTransition")

    row = ConfigEntry.objects.filter(key="qa.payment.finance_department").first()
    finance_code = (row.value.strip() if row is not None and row.value else "") or "finance"
    seed.seed_finance_seat_group(get_model=apps.get_model, finance_code=finance_code)

    old = Role.objects.filter(code=OLD).first()
    director = Role.objects.filter(code="director").first()
    if old is not None:
        if director is None:
            director = Role.objects.create(code="director", name="Director of Department", rank=100,
                                           max_open_tasks=50, is_system=True, requires_2fa=True)
        group = Group.objects.filter(name=seed.FINANCE_SEAT_GROUP[0]).first()
        if group is not None:
            group.permissions.add(*old.permissions.all())
            for g in old.groups.all():
                group.permissions.add(*g.permissions.all())
        finance = Department.objects.filter(code=finance_code).first()
        for u in User.objects.filter(role=old):
            u.role = director
            if u.department_id is None and finance is not None:
                u.department = finance
            u.save(update_fields=["role", "department"])
        # Repoint every other reference to the role.
        for rel in Role._meta.related_objects:
            model = rel.related_model
            if model is User:
                continue
            if rel.many_to_many:
                if rel.through is not None and not rel.through._meta.auto_created:
                    continue
                accessor = rel.get_accessor_name()
                for obj in getattr(old, accessor).all():
                    getattr(obj, rel.field.name).add(director)
            elif rel.one_to_many or rel.one_to_one:
                model._base_manager.filter(**{rel.field.name: old}).update(**{rel.field.name: director})
        old.delete()

    FlowTransition.objects.filter(required_role_setting="qa.payment.final_approver_role") \
        .update(required_role_setting="qa.payment.finance_department")
    if not ConfigEntry.objects.filter(key="qa.payment.finance_department").exists():
        old_row = ConfigEntry.objects.filter(key="qa.payment.final_approver_role").first()
        if old_row is not None:
            ConfigEntry.objects.create(
                key="qa.payment.finance_department", value=finance_code, value_type="str",
                group=old_row.group, scope=old_row.scope,
                description="Code of the department whose director gives the final confirm/reject on every "
                            "payment (the Director of Finance).",
                editable_by=old_row.editable_by, dual_control=old_row.dual_control)
    ConfigEntry.objects.filter(key="qa.payment.final_approver_role").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("org", "0003_staff_grade_fk"),
        ("qa_core", "0003_task_bridge"),
        ("institutions", "0006_merge_20261021_0805"),
        ("messaging", "0010_phase9_chat_upgrade"),
        ("complaints", "0002_institution_media_folders"),
        ("workflow", "0007_record_pdf_restricted_storage"),
        ("announcements", "0001_initial"),
        ("portal", "0006_roles_target_accounts_role"),
        ("payments", "0002_institution_media_folders"),
    ]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
