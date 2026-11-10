"""Public-facing queries — leadership, and other read-only data for the
landing page.
"""
from collections import defaultdict

from django.db.models import Q

from core.cache import get_or_set_context, TTL_VERY_LONG


def _all_current_public_tenures():
    from staff.models import ExecutiveTenure

    return (
        ExecutiveTenure.objects
        .filter(ended_on__isnull=True, is_public=True)
        .select_related(
            "staff", "staff__department", "staff__user",
            "position", "position__department",
        )
        .order_by(
            "position__order",
            "display_order",
            "-started_on",
        )
    )


def featured_leadership(limit: int = 6, department_code: str | None = None):
    key = ["top", str(limit)]
    if department_code:
        key.append(department_code)

    def _build():
        qs = _all_current_public_tenures()
        if department_code:
            qs = qs.filter(
                Q(position__department__code=department_code)
                | Q(position__scope="federal")
                | Q(position__scope="board")
            )
        return list(qs[:limit])

    return get_or_set_context(
        "public_leadership", key, _build, ttl=TTL_VERY_LONG,
    )


def all_leadership():
    return featured_leadership(limit=500)


def _bucket(position):
    scope = (getattr(position, "scope", "") or "").lower()
    if scope == "federal":
        return "executive"
    if scope == "board":
        return "board"
    return "departments"


def leadership_page_context():
    def _build():
        rows = list(_all_current_public_tenures())

        executive: list = []
        board: list = []
        by_dept: dict = {}
        dept_lookup: dict = {}

        for t in rows:
            section = _bucket(t.position)
            if section == "executive":
                executive.append(t)
            elif section == "board":
                board.append(t)
            else:
                dept = t.position.department or (t.staff.department if t.staff_id else None)
                if dept is None:
                    executive.append(t)
                    continue
                by_dept.setdefault(dept.pk, []).append(t)
                dept_lookup[dept.pk] = dept

        departments = [
            {"department": dept_lookup[pk], "tenures": tenures}
            for pk, tenures in sorted(
                by_dept.items(),
                key=lambda kv: dept_lookup[kv[0]].name.lower(),
            )
        ]
        departments_with_leadership = [
            {"department": row["department"], "count": len(row["tenures"])}
            for row in departments
        ]

        return {
            "executive":   executive,
            "board":       board,
            "departments": departments,
            "leadership": rows,
            "departments_with_leadership": departments_with_leadership,
            "total":       len(rows),
            "department_filter_choices": [
                {"code": d["department"].code, "name": d["department"].name}
                for d in departments
            ],
        }

    return get_or_set_context(
        "public_leadership", ["page"], _build, ttl=TTL_VERY_LONG,
    )


def departments_with_leadership():
    ctx = leadership_page_context()
    return [
        {"department": d["department"], "count": len(d["tenures"])}
        for d in ctx["departments"]
    ]

# ---------------------------------------------------------------------------
# v2.1 A3/A4/T-CONS-4: one leadership page — current leaders first, then every
# previous holder, newest first (people without a staff record included).
# ---------------------------------------------------------------------------
ES_CODES = ("executive-secretary", "es")


def _is_es(position):
    code = (getattr(position, "code", "") or "").lower()
    name = (getattr(position, "name", "") or "").lower()
    return code in ES_CODES or code.startswith("executive-secretary") or name.startswith("executive secretary")


def previous_holders():
    """{"es": [tenures], "others": [{"position": p, "tenures": [...]}, ...]} — ended,
    public tenures, newest first. Executive Secretaries come first."""
    from staff.models import ExecutiveTenure

    def _build():
        rows = list(
            ExecutiveTenure.objects
            .filter(ended_on__isnull=False, is_public=True)
            .select_related("staff", "position", "position__department")
            .order_by("position__order", "position__name", "-ended_on", "-started_on")
        )
        es, others, index = [], [], {}
        for t in rows:
            if _is_es(t.position):
                es.append(t)
                continue
            if t.position_id not in index:
                index[t.position_id] = {"position": t.position, "tenures": []}
                others.append(index[t.position_id])
            index[t.position_id]["tenures"].append(t)
        es.sort(key=lambda t: (t.ended_on, t.started_on), reverse=True)
        return {"es": es, "others": others}

    return get_or_set_context("public_leadership", ["previous"], _build, ttl=TTL_VERY_LONG)


def current_first(rows):
    """Current tenures with the Executive Secretary first (A3)."""
    rows = list(rows)
    return [t for t in rows if _is_es(t.position)] + [t for t in rows if not _is_es(t.position)]
