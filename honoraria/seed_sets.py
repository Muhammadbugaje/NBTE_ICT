"""seed_nbte set 25 (part): the Nigerian banks list for staff bank details (X33)."""
from core.seeding import load_json, seed_set, upsert


@seed_set("payments.banks", group="finance", files=("finance/banks.json",), order=250)
def seed_banks(ctx):
    from .models import Bank

    data = load_json(ctx, "finance/banks.json")
    for row in data["rows"]:
        upsert(Bank, {"code": row["code"]}, {"name": row["name"], "cbn_code": row["cbn_code"],
                                             "sort_order": row.get("sort_order", 0)}, ctx=ctx)
    if not data.get("confirmed"):
        ctx.note("bank list and CBN codes are unverified until Finance confirms them")


@seed_set("honoraria.references", group="finance", depends=("qa_core.startup",), order=251)
def seed_reference_formats(ctx):
    """v2.3: the honoraria schedule and honorarium-file reference formats."""
    from .references import ensure_formats

    created = ensure_formats()
    ctx.res.created += created
    if not created:
        ctx.res.unchanged += 1
