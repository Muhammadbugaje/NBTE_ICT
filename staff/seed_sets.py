"""seed_nbte data sets for geography (X49) and organisation reference data (X51).

Sets 4-11 of the seed spec: geo.states, geo.lgas, geo.zones, org.departments,
org.offices, org.designations, org.grade_levels, org.staff_states.
"""
from django.apps import apps

from core.seeding import add_m2m, load_json, seed_set, upsert

from . import nbte_reference as ref


def _state_index():
    """compact name/alias -> StateOfOrigin, plus code -> StateOfOrigin."""
    from .models import StateOfOrigin

    index = {}
    for st in StateOfOrigin.objects.all():
        index[ref.compact_key(st.name)] = st
        if st.code:
            index[st.code] = st
    return index


# ---------------------------------------------------------------------------
# 4 geo.states
# ---------------------------------------------------------------------------
@seed_set("geo.states", group="geo", files=("geo/states.json",), order=40)
def seed_states(ctx):
    from .models import StateOfOrigin

    data = load_json(ctx, "geo/states.json")
    for i, row in enumerate(data["rows"], start=1):
        existing = (StateOfOrigin.objects.filter(code=row["code"]).first()
                    or StateOfOrigin.objects.filter(name__iexact=row["name"]).first())
        if existing is None:
            for alias in row.get("aliases", []):
                existing = StateOfOrigin.objects.filter(name__iexact=alias).first()
                if existing:
                    break
        if existing is not None and existing.code and existing.code != row["code"]:
            ctx.warn(f"state {existing.name}: code '{existing.code}' kept (seed file says '{row['code']}')")
            ctx.res.unchanged += 1
            continue
        key = {"pk": existing.pk} if existing is not None else {"code": row["code"]}
        values = {"name": row["name"], "zone": row["zone"], "sort_order": i * 10}
        if "travel_mode" in row:
            values["travel_mode"] = row["travel_mode"]
        if "distance_from_hq_km" in row:
            values["distance_from_hq_km"] = row["distance_from_hq_km"]
        if existing is not None:
            values["code"] = row["code"]
        upsert(StateOfOrigin, key, values, ctx=ctx)


def state_for_lga_key(name, index):
    key = ref.compact_key(name)
    if key in index:
        return index[key]
    canonical = ref.STATE_ALIASES.get(key) or ref.STATE_ALIASES.get(name.upper())
    if canonical and ref.compact_key(canonical) in index:
        return index[ref.compact_key(canonical)]
    if key in {"FEDERALCAPITALTERRITORY", "ABUJA", "FCTABUJA"} or key.startswith("FCT"):
        return index.get("fc")
    return None


# ---------------------------------------------------------------------------
# 5 geo.lgas
# ---------------------------------------------------------------------------
@seed_set("geo.lgas", group="geo", depends=("geo.states",), files=("geo/lgas.json",), order=50)
def seed_lgas(ctx):
    from django.utils.text import slugify

    from .models import LGA

    data = load_json(ctx, "geo/lgas.json")
    index = _state_index()
    existing = {(l.state_id, l.name.casefold()): l for l in LGA.objects.all()}
    for state_name, lgas in data["states"].items():
        state = state_for_lga_key(state_name, index)
        if state is None:
            ctx.warn(f"no state for '{state_name}' ({len(lgas)} LGAs not seeded)")
            ctx.res.skipped += len(lgas)
            continue
        for order, name in enumerate(lgas, start=1):
            found = existing.get((state.pk, name.casefold()))
            if found is not None:
                upsert(LGA, {"pk": found.pk}, {"sort_order": order}, ctx=ctx)
                continue
            code = f"{state.code or slugify(state.name)[:6]}-{slugify(name)}"[:56]
            upsert(LGA, {"state": state, "name": name}, {"code": code, "sort_order": order}, ctx=ctx)
    from .geo import backfill_lga_refs, bump_lga_cache

    bump_lga_cache()
    report = backfill_lga_refs(apply=True)
    if report["matched"]:
        ctx.res.filled += report["matched"]
        ctx.note(f"lga_ref filled on {report['matched']} institution/address row(s)")
    if report["unmatched"]:
        ctx.warn(f"{len(report['unmatched'])} institution LGA spelling(s) match no LGA "
                 "(python manage.py lga_ref_report)")


# ---------------------------------------------------------------------------
# 6 geo.zones
# ---------------------------------------------------------------------------
@seed_set("geo.zones", group="geo", depends=("geo.states",), files=("geo/zonal_offices.json",), order=60)
def seed_zones(ctx):
    from .models import StateOfOrigin, ZonalOffice

    data = load_json(ctx, "geo/zonal_offices.json")
    unconfirmed = 0
    for row in data["rows"]:
        office = upsert(ZonalOffice, {"code": row["code"]},
                        {"name": row["name"], "location": row["location"], "email": row.get("email", ""),
                         "email_confirmed": bool(row.get("email_confirmed")), "sort_order": row.get("sort_order", 0)},
                        ctx=ctx)
        added = add_m2m(office, "states", list(StateOfOrigin.objects.filter(code__in=row.get("states", []))), ctx=ctx)
        if added and ctx.res.created == 0:
            ctx.res.filled += 1
        if not row.get("email_confirmed"):
            unconfirmed += 1
    if unconfirmed:
        ctx.warn(f"{unconfirmed} zonal office e-mail(s) to confirm")


# ---------------------------------------------------------------------------
# 7 org.departments
# ---------------------------------------------------------------------------
@seed_set("org.departments", group="org", files=("org/departments.json",), order=70)
def seed_departments(ctx):
    from .models import Department

    data = load_json(ctx, "org/departments.json")
    for row in data["rows"]:
        obj = (Department.objects.filter(code=row["code"]).first()
               or Department.objects.filter(name__iexact=row["name"]).first())
        if obj is None:
            for alt in [row.get("title", ""), *row.get("aliases", [])]:
                obj = Department.objects.filter(name__iexact=alt).first() if alt else None
                if obj:
                    break
        if obj is not None:
            ctx.res.unchanged += 1     # names and codes of existing departments are never changed here
            continue
        upsert(Department, {"code": row["code"]}, {"name": row["name"], "sort_order": row.get("sort_order", 500)},
               ctx=ctx)


# ---------------------------------------------------------------------------
# 8 org.offices
# ---------------------------------------------------------------------------
@seed_set("org.offices", group="org", depends=("org.departments",), files=("org/offices.json",), order=80)
def seed_offices(ctx):
    from .models import Office

    data = load_json(ctx, "org/offices.json")
    for row in data["rows"]:
        existing = Office.objects.filter(name__iexact=row["name"]).first()
        key = {"pk": existing.pk} if existing else {"name": row["name"]}
        upsert(Office, key, {"sort_order": row.get("sort_order", 0)}, ctx=ctx)
    # v2.2: offices live in the Department list (one list with a kind).
    from .models import Department, Staff
    from .office_merge import merge_offices
    merge_offices(Department, Office, Staff)


# ---------------------------------------------------------------------------
# 9 org.designations (+ promotion ladder)
# ---------------------------------------------------------------------------
@seed_set("org.designations", group="org", files=("org/designations.json",), order=90)
def seed_designations(ctx):
    import io

    from .management.commands.seed_staff_reference import Command

    cmd = Command(stdout=io.StringIO(), stderr=io.StringIO())
    cmd.quiet = True
    data = load_json(ctx, "org/designations.json")
    created, filled, existing = cmd.seed_designations(rows=data["designations"])
    _c, ladder_filled, _e = cmd.seed_ladder()
    ctx.res.created += created
    ctx.res.filled += filled + ladder_filled
    ctx.res.unchanged += existing


# ---------------------------------------------------------------------------
# 10 org.grade_levels (model from the organisation work, org.GradeLevel)
# ---------------------------------------------------------------------------
@seed_set("org.grade_levels", group="org", files=("org/grade_levels.json",), order=100)
def seed_grade_levels(ctx):
    data = load_json(ctx, "org/grade_levels.json")
    try:
        model = apps.get_model(data.get("model", "org.GradeLevel"))
    except (LookupError, ValueError):
        ctx.res.skipped += len(data["rows"])
        ctx.note(f"{data.get('model')} does not exist yet: {len(data['rows'])} placeholder row(s) waiting")
        return
    names = {f.name for f in model._meta.concrete_fields}
    for row in data["rows"]:
        key = {"code": row["code"]} if "code" in names else {"number": row["number"]}
        values = {k: v for k, v in row.items() if k in names and k not in key}
        upsert(model, key, values, ctx=ctx)
    ctx.warn("grade levels are placeholders until HR confirms them")


# ---------------------------------------------------------------------------
# 11 org.staff_states
# ---------------------------------------------------------------------------
@seed_set("org.staff_states", group="org", order=110)
def seed_staff_states(ctx):
    from .models import StaffState

    for name, order in (("Confirmed", 0), ("Probation", 10)):
        existing = StaffState.objects.filter(name__iexact=name).first()
        key = {"pk": existing.pk} if existing else {"name": name}
        upsert(StaffState, key, {"sort_order": order}, ctx=ctx)
