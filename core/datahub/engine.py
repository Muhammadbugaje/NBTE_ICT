"""Data Hub engine: model introspection, Excel export and previewed import.

Workbook layout
---------------
* ``README``   - what the file holds, the import order, notes per sheet.
* one sheet per model (or per "links" set), in dependency order. Row 1 holds
  the field names (each header has a comment: label, type, what a link
  points to); one row per record below it.
* ``__meta``   - hidden: sheet title -> sheet id, so renamed workbooks still
  import, plus the format version.
* ``__long``   - hidden, only when needed: text longer than an Excel cell
  (32k characters) is stored here in pieces.

Cell conventions
----------------
* A foreign key holds the natural key of the row it points to, e.g. an
  office's department ``"registry"``, an approval step's chain
  ``"leave-chain"``; composite keys are joined with ``" | "``
  (``"qa-onboarding | 2 | desk-review"``).
* A many-to-many holds one natural key per line.
* Permissions are written ``app_label.codename``; content types
  ``app_label.model``.
* JSON fields hold JSON text; date-times ISO 8601 text; booleans TRUE/FALSE.

Import
------
Every sheet is an upsert keyed on the natural key (then on any other unique
column, so a seeded row with the same name is updated instead of
duplicated). Nothing is deleted. The whole run is one transaction; a preview
is the same run rolled back, so its counts (new / changed / unchanged /
errors) are exact.
"""
import datetime
import decimal
import io
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass, field as dc_field

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.utils import dateparse, timezone

from . import registry

FORMAT_VERSION = 1
KEY_SEP = " | "
LIST_SEP = "\n"
META_SHEET = "__meta"
LONG_SHEET = "__long"
README_SHEET = "README"
INSTRUCTIONS_SHEET = "Instructions"   # per-category how-to (Category.instructions); skipped on import
LISTS_SHEET = "__lists"               # hidden: values behind the dropdowns; skipped on import
CELL_MAX = 32000
LONG_TOKEN = "@@long:"
MAX_ROWS = 100_000
FORMULA_PREFIXES = ("=", "+", "-", "@")
USER_SHEET = "accounts.User"
PASSWORD_COLUMN = "password"     # optional, Users sheet only: a Django password hash, never plaintext
_ILLEGAL = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")

CHARLIKE = (models.CharField, models.TextField)          # Slug/Email/URL are CharFields
SPECIAL_TARGETS = ("contenttypes.ContentType", "auth.Permission")


class HubError(Exception):
    """The whole workbook is refused."""


# ---------------------------------------------------------------------------
# Plan: which sheets exist, their columns, keys and import order
# ---------------------------------------------------------------------------
@dataclass
class SheetDef:
    id: str
    model: type
    spec: registry.Spec
    category: str
    title: str = ""
    key: tuple = ()
    fields: list = dc_field(default_factory=list)       # concrete fields, column order
    m2m: list = dc_field(default_factory=list)
    deferred: set = dc_field(default_factory=set)       # set in a second pass
    links_only: bool = False
    owner_category: str = ""                            # category of the model itself

    @property
    def label(self):
        return self.model._meta.label

    @property
    def verbose(self):
        name = str(self.model._meta.verbose_name_plural)
        name = name[:1].upper() + name[1:]
        return f"{name} (links)" if self.links_only else name

    @property
    def columns(self):
        return [f.name for f in self.fields] + [f.name for f in self.m2m]

    def all_fields(self):
        return list(self.fields) + list(self.m2m)


def _label(model):
    return model._meta.label


def _auto_key(model):
    meta = model._meta
    singles = [f for f in meta.concrete_fields if f.unique and not f.primary_key]
    for pref in ("code", "slug", "email", "employee_no", "action_code", "event_code", "number", "name"):
        for f in singles:
            if f.name == pref:
                return (f.name,)
    if meta.unique_together:
        return tuple(meta.unique_together[0])
    for c in meta.constraints:
        if isinstance(c, models.UniqueConstraint) and c.fields and c.condition is None:
            return tuple(c.fields)
    for f in singles:
        return (f.name,)
    return ()


_PLAN = None


def plan():
    """Build (once) the list of sheets in import order."""
    global _PLAN
    if _PLAN is not None:
        return _PLAN

    order = []          # (spec, category, model)
    for cat in registry.CATEGORIES:
        for spec in cat.specs:
            order.append((spec, cat.key, apps.get_model(spec.label)))
    # A model with several sheets (user accounts) is placed at its FIRST sheet:
    # links to it resolve against every account already in the database.
    pos, cat_of = {}, {}
    for i, (_s, c, m) in enumerate(order):
        pos.setdefault(_label(m), i)
        cat_of.setdefault(_label(m), c)
    cat_index = {c.key: i for i, c in enumerate(registry.CATEGORIES)}

    mains = {}
    links = {c.key: [] for c in registry.CATEGORIES}
    for here, (spec, cat, model) in enumerate(order):
        key = tuple(spec.key) or _auto_key(model)
        if not key:
            raise ImproperlyConfigured(f"Data Hub: {spec.label} has no natural key; give Spec(key=...).")
        skip = set(spec.exclude) | registry.ACTOR_FIELDS
        concrete, m2m, deferred = [], [], set()
        link_fields = {}

        def place(f, target_label):
            if target_label in SPECIAL_TARGETS:
                return "now"
            if target_label not in pos:
                return None
            forced = registry.LINK_CATEGORY.get(f"{spec.label}.{f.name}")
            if forced and forced != cat:
                return forced
            tpos = pos[target_label]
            if tpos < here:
                return "now"
            if cat_of[target_label] == cat:
                return "defer"
            return cat_of[target_label]

        for f in model._meta.concrete_fields:
            if f.primary_key or f.name in skip:
                continue
            if getattr(f, "auto_now", False) or getattr(f, "auto_now_add", False):
                continue
            if f.is_relation:
                where = place(f, _label(f.related_model))
                if where is None:
                    if not f.null:
                        raise ImproperlyConfigured(
                            f"Data Hub: {spec.label}.{f.name} needs {_label(f.related_model)}, which is not covered.")
                    continue
                if where == "defer":
                    if not f.null or f.name in key:
                        raise ImproperlyConfigured(f"Data Hub: {spec.label}.{f.name} points forward but is required.")
                    deferred.add(f.name)
                elif where != "now":
                    if not f.null or f.name in key:
                        raise ImproperlyConfigured(f"Data Hub: {spec.label}.{f.name} points to a later category.")
                    if cat_index[where] < cat_index[cat]:
                        raise ImproperlyConfigured(f"Data Hub: {spec.label}.{f.name} category order.")
                    link_fields.setdefault(where, []).append(f)
                    continue
            concrete.append(f)
        for f in model._meta.many_to_many:
            if f.name in skip or not f.remote_field.through._meta.auto_created:
                continue
            where = place(f, _label(f.related_model))
            if where is None:
                continue
            if where == "defer":
                deferred.add(f.name)
            elif where != "now":
                link_fields.setdefault(where, []).append(f)
                continue
            m2m.append(f)
        missing = [k for k in key if k not in {f.name for f in concrete}]
        if missing:
            raise ImproperlyConfigured(f"Data Hub: {spec.label} key {key} uses unexported {missing}.")
        # Key columns first; soft-delete bookkeeping last (columns are matched by name on import).
        concrete.sort(key=lambda f: (0, key.index(f.name)) if f.name in key else
                      ((2, 0) if f.name in ("is_deleted", "deleted_at") else (1, 0)))
        sheet_id = f"{spec.label}#{spec.sheet}" if spec.sheet else spec.label
        sd = SheetDef(id=sheet_id, model=model, spec=spec, category=cat, key=key,
                      fields=concrete, m2m=m2m, deferred=deferred, owner_category=cat)
        mains[sheet_id] = sd
        for target_cat, flist in link_fields.items():
            kf = [f for f in concrete if f.name in key]
            lsd = SheetDef(id=f"{sheet_id}@links:{target_cat}", model=model, spec=spec, category=target_cat,
                           key=key, fields=kf + [f for f in flist if not f.many_to_many],
                           m2m=[f for f in flist if f.many_to_many], links_only=True, owner_category=cat,
                           deferred={f.name for f in flist if _label(f.related_model) == _label(model)})
            links[target_cat].append(lsd)

    sheets = []
    for cat in registry.CATEGORIES:
        for spec in cat.specs:
            sheets.append(mains[f"{spec.label}#{spec.sheet}" if spec.sheet else spec.label])
        sheets.extend(links[cat.key])
    _assign_titles(sheets)
    _PLAN = sheets
    return sheets


def _assign_titles(sheets):
    used = {README_SHEET.lower(), META_SHEET, LONG_SHEET, INSTRUCTIONS_SHEET.lower(), LISTS_SHEET}
    for sd in sheets:
        name = sd.spec.title if (sd.spec.title and not sd.links_only) else sd.verbose
        if sd.spec.title and sd.links_only:
            name = f"{sd.spec.title} (links)"
        base = re.sub(r"[\[\]:*?/\\]", " ", name)[:31].strip()
        title, n = base, 2
        while title.lower() in used:
            suffix = f" {n}"
            title = base[:31 - len(suffix)] + suffix
            n += 1
        used.add(title.lower())
        sd.title = title


def sheets_for(categories):
    """Sheets for a list of category keys (or ["everything"]), in import order."""
    cats = set(categories or [])
    every = not cats or registry.EVERYTHING in cats
    return [sd for sd in plan() if every or sd.category in cats]


def sheet_by_id(sheet_id):
    for sd in plan():
        if sd.id == sheet_id:
            return sd
    return None


# ---------------------------------------------------------------------------
# Scopes and row rules (one model, several sheets: NBTE vs institution users)
# ---------------------------------------------------------------------------
def _q_nbte_records():
    from django.db.models import Q
    return Q(user__isnull=True) | Q(user__user_type="staff")


SCOPES = {
    "user_staff": lambda: models.Q(user_type="staff"),
    "user_institution": lambda: models.Q(user_type="institution"),
    "user_portal": lambda: models.Q(user_type__in=("resource_person", "professional_body")),
    "staff_nbte": _q_nbte_records,
    "override_staff": lambda: models.Q(user__user_type="staff"),
    "override_portal": lambda: ~models.Q(user__user_type="staff"),
    "active_only": lambda: models.Q(active=True),
    "is_active_only": lambda: models.Q(is_active=True),
}


def scoped_rows(sd):
    """The rows a sheet exports (all rows unless its spec names a scope)."""
    qs = sd.model._base_manager.all()
    scope = sd.spec.scope
    return qs.filter(SCOPES[scope]()) if scope else qs


def is_user_sheet(sd):
    return sd.label == USER_SHEET and not sd.links_only


def _unknown_link_message(f, raw):
    target = _label(f.related_model)
    text = _to_text(raw)
    if target == "institutions.Institution":
        return (f"{f.name}: no institution with code '{text}'. Institution users can only be imported "
                "into an institution that already exists — import the Institutions category first or "
                "correct the code.")
    if target == "staff.Department":
        return f"{f.name}: no NBTE department with code '{text}' (see the Departments sheet)."
    if target == "accounts.User":
        return f"{f.name}: no user account with e-mail (or staff number) '{text}'."
    if target == "assets.AssetCategory":
        return f"{f.name}: no asset category named '{text}' (see the Asset categories sheet)."
    if target == "assets.Asset":
        return f"{f.name}: no asset with tag '{text}' (see the Assets sheet)."
    return f"{f.name}: no {target} with key '{text}'"


_TYPE_SHEET = {"staff": "Users (NBTE users & staff)", "institution": "Institution users",
               "resource_person": "Portal users", "professional_body": "Portal users"}


def _user_type_rule(parsed, obj, allowed, default):
    """Force/validate user_type on a user sheet; returns the effective type."""
    given = parsed.get("user_type") or ""
    utype = given or (obj.user_type if obj is not None else default)
    if not utype:
        raise CellError(f"user_type is required on this sheet ({' or '.join(allowed)})")
    if utype not in allowed:
        raise CellError(f"user_type '{utype}' does not belong on this sheet: put it on the "
                        f"'{_TYPE_SHEET.get(utype, utype)}' sheet.")
    if obj is not None and obj.user_type not in allowed:
        raise CellError(f"{obj.email} is an existing {obj.get_user_type_display()} account; it cannot be "
                        "changed into another kind of account by an import.")
    parsed["user_type"] = utype
    return utype


def _role_rule(parsed, obj, utype):
    from accounts.audience import role_problem
    from accounts.models import Role
    role_id = parsed["role"] if "role" in parsed else (obj.role_id if obj is not None else None)
    if role_id:
        problem = role_problem(Role.objects.filter(pk=role_id).first(), utype)
        if problem:
            raise CellError(f"role: {problem}")


def _check_nbte_user(parsed, obj):
    utype = _user_type_rule(parsed, obj, ("staff",), "staff")
    for f in ("institution", "professional_body"):
        if parsed.get(f):
            raise CellError(f"{f} must be blank for an NBTE staff account")
    superuser = parsed.get("is_superuser") if "is_superuser" in parsed else getattr(obj, "is_superuser", False)
    dept = parsed["department"] if "department" in parsed else getattr(obj, "department_id", None)
    had_dept = obj is not None and obj.department_id is not None
    from accounts.models import Role
    role_id = parsed["role"] if "role" in parsed else getattr(obj, "role_id", None)
    above_departments = bool(role_id) and Role.objects.filter(
        pk=role_id, code__in=("executive-secretary", "super-admin")).exists()
    if not dept and not superuser and not above_departments and (obj is None or had_dept):
        raise CellError("department is required for an NBTE staff account (the department code from the "
                        "Departments sheet)")
    _role_rule(parsed, obj, utype)


def _check_institution_user(parsed, obj):
    utype = _user_type_rule(parsed, obj, ("institution",), "institution")
    for f in ("department", "professional_body"):
        if parsed.get(f):
            raise CellError(f"{f} must be blank for an institution user")
    _role_rule(parsed, obj, utype)


def _check_portal_user(parsed, obj):
    utype = _user_type_rule(parsed, obj, ("resource_person", "professional_body"), "")
    if parsed.get("department"):
        raise CellError("department must be blank for a portal account")
    _role_rule(parsed, obj, utype)


def _user_type_of(pk):
    from accounts.models import User
    return User._base_manager.filter(pk=pk).values_list("user_type", "institution_id").first() or (None, None)


def _check_nbte_staff_record(parsed, obj):
    uid = parsed["user"] if "user" in parsed else getattr(obj, "user_id", None)
    if uid and _user_type_of(uid)[0] != "staff":
        raise CellError("user: the staff directory only links NBTE staff accounts")


def _check_institution_link(parsed, obj):
    uid = parsed["user"] if "user" in parsed else getattr(obj, "user_id", None)
    inst = parsed["institution"] if "institution" in parsed else getattr(obj, "institution_id", None)
    utype, user_inst = _user_type_of(uid)
    if utype != "institution":
        raise CellError("user: only institution accounts can be linked to an institution")
    if user_inst != inst:
        raise CellError("user: this account belongs to another institution (its institution column on the "
                        "Institution users sheet)")


def _check_override(allowed_staff):
    def check(parsed, obj):
        uid = parsed["user"] if "user" in parsed else getattr(obj, "user_id", None)
        is_staff = _user_type_of(uid)[0] == "staff"
        if is_staff != allowed_staff:
            raise CellError("user: overrides of " + ("portal accounts go on the Portal permission overrides sheet"
                                                     if allowed_staff else "NBTE staff go on the Users category"))
    return check


ROW_CHECKS = {
    "nbte_user": _check_nbte_user,
    "institution_user": _check_institution_user,
    "portal_user": _check_portal_user,
    "nbte_staff_record": _check_nbte_staff_record,
    "institution_link": _check_institution_link,
    "override_staff": _check_override(True),
    "override_portal": _check_override(False),
}


def counts():
    """{category_key: [(SheetDef, count), ...]} for the hub cards (main sheets only)."""
    out = {}
    for sd in plan():
        if sd.links_only:
            continue
        out.setdefault(sd.category, []).append((sd, scoped_rows(sd).count()))
    return out


# ---------------------------------------------------------------------------
# Natural keys
# ---------------------------------------------------------------------------
def _fmt_part(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, decimal.Decimal):
        return format(value.normalize(), "f") if value == value.to_integral() else str(value)
    if isinstance(value, datetime.datetime) and timezone.is_aware(value):
        # One spelling per instant, whatever zone the cell was typed in.
        return value.astimezone(datetime.timezone.utc).isoformat()
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if hasattr(value, "name") and not isinstance(value, str):   # FieldFile
        return value.name or ""
    return str(value)


def norm(text):
    text = " ".join(str(text or "").split())
    text = re.sub(r"\s*\|\s*", " | ", text)
    return text.casefold()


class Keys:
    """pk <-> natural key maps per model, built lazily, updated as rows are saved."""

    def __init__(self):
        self.pk2key = {}
        self.key2pk = {}

    def _build(self, model):
        label = _label(model)
        if label == "contenttypes.ContentType":
            from django.contrib.contenttypes.models import ContentType
            m = {pk: f"{a}.{n}" for pk, a, n in ContentType.objects.values_list("pk", "app_label", "model")}
        elif label == "auth.Permission":
            from django.contrib.auth.models import Permission
            rows = list(Permission.objects.values_list("pk", "content_type__app_label",
                                                       "content_type__model", "codename"))
            short = {}
            for _pk, a, _m, c in rows:
                short[f"{a}.{c}"] = short.get(f"{a}.{c}", 0) + 1
            # "app_label.codename", or "app_label.model.codename" when two models of
            # one app share a codename. Both spellings are accepted on import.
            m = {pk: (f"{a}.{c}" if short[f"{a}.{c}"] == 1 else f"{a}.{mo}.{c}") for pk, a, mo, c in rows}
            self.pk2key[label] = m
            rev = {norm(f"{a}.{mo}.{c}"): pk for pk, a, mo, c in rows}
            rev.update({norm(k): pk for pk, k in m.items()})
            self.key2pk[label] = rev
            return
        else:
            sd = sheet_by_id(label)
            if sd is None:
                raise KeyError(label)
            fields = [model._meta.get_field(k) for k in sd.key]
            m = {}
            for row in model._base_manager.values_list("pk", *[f.attname for f in fields]):
                m[row[0]] = self._compose(fields, row[1:])
        self.pk2key[label] = m
        rev = {}
        for pk, k in m.items():
            rev.setdefault(norm(k), pk)
        self.key2pk[label] = rev

    def _compose(self, fields, values):
        parts = []
        for f, v in zip(fields, values):
            if f.is_relation:
                parts.append(self.key_of(f.related_model, v) if v is not None else "")
            else:
                parts.append(_fmt_part(v))
        return KEY_SEP.join(parts)

    def key_of(self, model, pk):
        label = _label(model)
        if label not in self.pk2key:
            self._build(model)
        return self.pk2key[label].get(pk, f"#{pk}")

    def lookup(self, model, text):
        label = _label(model)
        if label not in self.key2pk:
            self._build(model)
        return self.key2pk[label].get(norm(text))

    def remember(self, sd, obj):
        label = sd.label
        if label not in self.pk2key:
            self._build(sd.model)
            return
        fields = [sd.model._meta.get_field(k) for k in sd.key]
        old = self.pk2key[label].get(obj.pk)
        if old is not None and self.key2pk[label].get(norm(old)) == obj.pk:
            del self.key2pk[label][norm(old)]
        k = self._compose(fields, [getattr(obj, f.attname) for f in fields])
        self.pk2key[label][obj.pk] = k
        self.key2pk[label][norm(k)] = obj.pk


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def _neutral(text):
    text = _ILLEGAL.sub("", text)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def _unneutral(text):
    if len(text) > 1 and text[0] == "'" and text[1:].startswith(FORMULA_PREFIXES):
        return text[1:]
    return text


class _LongStore:
    def __init__(self):
        self.items = []

    def put(self, text):
        self.items.append(text)
        return f"{LONG_TOKEN}{len(self.items)}"


def _export_value(f, value, keys, longs):
    if value is None:
        return None
    if f.is_relation:
        return keys.key_of(f.related_model, value)
    if isinstance(f, models.JSONField):
        value = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    elif isinstance(value, decimal.Decimal):
        return float(value)
    elif isinstance(value, datetime.datetime):
        return value.isoformat()
    elif isinstance(value, (datetime.date, datetime.time)):
        return value
    elif hasattr(value, "name") and not isinstance(value, str):
        value = value.name or ""
    value = str(value)
    if len(value) > CELL_MAX:
        return longs.put(value)
    return _neutral(value)


def sheet_rows(sd, keys, longs, extra=None):
    """Rows (lists) for one sheet, ordered by pk. ``extra(pk)`` adds trailing cells."""
    model = sd.model
    attnames = [f.attname for f in sd.fields]
    m2m_maps = {}
    for f in sd.m2m:
        through = f.remote_field.through
        src, dst = f.m2m_field_name() + "_id", f.m2m_reverse_field_name() + "_id"
        mp = {}
        for a, b in through._default_manager.values_list(src, dst):
            mp.setdefault(a, []).append(keys.key_of(f.related_model, b))
        m2m_maps[f.name] = mp
    labels = {f.name: dict(f.flatchoices) for f in sd.fields
              if sd.spec.labels and getattr(f, "choices", None) and not f.is_relation}
    for row in scoped_rows(sd).order_by("pk").values_list("pk", *attnames):
        pk, vals = row[0], row[1:]
        out = [_export_value(f, v, keys, longs) for f, v in zip(sd.fields, vals)]
        for i, f in enumerate(sd.fields):
            if f.name in labels and vals[i] in labels[f.name]:
                out[i] = str(labels[f.name][vals[i]])
        for f in sd.m2m:
            items = sorted(m2m_maps[f.name].get(pk, []))
            out.append(_neutral(LIST_SEP.join(items)) if items else None)
        if extra is not None:
            out.extend(extra(pk))
        yield out


def _header_comment(sd, f):
    from openpyxl.comments import Comment
    bits = [str(getattr(f, "verbose_name", f.name)).capitalize()]
    if f.many_to_many:
        bits.append(f"List: one {_label(f.related_model)} key per line.")
    elif f.is_relation:
        tgt = _label(f.related_model)
        tsd = sheet_by_id(tgt)
        fmt = "app_label.codename (or app_label.model.codename)" if tgt == "auth.Permission" else (
            "app_label.model" if tgt == "contenttypes.ContentType" else KEY_SEP.join(tsd.key) if tsd else "key")
        bits.append(f"Link to {tgt} by: {fmt}.")
    else:
        bits.append(f.get_internal_type().replace("Field", ""))
    if f.name in sd.key:
        bits.append("Part of the row key.")
    if f.name in sd.deferred:
        bits.append("Set after all rows are loaded.")
    if getattr(f, "choices", None):
        if sd.spec.labels:
            vals = ", ".join(str(lbl) for _v, lbl in f.flatchoices)[:250]
            bits.append(f"One of (dropdown): {vals}. The codes ({', '.join(str(v) for v, _l in f.flatchoices)}) "
                        "are accepted too."[:400])
        else:
            vals = ", ".join(str(v) for v, _l in f.flatchoices)[:250]
            bits.append(f"One of: {vals}")
    if f.name in sd.spec.actor_default:
        bits.append("Blank: the administrator who imports the file.")
    if not f.many_to_many and not f.null and not f.blank and not f.has_default() and f.name not in sd.key:
        bits.append("Required.")
    c = Comment("\n".join(bits), "NBTE Data Hub")
    c.width, c.height = 320, 140
    return c


def _password_hashes():
    from django.contrib.auth import get_user_model
    return dict(get_user_model()._base_manager.values_list("pk", "password"))


def export_workbook(categories, include_rows=True, source_label="", include_password_hashes=False):
    """Bytes of an .xlsx holding the given categories (or everything).

    ``include_password_hashes`` (superuser opt-in, off by default) adds a
    ``password`` column to the Users sheet holding each account's Django
    password *hash* (never plaintext), so people keep their passwords on the
    new database."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    head_fill = PatternFill("solid", fgColor="1F6B3E")
    link_fill = PatternFill("solid", fgColor="7A5B12")
    head_font = Font(bold=True, color="FFFFFF")
    sheets = sheets_for(categories)
    keys, longs = Keys(), _LongStore()
    lists = _Lists()

    wb = Workbook()
    readme = wb.active
    readme.title = README_SHEET
    _write_instructions(wb, sheets, Font, Alignment)
    counts_by_sheet = {}
    hashes_written = False
    for sd in sheets:
        ws = wb.create_sheet(sd.title)
        with_hash = bool(include_password_hashes and include_rows and is_user_sheet(sd))
        ws.append(sd.columns + ([PASSWORD_COLUMN] if with_hash else []))
        for i, f in enumerate(sd.all_fields(), start=1):
            cell = ws.cell(row=1, column=i)
            cell.fill = link_fill if sd.links_only else head_fill
            cell.font = head_font
            cell.comment = _header_comment(sd, f)
            ws.column_dimensions[get_column_letter(i)].width = max(12, min(40, len(f.name) + 6))
        extra = None
        if with_hash:
            from openpyxl.comments import Comment
            i = len(sd.columns) + 1
            cell = ws.cell(row=1, column=i)
            cell.fill, cell.font = PatternFill("solid", fgColor="9B1C1C"), head_font
            cell.comment = Comment("Django password hash (never plaintext). Lets people keep their password on "
                                   "the new database. Treat this file as secret.", "NBTE Data Hub")
            ws.column_dimensions[get_column_letter(i)].width = 40
            hashes = _password_hashes()
            extra = lambda pk: [hashes.get(pk) or None]  # noqa: E731
            hashes_written = True
        n = 0
        if include_rows:
            for row in sheet_rows(sd, keys, longs, extra=extra):
                ws.append(row)
                n += 1
        counts_by_sheet[sd.id] = n
        _add_dropdowns(ws, sd, n, lists, keys)
        ws.freeze_panes = "A2"
        if sd.columns:
            ws.auto_filter.ref = f"A1:{get_column_letter(len(sd.columns))}{max(n + 1, 2)}"

    _write_readme(readme, sheets, counts_by_sheet, include_rows, source_label, Font, Alignment)
    if hashes_written:
        readme.insert_rows(5)
        readme["A5"] = ("WARNING: this file contains password hashes of every user account. Keep it secret, "
                        "move it securely and delete it after the import.")
        readme["A5"].font = Font(bold=True, color="9B1C1C")

    lists.write(wb)
    meta = wb.create_sheet(META_SHEET)
    meta.append(["sheet", "id", "category", "format_version"])
    for sd in sheets:
        meta.append([sd.title, sd.id, sd.category, FORMAT_VERSION])
    meta.sheet_state = "hidden"
    if longs.items:
        lw = wb.create_sheet(LONG_SHEET)
        lw.append(["id", "part", "text"])
        for i, text in enumerate(longs.items, start=1):
            for p in range(0, len(text), CELL_MAX):
                lw.append([i, p // CELL_MAX, _neutral(text[p:p + CELL_MAX])])
        lw.sheet_state = "hidden"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


DROPDOWN_ROWS = 1000      # blank rows below the data that still get the dropdowns


class _Lists:
    """The hidden ``__lists`` sheet: one column of keys per linked table, used by dropdowns."""

    def __init__(self):
        self.columns = {}            # target label -> (column letter, [values])

    def ref(self, model, keys):
        from openpyxl.utils import get_column_letter
        label = _label(model)
        if label not in self.columns:
            qs = model._base_manager.all()
            if label == USER_SHEET:
                qs = qs.filter(user_type="staff", is_active=True)
            elif any(f.name == "is_deleted" for f in model._meta.concrete_fields):
                qs = qs.filter(is_deleted=False)
            values = sorted({keys.key_of(model, pk) for pk in qs.values_list("pk", flat=True)}, key=str.casefold)
            self.columns[label] = (get_column_letter(len(self.columns) + 1), values)
        col, values = self.columns[label]
        return f"'{LISTS_SHEET}'!${col}$2:${col}${max(len(values) + 1, 2)}"

    def write(self, wb):
        if not self.columns:
            return
        ws = wb.create_sheet(LISTS_SHEET)
        for label, (col, values) in self.columns.items():
            ws[f"{col}1"] = label
            for i, v in enumerate(values, start=2):
                ws[f"{col}{i}"] = _neutral(v)
        ws.sheet_state = "hidden"


def _add_dropdowns(ws, sd, n, lists, keys):
    """Excel dropdowns: labels of choice columns (``Spec.labels``) and the keys of linked
    rows (``Spec.dropdowns``). Warning style: a value not in the list (a code, a staff
    number) can still be typed; the import decides."""
    if not (sd.spec.labels or sd.spec.dropdowns) or sd.links_only:
        return
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation
    last = n + 1 + DROPDOWN_ROWS
    for i, f in enumerate(sd.all_fields(), start=1):
        formula = None
        if sd.spec.labels and getattr(f, "choices", None) and not f.is_relation:
            text = ",".join(str(lbl) for _v, lbl in f.flatchoices)
            if "," not in "".join(str(lbl) for _v, lbl in f.flatchoices) and len(text) < 250:
                formula = f'"{text}"'
        elif f.name in sd.spec.dropdowns and f.is_relation and not f.many_to_many:
            formula = lists.ref(f.related_model, keys)
        if not formula:
            continue
        dv = DataValidation(type="list", formula1=formula, allow_blank=True, errorStyle="warning",
                            showErrorMessage=True, error="Not in the list. The import checks the value.")
        col = get_column_letter(i)
        dv.add(f"{col}2:{col}{last}")
        ws.add_data_validation(dv)


def _write_instructions(wb, sheets, Font, Alignment):
    present = []
    for sd in sheets:
        cat = registry.get_category(sd.category)
        if cat and cat.instructions and cat not in present:
            present.append(cat)
    if not present:
        return
    ws = wb.create_sheet(INSTRUCTIONS_SHEET)
    for cat in present:
        for i, line in enumerate(cat.instructions):
            ws.append([line])
            cell = ws.cell(row=ws.max_row, column=1)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if i == 0:
                cell.font = Font(bold=True, size=13)
        ws.append([])
    ws.column_dimensions["A"].width = 120


def _write_readme(ws, sheets, counts_by_sheet, include_rows, source_label, Font, Alignment):
    cats = {c.key: c for c in registry.CATEGORIES}
    ws.append(["NBTE ICT System - Data Hub workbook"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"{'Export' if include_rows else 'Blank template'} generated {timezone.localtime():%Y-%m-%d %H:%M}"
               + (f" from {source_label}" if source_label else "")])
    ws.append(["Import it on the Data Hub page (Administration > Data Hub). Rows are matched by their key "
               "columns; existing rows are updated, new ones created, nothing is deleted."])
    ws.append(["Links between sheets use natural keys (codes, names, e-mails). Composite keys are joined "
               "with ' | '; lists hold one key per line. Do not rename the header row."])
    ws.append([])
    ws.append(["#", "Sheet", "Category", "Model", "Key columns", "Rows", "Notes"])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for i, sd in enumerate(sheets, start=1):
        note = sd.spec.note
        if sd.links_only:
            note = (f"Links for {sd.label} that point at rows loaded in this category "
                    f"(the rows themselves are in '{cats[sd.owner_category].title}').")
        ws.append([i, sd.title, cats[sd.category].title, sd.label, KEY_SEP.join(sd.key),
                   counts_by_sheet.get(sd.id, 0), note])
    # Recommended import order (categories, dependency order) + the user split.
    present = []
    for sd in sheets:
        if sd.category not in present:
            present.append(sd.category)
    ws.append([])
    ws.append(["Recommended import order"])
    ws[ws.max_row][0].font = Font(bold=True, size=12)
    ws.append(["Import this whole workbook in one go (it loads the sheets in the order above), or the "
               "categories one by one in this order: each later category can point back at rows of an "
               "earlier one."])
    for n, key in enumerate(present, start=1):
        ws.append([n, cats[key].title, "", "", "", "", cats[key].summary])
    ws.append([])
    ws.append(["User accounts are split: 'Users' holds NBTE staff only (department code required, NBTE "
               "roles only); 'Institution users' holds institution portal accounts (institution code "
               "required — an unknown code is refused — and inst-… roles only), loaded after Institutions; "
               "'Portal users' holds resource persons and professional-body accounts. A row on the wrong "
               "sheet is refused."])
    if "assets" in present:
        ws.append(["Assets come after Organisation and NBTE users & staff: departments are linked by code, people "
                   "by e-mail (or staff number). A blank asset_tag is generated; holders are on the Asset "
                   "assignments sheet; photos and attachments travel as file paths only (copy the protected "
                   "media folder). See the Instructions sheet."])
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 34
    ws.column_dimensions["C"].width = 40
    ws.column_dimensions["D"].width = 36
    ws.column_dimensions["E"].width = 44
    ws.column_dimensions["F"].width = 8
    ws.column_dimensions["G"].width = 90
    for row in ws.iter_rows(min_row=1, max_row=4):
        for c in row:
            c.alignment = Alignment(wrap_text=False)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
@dataclass
class SheetReport:
    id: str
    title: str
    label: str
    category: str
    links_only: bool = False
    rows: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list = dc_field(default_factory=list)      # [(row_no, message)]
    warnings: list = dc_field(default_factory=list)
    changed_fields: dict = dc_field(default_factory=dict)

    @property
    def error_count(self):
        return len(self.errors)

    def add_error(self, row, msg):
        self.errors.append((row, msg))

    def add_warning(self, row, msg):
        if len(self.warnings) < 500:
            self.warnings.append((row, msg))


@dataclass
class Report:
    applied: bool = False
    rolled_back: bool = False
    sheets: list = dc_field(default_factory=list)
    ignored: list = dc_field(default_factory=list)
    fatal: str = ""
    needs_default_password: bool = False      # some Users row has no password hash

    def total(self, attr):
        return sum(getattr(s, attr) for s in self.sheets)

    @property
    def created(self):
        return self.total("created")

    @property
    def updated(self):
        return self.total("updated")

    @property
    def unchanged(self):
        return self.total("unchanged")

    @property
    def error_count(self):
        return sum(s.error_count for s in self.sheets)

    @property
    def warning_count(self):
        return sum(len(s.warnings) for s in self.sheets)

    @property
    def rows(self):
        return self.total("rows")


_BLANK = object()


class CellError(ValueError):
    pass


_TRUE = {"true", "yes", "y", "1", "on", "t"}
_FALSE = {"false", "no", "n", "0", "off", "f", ""}


def _is_blank(v):
    return v is None or (isinstance(v, str) and v.strip() == "")


def _to_text(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, datetime.datetime):
        return v.isoformat()
    if isinstance(v, (datetime.date, datetime.time)):
        return v.isoformat()
    return _unneutral(str(v))


def coerce(f, v):
    """Excel cell -> python value for field ``f`` (raises CellError)."""
    if _is_blank(v):
        if getattr(f, "choices", None) and f.has_default() and "" not in {c for c, _l in f.flatchoices}:
            return f.get_default()          # a blank choice cell means the default, never an invalid ""
        if isinstance(f, (CHARLIKE, models.FileField)) and not f.null:
            return ""
        if f.null:
            return None
        if f.has_default():
            return f.get_default()
        if isinstance(f, (CHARLIKE, models.FileField)):
            return ""
        raise CellError("is required")
    try:
        if isinstance(f, models.BooleanField):
            if isinstance(v, bool):
                return v
            s = str(v).strip().lower()
            if s in _TRUE:
                return True
            if s in _FALSE:
                return False
            raise CellError(f"'{v}' is not TRUE/FALSE")
        if isinstance(f, (models.IntegerField,)) and not isinstance(f, models.AutoField):
            value = int(float(v)) if not isinstance(v, int) else v
            return _choice(f, value)
        if isinstance(f, models.FloatField):
            return float(v)
        if isinstance(f, models.DecimalField):
            d = decimal.Decimal(_money_text(v))
            if f.decimal_places is not None:
                d = d.quantize(decimal.Decimal(1).scaleb(-f.decimal_places))
            return d
        if isinstance(f, models.DateTimeField):
            if isinstance(v, datetime.datetime):
                dt = v
            elif isinstance(v, datetime.date):
                dt = datetime.datetime.combine(v, datetime.time())
            else:
                s = str(v).strip()
                dt = dateparse.parse_datetime(s)
                if dt is None:
                    d = dateparse.parse_date(s)
                    if d is None:
                        raise CellError(f"'{v}' is not a date-time")
                    dt = datetime.datetime.combine(d, datetime.time())
            if timezone.is_naive(dt):
                dt = timezone.make_aware(dt)
            return dt
        if isinstance(f, models.DateField):
            if isinstance(v, datetime.datetime):
                return v.date()
            if isinstance(v, datetime.date):
                return v
            s = str(v).strip()
            d = dateparse.parse_date(s[:10]) if re.match(r"^\d{4}-\d\d-\d\d", s) else None
            if d is None:
                m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
                if m:
                    d = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            if d is None:
                raise CellError(f"'{v}' is not a date (use YYYY-MM-DD)")
            return d
        if isinstance(f, models.TimeField):
            if isinstance(v, datetime.datetime):
                return v.time()
            if isinstance(v, datetime.time):
                return v
            t = dateparse.parse_time(str(v).strip())
            if t is None:
                raise CellError(f"'{v}' is not a time (HH:MM)")
            return t
        if isinstance(f, models.JSONField):
            if isinstance(v, str):
                try:
                    return json.loads(_unneutral(v))
                except ValueError:
                    raise CellError("is not valid JSON")
            return v
        if isinstance(f, models.FileField):
            return _to_text(v)
        return _choice(f, _to_text(v))
    except CellError:
        raise
    except (ValueError, TypeError, decimal.InvalidOperation) as exc:
        raise CellError(f"'{v}' is not valid ({exc.__class__.__name__})")


def _money_text(v):
    """'NGN 1,250,000.50' / '₦250 000' -> '1250000.50' (plain numbers pass through)."""
    if isinstance(v, (int, float, decimal.Decimal)):
        return str(v)
    s = str(v).strip()
    s = re.sub(r"^(NGN|N|₦)\s*", "", s, flags=re.I)
    return re.sub(r"[\s,₦]", "", s)


def _choice(f, value):
    if not getattr(f, "choices", None):
        return value
    valid = [c for c, _l in f.flatchoices]
    if value in valid:
        return value
    s = str(value).strip().casefold()
    for c, label in f.flatchoices:
        if str(c).casefold() == s or str(label).casefold() == s:
            return c
    raise CellError(f"'{value}' is not one of {', '.join(str(c) for c in valid)[:200]}")


def _same(f, old, new):
    if isinstance(f, models.FileField):
        old = getattr(old, "name", old)
    if isinstance(f, (CHARLIKE, models.FileField)):
        return (old or "") == (new or "")
    return old == new


def _split_list(text):
    text = _to_text(text)
    parts = text.split("\n") if "\n" in text else text.split(";")
    return [p.strip() for p in parts if p.strip()]


@contextmanager
def _quiet_signals():
    """Signals that would invent data during a raw load (a staff record for each new
    user) are switched off; the workbook brings those rows itself."""
    from django.db.models.signals import post_save
    disconnected = []
    try:
        from accounts import signals as acc_signals
        from accounts.models import User
        if post_save.disconnect(acc_signals.create_staff_profile, sender=User):
            disconnected.append((acc_signals.create_staff_profile, User))
    except Exception:   # pragma: no cover - app layout changed
        pass
    try:
        yield
    finally:
        for receiver, sender in disconnected:
            post_save.connect(receiver, sender=sender)


def _read_workbook(fileobj):
    from openpyxl import load_workbook
    try:
        wb = load_workbook(fileobj, read_only=True, data_only=True)
    except Exception as exc:
        raise HubError(f"This is not a readable .xlsx workbook ({exc.__class__.__name__}).")
    titles = {ws.title: ws for ws in wb.worksheets}
    mapping = {}
    if META_SHEET in titles:
        rows = titles[META_SHEET].iter_rows(values_only=True)
        next(rows, None)
        for r in rows:
            if r and r[0] and len(r) > 1 and r[1]:
                mapping[str(r[0])] = str(r[1])
    longs = {}
    if LONG_SHEET in titles:
        rows = titles[LONG_SHEET].iter_rows(values_only=True)
        next(rows, None)
        parts = {}
        for r in rows:
            if r and r[0] is not None:
                parts.setdefault(int(r[0]), []).append((int(r[1]), _unneutral(str(r[2] or ""))))
        longs = {f"{LONG_TOKEN}{i}": "".join(t for _p, t in sorted(p)) for i, p in parts.items()}
    by_title = {sd.title.lower(): sd for sd in plan()}
    by_id = {sd.id: sd for sd in plan()}
    found, ignored = [], []
    for title, ws in titles.items():
        if title in (META_SHEET, LONG_SHEET, README_SHEET, INSTRUCTIONS_SHEET, LISTS_SHEET):
            continue
        sd = by_id.get(mapping.get(title, "")) or by_title.get(title.lower()) or by_id.get(title)
        if sd is None:
            ignored.append(title)
            continue
        found.append((sd, ws))
    index = {sd.id: i for i, sd in enumerate(plan())}
    found.sort(key=lambda p: index[p[0].id])
    return wb, found, ignored, longs


def _users_without_hash(ws):
    """True when the Users sheet has a row with no password hash (or no password column)."""
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None) or ()
    header = [str(h).strip() if h is not None else "" for h in header]
    idx = header.index(PASSWORD_COLUMN) if PASSWORD_COLUMN in header else None
    for row in rows:
        if row is None or all(_is_blank(c) for c in row):
            continue
        if idx is None or idx >= len(row) or _is_blank(row[idx]):
            return True
    return False


def default_password_problems(password):
    """Django's password validators on the "Default password for imported users"."""
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError
    try:
        validate_password(password)
    except ValidationError as exc:
        return list(exc.messages)
    return []


def import_workbook(fileobj, apply=False, categories=None, skip_errors=True, default_password=None,
                    default_password_hash=None, actor=None):
    """Preview (``apply=False``: everything is rolled back) or apply a workbook.

    ``categories`` limits the run to those categories' sheets. With
    ``skip_errors`` False an apply is rolled back if any row has an error.

    Users: a ``password`` column holding a Django hash is used as is;
    a new account without one gets ``default_password`` (or the ready-made
    ``default_password_hash``), which is then required. Every imported
    account ends up with ``must_change_password`` switched on.

    ``actor`` (the importing administrator) fills blank "who" links that a
    sheet requires (``Spec.actor_default``: who handed an asset over ...);
    without one the first active superuser is used.
    """
    report = Report()
    try:
        wb, found, ignored, longs = _read_workbook(fileobj)
    except HubError as exc:
        report.fatal = str(exc)
        return report
    report.ignored = ignored
    if categories and registry.EVERYTHING not in categories:
        found = [(sd, ws) for sd, ws in found if sd.category in categories]
    if not found:
        report.fatal = "No Data Hub sheets were found in this workbook. Export or download a template first."
        wb.close()
        return report
    report.needs_default_password = any(is_user_sheet(sd) and _users_without_hash(ws) for sd, ws in found)
    if default_password_hash is None and default_password:
        problems = default_password_problems(default_password)
        if problems:
            report.fatal = "Default password for imported users: " + " ".join(problems)
            wb.close()
            return report
        from django.contrib.auth.hashers import make_password
        default_password_hash = make_password(default_password)
    if report.needs_default_password and not default_password_hash:
        report.fatal = ("This file has user accounts without a password hash: enter a "
                        "'Default password for imported users'.")
        wb.close()
        return report
    try:
        with transaction.atomic():
            with _quiet_signals():
                run = _Run(report, longs, default_password_hash=default_password_hash, actor=actor)
                for sd, ws in found:
                    run.sheet(sd, ws)
                run.finish()
            rollback = (not apply) or (report.error_count and not skip_errors)
            if rollback:
                transaction.set_rollback(True)
            report.applied = not rollback
            report.rolled_back = bool(apply and rollback)
    except HubError as exc:
        report.fatal = str(exc)
    finally:
        wb.close()
    return report


class _Run:
    def __init__(self, report, longs, default_password_hash=None, actor=None):
        self.default_password_hash = default_password_hash
        self.actor = actor
        self._actor_pk = _BLANK
        self.touched = {}           # sheet id -> {pk, ...} of rows created / updated / unchanged
        self.report = report
        self.longs = longs
        self.keys = Keys()
        self.deferred = []          # (sd, sr, pk, field, raw, row_no)
        self.total = 0

    def _long(self, v):
        if isinstance(v, str) and v.startswith(LONG_TOKEN):
            return self.longs.get(v, v)
        return v

    def sheet(self, sd, ws):
        sr = SheetReport(id=sd.id, title=sd.title, label=sd.label, category=sd.category, links_only=sd.links_only)
        self.report.sheets.append(sr)
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            return
        header = [str(h).strip() if h is not None else "" for h in header]
        fmap = {f.name: f for f in sd.all_fields()}
        cols = []
        pw_col = None
        for i, h in enumerate(header):
            if not h:
                continue
            if h == PASSWORD_COLUMN and is_user_sheet(sd):
                pw_col = i
            elif h in fmap:
                cols.append((i, fmap[h]))
            else:
                sr.add_warning(1, f"Column '{h}' is not known and was ignored.")
        present = {f.name for _i, f in cols}
        required = () if sd.links_only else tuple(sd.spec.required)
        missing = [k for k in tuple(sd.key) + required if k not in present]
        if missing:
            sr.add_error(1, f"Key column(s) missing: {', '.join(missing)}. The sheet was skipped.")
            return
        seen = {}
        for n, row in enumerate(rows, start=2):
            if row is None or all(_is_blank(c) for c in row):
                continue
            self.total += 1
            if self.total > MAX_ROWS:
                raise HubError(f"The workbook has more than {MAX_ROWS} rows.")
            sr.rows += 1
            values = {f.name: self._long(row[i] if i < len(row) else None) for i, f in cols}
            pw_hash = row[pw_col] if pw_col is not None and pw_col < len(row) else None
            try:
                with transaction.atomic():
                    self.row(sd, sr, n, values, seen, pw_hash=pw_hash)
            except CellError as exc:
                sr.add_error(n, str(exc))
            except Exception as exc:     # integrity / validation problems of one row
                sr.add_error(n, f"{exc.__class__.__name__}: {str(exc)[:300]}")

    def actor_pk(self):
        """The importing administrator (else the first active superuser), for blank "who" links."""
        if self._actor_pk is _BLANK:
            pk = getattr(self.actor, "pk", None)
            if pk is None:
                from django.contrib.auth import get_user_model
                pk = (get_user_model()._base_manager.filter(is_superuser=True, is_active=True)
                      .order_by("pk").values_list("pk", flat=True).first())
            self._actor_pk = pk
        return self._actor_pk

    def _resolve(self, f, text, sr, n, sd=None):
        target = f.related_model
        pk = self.keys.lookup(target, _to_text(text))
        if pk is None and sd is not None and sd.spec.aliases and _label(target) in LINK_ALIASES:
            pk = LINK_ALIASES[_label(target)](_to_text(text).strip())
        if pk is None:
            raise LookupError(f"{f.name}: no {_label(target)} with key '{_to_text(text)}'")
        return pk

    @staticmethod
    def _clean_hash(raw):
        """A Django password hash from the cell, or None. Plaintext is refused."""
        if _is_blank(raw):
            return None
        from django.contrib.auth.hashers import UNUSABLE_PASSWORD_PREFIX, identify_hasher
        text = _to_text(raw).strip()
        if text.startswith(UNUSABLE_PASSWORD_PREFIX):
            return text
        try:
            identify_hasher(text)
        except ValueError:
            raise CellError("password: not a Django password hash (plaintext passwords are never imported)")
        return text

    def row(self, sd, sr, n, values, seen, pw_hash=None):
        model = sd.model
        parsed, later = {}, {}
        is_user = is_user_sheet(sd)
        pw_hash = self._clean_hash(pw_hash) if is_user else None
        for name, raw in values.items():
            f = model._meta.get_field(name)
            if f.many_to_many:
                items = [] if _is_blank(raw) else _split_list(raw)
                if name in sd.deferred:
                    later[name] = items
                    continue
                pks = []
                for it in items:
                    pk = self.keys.lookup(f.related_model, it)
                    if pk is None:
                        sr.add_warning(n, f"{name}: '{it}' not found, left out")
                    else:
                        pks.append(pk)
                parsed[name] = pks
            elif f.is_relation:
                if name in sd.deferred:
                    later[name] = None if _is_blank(raw) else _to_text(raw)
                    continue
                if _is_blank(raw):
                    if name in sd.spec.actor_default:
                        continue        # filled with the importing administrator below
                    if not f.null or name in sd.spec.required:
                        raise CellError(f"{name} is required on this sheet")
                    parsed[name] = None
                    continue
                try:
                    parsed[name] = self._resolve(f, raw, sr, n, sd)
                except LookupError as exc:
                    if name in sd.spec.required or name in sd.spec.strict:
                        raise CellError(_unknown_link_message(f, raw))
                    if not f.null or name in sd.key:
                        raise CellError(str(exc))
                    sr.add_warning(n, f"{exc}; left blank")
                    parsed[name] = None
            else:
                try:
                    parsed[name] = coerce(f, raw)
                except CellError as exc:
                    raise CellError(f"{name}: {exc}")
        if is_user and any(f.name == "must_change_password" for f in model._meta.concrete_fields):
            # Every imported account must set a new password on first sign-in
            # (also on re-import: an account that already has it on is unchanged).
            parsed["must_change_password"] = True

        if sd.spec.fill and not sd.links_only:
            ROW_FILLS[sd.spec.fill](self, sd, parsed, values)
        kfields = [model._meta.get_field(k) for k in sd.key]
        key = self.keys._compose(kfields, [parsed[k] for k in sd.key])
        nk = norm(key)
        if nk in seen:
            raise CellError(f"Duplicate key '{key}' (first seen on row {seen[nk]})")
        seen[nk] = n

        pk = self.keys.lookup(model, key)
        if pk is None and not sd.links_only:
            pk = self._alt_match(sd, parsed)
        if pk is None and sd.links_only:
            raise CellError(f"No {sd.label} with key '{key}': import its own sheet first")

        changed = []
        if pk is not None:
            obj = model._base_manager.get(pk=pk)
            created = False
        else:
            obj = model()
            created = True
        for name in sd.spec.actor_default:
            if name not in parsed and created:     # blank on an update: the stored value stays
                actor = self.actor_pk()
                if actor is None:
                    raise CellError(f"{name} is required (no administrator to default to)")
                parsed[name] = actor
        if sd.spec.check and not sd.links_only:
            ROW_CHECKS[sd.spec.check](parsed, None if created else obj)
        for name, value in parsed.items():
            f = model._meta.get_field(name)
            if f.many_to_many:
                continue
            old = getattr(obj, f.attname)
            if created or not _same(f, old, value):
                setattr(obj, f.attname, value)
                if not created:
                    changed.append(name)
        if is_user:
            if pw_hash is not None:
                if created or obj.password != pw_hash:
                    obj.password = pw_hash
                    if not created:
                        changed.append("password")
            elif created:
                if not self.default_password_hash:
                    raise CellError("No password hash and no default password for this new account")
                obj.password = self.default_password_hash
        if created or changed:
            now = timezone.now()
            for f in model._meta.concrete_fields:
                if getattr(f, "auto_now", False) or (created and getattr(f, "auto_now_add", False)):
                    setattr(obj, f.attname, now)
            obj.save_base(raw=True)
        for name, pks in parsed.items():
            f = model._meta.get_field(name)
            if not f.many_to_many:
                continue
            rel = getattr(obj, name)
            current = set(rel.values_list("pk", flat=True)) if not created else set()
            if set(pks) != current:
                rel.set(pks)
                if not created:
                    changed.append(name)
        self.keys.remember(sd, obj)
        self.touched.setdefault(sd.id, set()).add(obj.pk)
        if created:
            sr.created += 1
            status = "created"
        elif changed:
            sr.updated += 1
            status = "updated"
            for c in changed:
                sr.changed_fields[c] = sr.changed_fields.get(c, 0) + 1
        else:
            sr.unchanged += 1
            status = "unchanged"
        for name, raw in later.items():
            self.deferred.append([sd, sr, obj.pk, name, raw, n, status])

    def _alt_match(self, sd, parsed):
        model = sd.model
        for f in model._meta.concrete_fields:
            if not f.unique or f.primary_key or f.name in sd.key or f.name not in parsed:
                continue
            value = parsed[f.name]
            if value in (None, ""):
                continue
            hit = model._base_manager.filter(**{f.attname: value}).values_list("pk", flat=True).first()
            if hit is not None:
                return hit
        return None

    def finish(self):
        """Second pass: links to rows of the same category (parents, next rank ...),
        then the ``Spec.after`` hooks (once each)."""
        self._links_pass()
        done = set()
        for sd in plan():
            hook = sd.spec.after
            if hook and hook not in done and not sd.links_only and sd.id in self.touched:
                done.add(hook)
                AFTER_RUN[hook](self)

    def _links_pass(self):
        for item in self.deferred:
            sd, sr, pk, name, raw, n, status = item
            model = sd.model
            f = model._meta.get_field(name)
            try:
                with transaction.atomic():
                    if f.many_to_many:
                        pks = []
                        for it in raw:
                            tpk = self.keys.lookup(f.related_model, it)
                            if tpk is None:
                                sr.add_warning(n, f"{name}: '{it}' not found, left out")
                            else:
                                pks.append(tpk)
                        obj = model._base_manager.get(pk=pk)
                        rel = getattr(obj, name)
                        if set(rel.values_list("pk", flat=True)) != set(pks):
                            rel.set(pks)
                            self._bump(sr, item, name)
                        continue
                    tpk = None
                    if raw is not None:
                        tpk = self.keys.lookup(f.related_model, raw)
                        if tpk is None:
                            sr.add_warning(n, f"{name}: no {_label(f.related_model)} with key '{raw}'; left blank")
                    current = model._base_manager.filter(pk=pk).values_list(f.attname, flat=True).first()
                    if current != tpk:
                        model._base_manager.filter(pk=pk).update(**{f.attname: tpk})
                        self._bump(sr, item, name)
            except Exception as exc:
                sr.add_error(n, f"{name}: {exc.__class__.__name__}: {str(exc)[:300]}")

    @staticmethod
    def _bump(sr, item, name):
        if item[6] == "unchanged":
            sr.unchanged -= 1
            sr.updated += 1
            item[6] = "updated"
        if item[6] == "updated":
            sr.changed_fields[name] = sr.changed_fields.get(name, 0) + 1


# ---------------------------------------------------------------------------
# Per-sheet hooks of the Assets category (core/datahub/asset_rules.py)
# ---------------------------------------------------------------------------
# ROW_FILLS[name](run, sd, parsed): fill blank cells (generated keys) before the row is matched.
# AFTER_RUN[name](run): once, after every sheet is loaded (run.touched holds the saved pks).
# LINK_ALIASES[label](text) -> pk or None: other spellings a link accepts (Spec.aliases).
from . import asset_rules as _asset_rules  # noqa: E402

ROW_FILLS = dict(_asset_rules.FILLS)
AFTER_RUN = dict(_asset_rules.AFTER)
LINK_ALIASES = dict(_asset_rules.ALIASES)
ROW_CHECKS.update(_asset_rules.CHECKS)
