"""Fair workload limits per rank (Master Plan §9, Phase 8).

The user's rule: work is shared equally instead of the same people doing
everything. One setting, ``qa.workload.limits`` (JSON), holds a small table:

    {"rows": [
        {"key": "default"},                               # everyone else
        {"key": "director", "max_visits_per_year": 4},    # a designation or role code
        {"key": "resource_person", "max_open_ssq": 2}     # a kind of person
    ]}

Each row may set any of :data:`FIELDS`; a blank cell falls back to the
"default" row, then to the general setting the rule always used (so an
untouched table behaves exactly as before), then to "no limit".

A person's row is the first that matches, in this order: their designation
code, their role code, their kind (``qa_personnel`` / ``resource_person`` /
``staff``), ``default``.

:func:`load` counts what a person holds now (open SSQ reviews, SSQ reviews
and visits this year, open tasks, last served); the finder, the QA personnel
and resource-person lists show it, and the rules in ``inspection.rules`` /
``accreditation.case_services`` enforce it. There is no override (§9).
"""
# scoped-by-caller
from __future__ import annotations

import datetime
import json

from django.db.models import Sum
from django.utils import timezone

import config

SETTING = "qa.workload.limits"

# key, label, help, the general setting a blank cell falls back to, built-in fallback
FIELDS = [
    ("max_open_ssq", "SSQ reviews at once", "Open SSQ reviews a person may hold at the same time.",
     "qa.resource_person.max_open_ssq_per_officer", 3),
    ("max_ssq_per_year", "SSQ reviews a year", "SSQ reviews a person may do in one calendar year.", "", None),
    ("max_visits_per_year", "Visits a year", "QA visits a person may make in one calendar year.",
     "qa.resource_person.max_visits_per_year", 6),
    ("max_same_institution", "Same institution", "Times the same person may serve the same institution "
                                                 "within the period below.", "", 1),
    ("same_institution_months", "…within (months)", "The period for the same-institution limit.",
     "qa.matching.same_institution_cooldown_months", 24),
    ("max_open_tasks", "Open tasks", "Open tasks (any kind) a person may hold before new QA work is refused.",
     "", None),
]
FIELD_KEYS = [f[0] for f in FIELDS]
KIND_KEYS = [("qa_personnel", "QA personnel (any rank)"), ("resource_person", "Resource persons (external)"),
             ("staff", "NBTE staff (any rank)")]


# ===========================================================================
# The table
# ===========================================================================
def _clean_int(value):
    if value in (None, ""):
        return None
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def table():
    """The rows as stored (normalised): [{"key", "label", field: int|None}]."""
    raw = config.get(SETTING, None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    rows = raw.get("rows") if isinstance(raw, dict) else raw if isinstance(raw, list) else None
    out, seen = [], set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        item = {"key": key, "label": str(row.get("label") or "")[:120]}
        for f in FIELD_KEYS:
            item[f] = _clean_int(row.get(f))
        out.append(item)
    if "default" not in seen:
        out.insert(0, {"key": "default", "label": "Everyone else", **{f: None for f in FIELD_KEYS}})
    else:  # default first
        out.sort(key=lambda r: r["key"] != "default")
    return out


def serialise(rows):
    """Rows (from the editor) → the stored JSON text."""
    clean = []
    for row in rows:
        key = str(row.get("key") or "").strip().lower()
        if not key:
            continue
        item = {"key": key}
        if row.get("label"):
            item["label"] = str(row["label"])[:120]
        for f in FIELD_KEYS:
            value = _clean_int(row.get(f))
            if value is not None:
                item[f] = value
        clean.append(item)
    if not any(r["key"] == "default" for r in clean):
        clean.insert(0, {"key": "default"})
    return json.dumps({"rows": clean}, ensure_ascii=False)


def key_choices():
    """(code, label) for the editor: kinds, designations, roles."""
    from accounts.models import Role
    from staff.models import Designation

    out = [("default", "Everyone else (default)")] + list(KIND_KEYS)
    seen = {k for k, _l in out}
    for d in Designation.objects.exclude(code="").order_by("-rank", "name"):
        if d.code and d.code not in seen:
            out.append((d.code, f"{d.name} (designation)"))
            seen.add(d.code)
    for r in Role.objects.order_by("-rank", "name"):
        if r.code not in seen:
            out.append((r.code, f"{r.name} (role)"))
            seen.add(r.code)
    return out


# ===========================================================================
# Who is who
# ===========================================================================
def _user_of(person):
    from accounts.models import User

    if person is None:
        return None
    if isinstance(person, User):
        return person
    if hasattr(person, "kind") and hasattr(person, "obj"):  # rules.Candidate
        person = person.obj
    user = getattr(person, "user", None)
    if user is None and getattr(person, "staff_id", None):
        user = getattr(person.staff, "user", None)
    return user


def _staff_of(person):
    from accounts.models import User
    from staff.models import Staff

    if hasattr(person, "kind") and hasattr(person, "obj"):
        person = person.obj
    if isinstance(person, Staff):
        return person
    if isinstance(person, User):
        return getattr(person, "staff_profile", None)
    return getattr(person, "staff", None)


def kind_of(person):
    from accounts.models import User

    from .models import QAPersonnel, ResourcePerson

    if hasattr(person, "kind") and hasattr(person, "obj"):
        return person.kind
    if isinstance(person, ResourcePerson):
        return "resource_person"
    if isinstance(person, QAPersonnel):
        return "qa_personnel"
    if isinstance(person, User):
        return "staff"
    return "staff"


def rank_keys(person):
    """Codes tried against the table, most specific first."""
    obj = person.obj if (hasattr(person, "kind") and hasattr(person, "obj")) else person
    keys = []
    staff = None
    try:
        staff = _staff_of(obj)
    except Exception:
        staff = None
    designation = getattr(staff, "designation", None) if staff is not None else None
    if designation is not None and designation.code:
        keys.append(designation.code.lower())
    rank = getattr(obj, "rank", "")
    if isinstance(rank, str) and rank.strip():  # external resource person: controlled rank code
        keys.append(rank.strip().lower())
    user = _user_of(obj)
    role = getattr(user, "role", None) if user is not None else None
    if role is not None and role.code:
        keys.append(role.code.lower())
    kind = kind_of(person)
    if kind == "staff" and staff is not None:
        from .models import QAPersonnel

        if QAPersonnel.objects.filter(staff=staff).exists():
            keys.append("qa_personnel")
    keys.append(kind)
    return keys


def row_for(person, rows=None):
    rows = rows if rows is not None else table()
    by_key = {r["key"]: r for r in rows}
    for key in rank_keys(person):
        if key in by_key and key != "default":
            return by_key[key]
    return by_key.get("default")


def _general(setting, fallback):
    if not setting:
        return fallback
    try:
        return int(config.get(setting, fallback))
    except (TypeError, ValueError):
        return fallback


class Limit:
    """One effective limit: value (None = no limit) and the setting to name."""

    def __init__(self, field, value, setting, row_label=""):
        self.field, self.value, self.setting, self.row_label = field, value, setting, row_label

    def __bool__(self):
        return self.value is not None

    def __repr__(self):
        return f"Limit({self.field}={self.value} via {self.setting})"


def _row_name(row):
    if row is None:
        return ""
    if row.get("label"):
        return row["label"]
    return "everyone else" if row["key"] == "default" else row["key"]


def limit(person, field, rows=None):
    rows = rows if rows is not None else table()
    spec = next(f for f in FIELDS if f[0] == field)
    own = row_for(person, rows)
    default = next((r for r in rows if r["key"] == "default"), None)
    for row in (own, default):
        if row is not None and row.get(field) is not None:
            return Limit(field, row[field], f"{SETTING} · {_row_name(row)}", _row_name(row))
    value = _general(spec[3], spec[4])
    return Limit(field, value, spec[3] or SETTING, "")


def limits(person, rows=None):
    rows = rows if rows is not None else table()
    return {f: limit(person, f, rows) for f in FIELD_KEYS}


# ===========================================================================
# What a person holds now
# ===========================================================================
def _service(kind, pk):
    from .models import ServiceRecord

    return ServiceRecord.unscoped.filter(person_type=kind, person_id=pk)


def _service_rows(person):
    """ServiceRecord rows for a finder person or a staff user (any person type
    the user appears as)."""
    from django.db.models import Q

    from .models import QAPersonnel, ResourcePerson, ServiceRecord

    obj = person.obj if (hasattr(person, "kind") and hasattr(person, "obj")) else person
    q = Q(pk__in=[])
    if isinstance(obj, ResourcePerson):
        q |= Q(person_type="resource_person", person_id=obj.pk)
        if obj.staff_id:
            q |= Q(person_type="staff", person_id=obj.staff_id)
    elif isinstance(obj, QAPersonnel):
        q |= Q(person_type="qa_personnel", person_id=obj.pk) | Q(person_type="staff", person_id=obj.staff_id)
    else:
        staff = _staff_of(obj)
        if staff is not None:
            q |= Q(person_type="staff", person_id=staff.pk)
            qp = QAPersonnel.objects.filter(staff=staff).first()
            if qp is not None:
                q |= Q(person_type="qa_personnel", person_id=qp.pk)
        user = _user_of(obj)
        rp = ResourcePerson.objects.filter(user=user).first() if user is not None else None
        if rp is not None:
            q |= Q(person_type="resource_person", person_id=rp.pk)
    return ServiceRecord.unscoped.filter(q)


def open_ssq_reviews(person, exclude_mapping_id=None):
    from .models import QAMapping, ResourcePerson, SSQReviewAssignment

    obj = person.obj if (hasattr(person, "kind") and hasattr(person, "obj")) else person
    user = _user_of(obj)
    count = SSQReviewAssignment.unscoped.filter(reviewer=user, completed_at__isnull=True).count() if user else 0
    kind = kind_of(person)
    if kind in ("resource_person", "qa_personnel"):
        field = "resource_person" if kind == "resource_person" else "qa_personnel"
        qs = QAMapping.unscoped.filter(status__in=QAMapping.LIVE, duty="ssq_review", **{field: obj})
        if exclude_mapping_id:
            qs = qs.exclude(pk=exclude_mapping_id)
        count += qs.count()
    elif isinstance(obj, ResourcePerson):  # pragma: no cover - kind covers it
        pass
    return count


def open_tasks(person):
    from tasks.models import Task, TaskAssignee

    user = _user_of(person)
    if user is None:
        return 0
    return (TaskAssignee.objects.filter(user=user, task__state__in=Task.OPEN_STATES)
            .exclude(state__in=(TaskAssignee.DONE, TaskAssignee.DECLINED)).values("task").distinct().count())


def visits_in(person, year):
    from .models import VISIT_DUTIES

    return (_service_rows(person).filter(served_to__year=year, duty__in=VISIT_DUTIES)
            .aggregate(n=Sum("visit_count"))["n"] or 0)


def ssq_reviews_in(person, year):
    return _service_rows(person).filter(served_to__year=year).aggregate(n=Sum("ssq_count"))["n"] or 0


def times_at(person, institution_id, months):
    from .models import VISIT_DUTIES

    if not institution_id or not months or months <= 0:
        return 0
    since = timezone.localdate() - datetime.timedelta(days=int(months * 30.44))
    return (_service_rows(person).filter(institution_id=institution_id, served_to__gte=since,
                                         duty__in=VISIT_DUTIES).aggregate(n=Sum("visit_count"))["n"] or 0)


def last_served(person):
    return (_service_rows(person).filter(voided_by__isnull=True).exclude(visit_count__lte=0, ssq_count__lte=0)
            .order_by("-served_to").values_list("served_to", flat=True).first())


def load(person, *, year=None, rows=None):
    """What ``person`` holds now, with their effective limits."""
    year = year or timezone.localdate().year
    lim = limits(person, rows)
    data = {
        "open_ssq": open_ssq_reviews(person),
        "ssq_year": ssq_reviews_in(person, year),
        "visits_year": visits_in(person, year),
        "open_tasks": open_tasks(person),
        "last_served": last_served(person),
        "limits": {k: v.value for k, v in lim.items()},
        "row": _row_name(row_for(person, rows)),
    }
    data["assignments_year"] = data["visits_year"] + data["ssq_year"]
    data["full"] = [label for key, field, label in (
        ("open_ssq", "max_open_ssq", "SSQ reviews"), ("visits_year", "max_visits_per_year", "visits"),
        ("ssq_year", "max_ssq_per_year", "SSQ reviews this year"), ("open_tasks", "max_open_tasks", "tasks"))
        if lim[field].value is not None and data[key] >= lim[field].value]
    return data


# ===========================================================================
# Checks (shared by the finder, the save paths and the task bridge)
# ===========================================================================
def problems_for(person, *, duty, institution_id=None, year=None, name=None, rows=None, include_same_inst=True,
                 exclude_mapping_id=None):
    """[(rule, setting, message)] — the limits ``person`` would break by taking
    one more piece of work of ``duty`` at ``institution_id``."""
    from .models import VISIT_DUTIES

    rows = rows if rows is not None else table()
    year = year or timezone.localdate().year
    name = name or str(getattr(person, "name", None) or person)
    out = []
    if duty in VISIT_DUTIES:
        lim = limit(person, "max_visits_per_year", rows)
        if lim:
            visits = visits_in(person, year)
            if visits >= lim.value:
                out.append(("annual_cap", lim.setting if lim.row_label else "qa.resource_person.max_visits_per_year",
                            f"{name} has {visits} visit(s) in {year}; the cap is {lim.value}"))
    if include_same_inst and institution_id:
        lim_n = limit(person, "max_same_institution", rows)
        months = limit(person, "same_institution_months", rows)
        if lim_n and months:
            served = times_at(person, institution_id, months.value)
            if served >= lim_n.value:
                setting = (lim_n.setting if lim_n.row_label else months.setting if months.row_label
                           else "qa.matching.same_institution_cooldown_months")
                times = "once" if lim_n.value == 1 else f"{lim_n.value} times"
                out.append(("cooldown", setting,
                            f"{name} served this institution within the last {months.value} months "
                            f"(at most {times} in that period)"))
    if duty == "ssq_review":
        lim = limit(person, "max_open_ssq", rows)
        if lim:
            n = open_ssq_reviews(person, exclude_mapping_id)
            if n >= lim.value:
                out.append(("open_ssq", lim.setting if lim.row_label else "qa.resource_person.max_open_ssq_per_officer",
                            f"{name} has {n} open SSQ review(s); the limit is {lim.value} (R-01)"))
        lim = limit(person, "max_ssq_per_year", rows)
        if lim:
            n = ssq_reviews_in(person, year) + open_ssq_reviews(person, exclude_mapping_id)
            if n >= lim.value:
                out.append(("ssq_year_cap", lim.setting,
                            f"{name} has {n} SSQ review(s) in {year} (done and open); the cap is {lim.value}"))
    lim = limit(person, "max_open_tasks", rows)
    if lim:
        n = open_tasks(person)
        if n >= lim.value:
            out.append(("open_tasks", lim.setting, f"{name} holds {n} open task(s); the limit is {lim.value}"))
    return out


def bool_setting(key, default):
    value = config.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)
