"""Advanced staff query engine (People → Advanced query).

    qs = build_queryset(conditions, logic="AND", base_qs=scoped_staff)

`conditions` is a list of {"field", "op", "value"[, "value2"]} dicts — the
same shape SavedQuery stores. Only fields in SUPPORTED_FIELDS and operators
valid for the field's kind are accepted; anything else raises QueryError.
Nothing from the request reaches the ORM except through this whitelist.

URL shape (GET): logic=AND&field=..&op=..&value=..&value2=.. with the four
condition keys repeated once per condition, in order. A later version can add
a parallel `group` key for nested groups without changing SavedQuery.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from django.db.models import Q
from django.utils import timezone

from .models import Department, Designation, Office, StaffCategory, StaffState, StateOfOrigin


class QueryError(ValueError):
    pass


# name -> (label, queryset path, kind[, model for fk options / choices])
SUPPORTED_FIELDS = {
    "last_name":   ("Surname", "last_name", "str"),
    "first_name":  ("Other names", "first_name", "str"),
    "employee_no": ("File no.", "employee_no", "str"),
    "department":  ("Department", "department__name", "fk", Department),
    "designation": ("Designation", "designation__name", "fk", Designation),
    "designation_rank": ("Designation rank", "designation__rank", "int"),
    "grade_level": ("Grade level (CONTEDISS)", "grade_level", "grade"),
    "state":       ("Staff state", "state__name", "fk", StaffState),
    "office":      ("Office", "office__name", "fk", Office),
    "category":    ("Category", "category__name", "fk", StaffCategory),
    "gender":      ("Sex", "gender", "choice", [("M", "Male"), ("F", "Female")]),
    "state_of_origin": ("State of origin", "state_of_origin__name", "fk", StateOfOrigin),
    "lga":         ("LGA", "lga", "str"),
    "date_of_birth": ("Date of birth", "date_of_birth", "date"),
    "date_first_appointment": ("Date of first appointment", "date_first_appointment", "date"),
    "date_joined_board": ("Date joined the Board", "date_joined_board", "date"),
    "date_present_appointment": ("Date of present appointment", "date_present_appointment", "date"),
    "date_confirmation": ("Date of confirmation", "date_confirmation", "date"),
}

OPERATORS = {
    "eq": "equals", "ne": "not equals", "contains": "contains", "startswith": "starts with",
    "in": "is one of", "empty": "is empty", "notempty": "is not empty",
    "lt": "<", "lte": "≤", "gt": ">", "gte": "≥", "between": "between",
}
NO_VALUE_OPS = {"empty", "notempty"}
KIND_OPS = {
    "str": ["eq", "ne", "contains", "startswith", "in", "empty", "notempty"],
    "fk": ["eq", "ne", "in", "contains", "startswith", "empty", "notempty"],
    "choice": ["eq", "ne", "in", "empty", "notempty"],
    "date": ["eq", "ne", "lt", "lte", "gt", "gte", "between", "empty", "notempty"],
    "int": ["eq", "ne", "lt", "lte", "gt", "gte", "between", "in", "empty", "notempty"],
    "grade": ["eq", "ne", "lt", "lte", "gt", "gte", "between", "in", "empty", "notempty"],
}
MAX_CONDITIONS = 20


def field_meta(name):
    meta = SUPPORTED_FIELDS.get(name)
    if meta is None:
        raise QueryError(f"Unknown field: {name!r}")
    return meta


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------
_REL = re.compile(r"^(today)?\s*([+-])\s*(\d+)\s*([dwmy])$|^today$", re.I)


def parse_date_value(text) -> date:
    """'2024-01-31', '31/01/2024', 'today', 'today-12m', '-30d', '+1y'."""
    raw = str(text or "").strip()
    if not raw:
        raise QueryError("A date is required.")
    m = _REL.match(raw)
    if m:
        today = timezone.localdate()
        if raw.lower() == "today":
            return today
        sign = -1 if m[2] == "-" else 1
        n, unit = int(m[3]) * sign, m[4].lower()
        if unit == "d":
            return today + timedelta(days=n)
        if unit == "w":
            return today + timedelta(weeks=n)
        months = n if unit == "m" else n * 12
        y, mo = divmod(today.month - 1 + months, 12)
        year, month = today.year + y, mo + 1
        day = min(today.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
                              31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return date(year, month, day)
    for pattern in (r"^(\d{4})-(\d{1,2})-(\d{1,2})$", r"^(\d{1,2})/(\d{1,2})/(\d{4})$"):
        m = re.match(pattern, raw)
        if m:
            parts = [int(x) for x in m.groups()]
            y, mo, d = parts if pattern.startswith("^(\\d{4})") else (parts[2], parts[1], parts[0])
            try:
                return date(y, mo, d)
            except ValueError:
                break
    raise QueryError(f"Not a date: {raw!r}. Use YYYY-MM-DD, or e.g. today-12m.")


def _int(text):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        raise QueryError(f"Not a number: {text!r}")


def _split(value):
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value or "").split(",")
    out = [str(v).strip() for v in items if str(v).strip()]
    if not out:
        raise QueryError("Give at least one value, separated by commas.")
    return out


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------
def condition_q(cond, grade_values=None) -> Q:
    name = cond.get("field")
    op = cond.get("op")
    label, path, kind = field_meta(name)[:3]
    if op not in KIND_OPS[kind]:
        raise QueryError(f"'{OPERATORS.get(op, op)}' can't be used with {label}.")
    value, value2 = cond.get("value", ""), cond.get("value2", "")

    if op in NO_VALUE_OPS:
        if kind == "fk":
            q = Q(**{f"{path.split('__')[0]}__isnull": True})
        elif kind in ("date", "int"):
            q = Q(**{f"{path}__isnull": True})
        else:
            q = Q(**{f"{path}__isnull": True}) | Q(**{path: ""})
        return ~q if op == "notempty" else q

    if kind == "grade":
        return _grade_q(path, op, value, value2, grade_values or [])

    if kind == "date":
        conv = parse_date_value
    elif kind == "int":
        conv = _int
    else:
        conv = lambda v: str(v or "").strip()  # noqa: E731

    if op == "in":
        items = _split(value)
        if kind in ("str", "fk"):
            q = Q()
            for it in items:
                q |= Q(**{f"{path}__iexact": it})
            return q
        return Q(**{f"{path}__in": [conv(i) for i in items]})
    if op == "between":
        lo, hi = conv(value), conv(value2)
        if lo > hi:
            lo, hi = hi, lo
        return Q(**{f"{path}__gte": lo, f"{path}__lte": hi})
    v = conv(value)
    if v in ("", None):
        raise QueryError(f"Enter a value for {label}.")
    if op == "eq":
        return Q(**{f"{path}__iexact" if kind in ("str", "fk", "choice") else path: v})
    if op == "ne":
        return ~Q(**{f"{path}__iexact" if kind in ("str", "fk", "choice") else path: v})
    if op == "contains":
        return Q(**{f"{path}__icontains": v})
    if op == "startswith":
        return Q(**{f"{path}__istartswith": v})
    return Q(**{f"{path}__{op}": v})       # lt / lte / gt / gte


def _grade_q(path, op, value, value2, grade_values):
    """grade_level is text ('7', '13'); compare numerically without a DB cast
    by expanding to the matching stored values."""
    numeric = {}
    for g in grade_values:
        if str(g).strip().isdigit():
            numeric.setdefault(int(g), []).append(g)
    if op == "in":
        wanted = {_int(v) for v in _split(value)}
        return Q(**{f"{path}__in": [g for n, gs in numeric.items() if n in wanted for g in gs]})
    if op == "between":
        lo, hi = sorted((_int(value), _int(value2)))
        test = lambda n: lo <= n <= hi  # noqa: E731
    else:
        x = _int(value)
        test = {"eq": lambda n: n == x, "ne": lambda n: n != x, "lt": lambda n: n < x,
                "lte": lambda n: n <= x, "gt": lambda n: n > x, "gte": lambda n: n >= x}[op]
    matched = [g for n, gs in numeric.items() if test(n) for g in gs]
    if op == "ne":
        return ~Q(**{f"{path}__in": [g for n, gs in numeric.items() if n == _int(value) for g in gs]})
    return Q(**{f"{path}__in": matched})


def clean_conditions(conditions):
    """Validate and normalise; raises QueryError. Returns a list of dicts."""
    if not isinstance(conditions, (list, tuple)):
        raise QueryError("Conditions must be a list.")
    if len(conditions) > MAX_CONDITIONS:
        raise QueryError(f"At most {MAX_CONDITIONS} conditions.")
    out = []
    for c in conditions:
        if not isinstance(c, dict):
            raise QueryError("Each condition must be an object.")
        name = str(c.get("field") or "")
        if not name:
            continue
        field_meta(name)
        op = str(c.get("op") or "")
        item = {"field": name, "op": op, "value": c.get("value", "")}
        if c.get("value2") not in (None, ""):
            item["value2"] = c.get("value2")
        out.append(item)
    return out


def build_queryset(conditions, logic="AND", base_qs=None):
    from .models import Staff
    qs = base_qs if base_qs is not None else Staff.objects.all()
    logic = (logic or "AND").upper()
    if logic not in ("AND", "OR"):
        raise QueryError("Logic must be AND or OR.")
    conditions = clean_conditions(conditions)
    if not conditions:
        return qs
    grade_values = None
    if any(SUPPORTED_FIELDS[c["field"]][2] == "grade" for c in conditions):
        grade_values = list(Staff.all_objects.exclude(grade_level="")
                            .values_list("grade_level", flat=True).distinct())
    combined = None
    for c in conditions:
        q = condition_q(c, grade_values)
        combined = q if combined is None else (combined & q if logic == "AND" else combined | q)
    return qs.filter(combined).distinct()


def conditions_from_querydict(data):
    """Read repeated field/op/value/value2 keys from request.GET / POST."""
    fields, ops = data.getlist("field"), data.getlist("op")
    values, values2 = data.getlist("value"), data.getlist("value2")
    out = []
    for i, name in enumerate(fields):
        if not name:
            continue
        out.append({"field": name, "op": ops[i] if i < len(ops) else "",
                    "value": values[i] if i < len(values) else "",
                    "value2": values2[i] if i < len(values2) else ""})
    return out


def describe(cond) -> str:
    label = SUPPORTED_FIELDS.get(cond.get("field"), (cond.get("field"),))[0]
    op = OPERATORS.get(cond.get("op"), cond.get("op"))
    if cond.get("op") in NO_VALUE_OPS:
        return f"{label} {op}"
    if cond.get("op") == "between":
        return f"{label} between {cond.get('value')} and {cond.get('value2')}"
    return f"{label} {op} {cond.get('value')}"


def builder_schema(user):
    """Field metadata for the builder UI (json_script). FK options are the
    related objects' names; departments outside the user's scope are listed
    but flagged so the UI shows them disabled."""
    schema = {}
    allowed_depts = None          # None = every department
    if not (user.is_superuser or user.has_perm("accounts.access_all_departments")):
        allowed_depts = set(Department.objects.filter(pk=getattr(user, "department_id", None))
                            .values_list("name", flat=True))
    for name, meta in SUPPORTED_FIELDS.items():
        label, _path, kind = meta[:3]
        entry = {"label": label, "kind": kind,
                 "ops": [[o, OPERATORS[o]] for o in KIND_OPS[kind]]}
        if kind == "fk":
            model = meta[3]
            qs = model.objects.all()
            if hasattr(model, "is_active"):
                qs = qs.filter(is_active=True)
            names = list(qs.order_by(*(["-rank"] if model is Designation else []), "name")
                         .values_list("name", flat=True))
            if model is Department and allowed_depts is not None:
                entry["options"] = [[n, n, n not in allowed_depts] for n in names]
            else:
                entry["options"] = [[n, n, False] for n in names]
        elif kind == "choice":
            entry["options"] = [[v, lbl, False] for v, lbl in meta[3]]
        schema[name] = entry
    return schema
