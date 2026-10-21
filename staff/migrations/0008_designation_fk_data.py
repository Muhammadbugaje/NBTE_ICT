"""Nominal roll, step 2 of 4 — move Staff.designation text into Designation rows.

Forward (idempotent):
  * copy each staff member's text designation into `designation_legacy`;
  * get_or_create one Designation per distinct non-empty value (rank 0 — the
    `seed_nbte` command and the admin set real ranks later);
  * point the temporary FK `designation_fk` at it.
Reverse:
  * copy `designation_legacy` back into the text column `designation`
    (restores every original value exactly) and clear the FK.
Designation rows created here are left in place on reverse; they are harmless
and `0007`'s reverse drops the table anyway.
"""
from django.db import migrations
from django.utils.text import slugify


def _unique_code(Designation, name):
    base = slugify(name)[:72] or "designation"
    code, n = base, 2
    while Designation.objects.filter(code=code).exists():
        code, n = f"{base}-{n}", n + 1
    return code


def forwards(apps, schema_editor):
    Staff = apps.get_model("staff", "Staff")
    Designation = apps.get_model("staff", "Designation")
    cache = {}
    for s in Staff._base_manager.all().only("pk", "designation", "designation_legacy", "designation_fk"):
        text = (s.designation or "").strip()
        updates = {}
        if s.designation_legacy != s.designation:
            updates["designation_legacy"] = s.designation or ""
        if text:
            d = cache.get(text)
            if d is None:
                d = Designation.objects.filter(name=text).first()
                if d is None:
                    d = Designation.objects.create(name=text, code=_unique_code(Designation, text), rank=0)
                cache[text] = d
            if s.designation_fk_id != d.pk:
                updates["designation_fk_id"] = d.pk
        if updates:
            Staff._base_manager.filter(pk=s.pk).update(**updates)


def backwards(apps, schema_editor):
    Staff = apps.get_model("staff", "Staff")
    for s in Staff._base_manager.all().only("pk", "designation_legacy"):
        Staff._base_manager.filter(pk=s.pk).update(
            designation=s.designation_legacy or "", designation_fk=None)


class Migration(migrations.Migration):

    dependencies = [("staff", "0007_nominal_roll_models")]

    operations = [migrations.RunPython(forwards, backwards)]
