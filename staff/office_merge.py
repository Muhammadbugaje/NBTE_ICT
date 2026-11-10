"""v2.2 — Departments and Offices are one list.

Every ``staff.Office`` becomes (or is merged into) a ``staff.Department`` with
a ``kind``. The function works on real or historical models, so the data
migration and the seed set share one rule set. Running it twice changes
nothing.
"""
from django.utils.text import slugify

# Office name (upper-case) -> code of an existing department it duplicates.
SAME_AS = {
    "EXECUTIVE SECRETARY": "es-office",
    "ES OFFICE": "es-office",
    "LEGAL UNIT": "legal",
    "NBTE LIAISON OFFICE ABUJA": "liaison-office",
}
# Kinds for the departments that already existed under office-like names.
EXISTING_KINDS = {
    "es-office": "office",
    "zonal-office": "zonal_office",
    "liaison-office": "liaison_office",
    "legal": "unit",
}


def _kind_and_parent_code(name):
    up = name.upper()
    if up.startswith("ZONAL OFFICE"):
        return "zonal_office", "zonal-office"
    if "LIAISON" in up:
        return "liaison_office", None
    if up.startswith("TA-ES"):
        return "office", "es-office"
    if up.endswith(" UNIT"):
        return "unit", "es-office"
    return "unit", None


def _unique_code(Department, name):
    base = slugify(name)[:45] or "unit"
    code, n = base, 2
    while Department.objects.filter(code=code).exists():
        code, n = f"{base}-{n}", n + 1
    return code


def merge_offices(Department, Office, Staff, log=None):
    """Merge every Office into the Department list. Returns {office_pk: dept_pk}."""
    say = log or (lambda *_: None)
    for code, kind in EXISTING_KINDS.items():
        Department.objects.filter(code=code, kind="department").update(kind=kind)
    by_code = {d.code: d for d in Department.objects.all()}
    mapping = {}
    for office in Office.objects.all().order_by("sort_order", "pk"):
        if getattr(office, "merged_into_id", None):
            mapping[office.pk] = office.merged_into_id
            continue
        kind, parent_code = _kind_and_parent_code(office.name)
        target = None
        same = SAME_AS.get(office.name.upper())
        if same:
            target = by_code.get(same)
        if target is None:
            target = Department.objects.filter(name__iexact=office.name).first()
        if target is None:
            # A known office keeps its well-known code (e.g. Legal Unit -> "legal")
            # on a fresh database too, so code lookups find it.
            code = same if same and not Department.objects.filter(code=same).exists() \
                else _unique_code(Department, office.name)
            target = Department.objects.create(
                name=office.name, code=code, kind=kind,
                parent=by_code.get(parent_code) if parent_code else None,
                description=(office.description or "")[:255], is_active=office.is_active,
                sort_order=500 + (office.sort_order or 0))
            by_code[target.code] = target
            say(f"office '{office.name}' -> new {kind} '{target.code}'")
        else:
            changed = []
            if target.kind == "department" and kind != "department":
                target.kind = kind
                changed.append("kind")
            if parent_code and not target.parent_id and by_code.get(parent_code) and by_code[parent_code].pk != target.pk:
                target.parent = by_code[parent_code]
                changed.append("parent")
            if changed:
                target.save(update_fields=changed)
            say(f"office '{office.name}' -> existing '{target.code}'")
        if office.head_id and not target.head_id:
            head_user = Staff.objects.filter(pk=office.head_id).values_list("user_id", flat=True).first()
            if head_user:
                target.head_id = head_user
                target.save(update_fields=["head"])
        groups = list(office.groups.all())
        if groups:
            target.member_groups.add(*groups)
        # Members posted to the office now belong to the unit.
        Staff.objects.filter(office=office, department__isnull=True).update(department=target)
        office.merged_into = target
        office.is_active = False
        office.save(update_fields=["merged_into", "is_active"])
        mapping[office.pk] = target.pk
    return mapping
