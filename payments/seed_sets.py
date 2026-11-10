"""seed_nbte data sets for payments (v2.1 Phase F, F.4 / P1).

* ``finance.fee_components``  bill lines per fee schedule: base fee + processing
                              fee (core/seed_data/finance/fee_components.json).
                              Amounts are qa.fee.* settings ([FEE AMOUNT] until
                              NBTE confirms; the polytechnic processing fee is
                              the owner's example, ₦1,000,000, to confirm).

Idempotent: a second run reports no changes; edited rows are kept.
"""
from core.seeding import load_json, seed_set, upsert


@seed_set("finance.fee_components", group="finance", depends=("settings", "qa_core.startup"),
          files=("finance/fee_components.json",), order=580, phase="F")
def seed_fee_components(ctx):
    from institutions.models import InstitutionType

    from .models import FeeComponent, FeeSchedule

    data = load_json(ctx, "finance/fee_components.json")
    for row in data["rows"]:
        schedule = FeeSchedule.objects.filter(code=row["schedule"]).first()
        if schedule is None:
            ctx.warn(f"No fee schedule '{row['schedule']}': component {row['code']} skipped")
            continue
        itype = None
        if row.get("institution_type"):
            itype = InstitutionType.objects.filter(code=row["institution_type"]).first()
            if itype is None:
                ctx.warn(f"No institution type '{row['institution_type']}': component {row['code']} skipped")
                continue
        upsert(FeeComponent, {"schedule": schedule, "code": row["code"]},
               {"label": row["label"], "kind": row["kind"], "description": row.get("description", ""),
                "amount_setting": row.get("amount_setting", ""), "mode": row.get("mode", "fixed"),
                "institution_type": itype, "order": row.get("order", 10), "to_confirm": row.get("to_confirm", False)},
               ctx=ctx)


@seed_set("finance.type_fees", group="finance", depends=("settings", "qa_core.startup"), order=585, phase="F")
def seed_type_fees_set(ctx):
    """v2.2: one row of fees per institution category (Settings → Fees by
    institution category). Rows that exist are never changed."""
    from config.models import ConfigEntry
    from institutions.models import InstitutionType

    from .fee_table_seed import seed_type_fees
    from .models import InstitutionTypeFee

    made = seed_type_fees(InstitutionType, InstitutionTypeFee, ConfigEntry, activate=True)
    if made:
        ctx.note(f"{made} institution categories given a fee row") if hasattr(ctx, "note") else None
