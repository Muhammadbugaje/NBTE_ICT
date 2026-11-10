"""seed_nbte data sets owned by public (v2.1 Phase A)."""
from core.seeding import load_json, seed_set, upsert


@seed_set("public.history", group="base", files=("public/history.json",), order=16, phase="A")
def seed_history(ctx):
    """History timeline (A1). Placeholders until NBTE confirms; the section stays
    hidden (home.history.enabled = false) until then. Natural key: year + title."""
    from .models import HistoryMilestone

    data = load_json(ctx, "public/history.json")
    for row in data["rows"]:
        upsert(HistoryMilestone, {"year": row["year"], "title": row["title"]},
               {"body": row["body"], "led_by": row.get("led_by", ""), "order": row["order"],
                "is_current": bool(row.get("current")), "is_placeholder": bool(row["to_confirm"]),
                "is_published": True}, ctx=ctx)
    if not data.get("confirmed"):
        ctx.warn("History milestones are placeholders until NBTE confirms them (home.history.enabled is off).")
