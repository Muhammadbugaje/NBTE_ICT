"""seed_nbte data sets owned by leave (v2.1 Phase D): 12 hr.leave_types and
13 hr.holidays. Both are fill-only: HR edits in Settings are never overwritten."""
from datetime import date

from core.seeding import load_json, seed_set, upsert

LEAVE_TYPE_FIELDS = ("name", "description", "days", "input_mode", "counting", "paid", "gender_restriction",
                     "needs_document", "notice_days", "max_per_year", "max_days_per_application", "sort_order")


@seed_set("hr.leave_types", group="hr", files=("hr/leave_types.json",), order=120, phase="D")
def seed_leave_types(ctx):
    from .models import LeaveType

    data = load_json(ctx, "hr/leave_types.json")
    for row in data["rows"]:
        values = {k: row[k] for k in LEAVE_TYPE_FIELDS if k in row and row[k] is not None}
        upsert(LeaveType, {"code": row["code"]}, values, ctx=ctx)
    if not data.get("confirmed"):
        ctx.note("leave days and rules are [TO CONFIRM] with HR (decision 26)")


HOLIDAY_FILES = ("hr/holidays_2026.json", "hr/holidays_2027.json")


@seed_set("hr.holidays", group="hr", files=HOLIDAY_FILES, order=130, phase="D")
def seed_holidays(ctx):
    from portal.models import Holiday, Scope

    to_confirm = 0
    for rel in HOLIDAY_FILES:
        data = load_json(ctx, rel)
        for row in data["rows"]:
            day = date.fromisoformat(row["date"])
            unconfirmed = bool(row.get("to_confirm"))
            to_confirm += unconfirmed
            upsert(Holiday, {"name": row["name"], "date_start": day},
                   {"date_end": day, "kind": Holiday.KIND_RELIGIOUS if unconfirmed else Holiday.KIND_PUBLIC,
                    "visibility": Scope.STAFF, "to_confirm": unconfirmed,
                    "description": "Date to confirm: the Federal Government declares it." if unconfirmed else ""},
                   ctx=ctx)
    if to_confirm:
        ctx.note(f"{to_confirm} movable holiday date(s) marked 'to confirm'")
