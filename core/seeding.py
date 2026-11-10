"""`seed_nbte` engine (v2.1 X51; docs/master_plan_v2_1/specs/seed_command_spec.md).

Apps register data sets in ``<app>/seed_sets.py`` (autodiscovered by
CoreConfig.ready):

    from core.seeding import seed_set, upsert, load_json

    @seed_set("geo.states", group="geo", files=("geo/states.json",), order=40)
    def seed_states(ctx):
        data = load_json(ctx, "geo/states.json")
        for row in data["rows"]:
            upsert(StateOfOrigin, {"code": row["code"]}, {"name": row["name"], "zone": row["zone"]}, ctx=ctx)

Contract: idempotent (natural keys), fill-only (an edited value is never
overwritten), transparent (per-set summary), data in files under
core/seed_data/. Each set runs in its own savepoint; ``--dry-run`` wraps
everything in one transaction that is rolled back.
"""
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

DATA_DIR = Path(settings.BASE_DIR) / "core" / "seed_data"

# Group aliases (seed spec §5): base 1-3,33; geo 4-6; org 7-11; hr 12-13; qa ...; finance 25; demo 34
GROUP_ORDER = ["base", "geo", "org", "hr", "qa", "samples", "finance", "visits", "demo"]


@dataclass
class Result:
    created: int = 0
    filled: int = 0
    unchanged: int = 0
    updated_unedited: int = 0
    kept_edited: int = 0
    skipped: int = 0
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    status: str = "ok"           # ok | failed | skipped | dependency-failed
    error: str = ""
    as_dependency: bool = False

    @property
    def changes(self):
        return self.created + self.filled + self.updated_unedited

    def as_dict(self):
        return {"created": self.created, "filled": self.filled, "unchanged": self.unchanged,
                "updated_unedited": self.updated_unedited, "kept_edited": self.kept_edited,
                "skipped": self.skipped, "warnings": list(self.warnings), "notes": list(self.notes),
                "status": self.status, "error": self.error, "as_dependency": self.as_dependency}


@dataclass
class SeedSet:
    name: str
    fn: object
    depends: tuple
    group: tuple
    files: tuple
    version: int
    phase: str
    order: int
    demo_only: bool = False


@dataclass
class Context:
    set_name: str = ""
    version: int = 1
    dry_run: bool = False
    update_unedited: bool = False
    data_dir: Path = DATA_DIR
    with_demo: bool = False
    verbosity: int = 1
    res: Result = None
    stdout: object = None
    options: dict = field(default_factory=dict)

    def note(self, text):
        self.res.notes.append(text)

    def warn(self, text):
        self.res.warnings.append(text)


REGISTRY = {}


def seed_set(name, *, depends=(), group=(), files=(), version=1, phase="B", order=500, demo_only=False):
    groups = (group,) if isinstance(group, str) else tuple(group)

    def deco(fn):
        REGISTRY[name] = SeedSet(name, fn, tuple(depends), groups, tuple(files), version, phase, order, demo_only)
        return fn
    return deco


# ---------------------------------------------------------------------------
# Data files
# ---------------------------------------------------------------------------
HEADER_KEYS = ("set", "version", "source", "confirmed")


def load_json(ctx, relpath):
    path = Path(ctx.data_dir) / relpath
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _check_type(value, expected):
    types = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict, "number": (int, float),
             "null": type(None)}
    if isinstance(expected, list):
        return any(_check_type(value, e) for e in expected)
    if expected == "integer" and isinstance(value, bool):
        return False
    return isinstance(value, types.get(expected, object))


def validate(data, schema, where=""):
    """Small JSON-schema subset (type, required, properties, items, enum, minItems)."""
    errors = []
    t = schema.get("type")
    if t and not _check_type(data, t):
        return [f"{where or '$'}: expected {t}"]
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{where or '$'}: {data!r} not in {schema['enum']}")
    if isinstance(data, dict):
        for key in schema.get("required", []):
            if key not in data:
                errors.append(f"{where or '$'}: missing '{key}'")
        for key, sub in (schema.get("properties") or {}).items():
            if key in data:
                errors += validate(data[key], sub, f"{where}.{key}" if where else key)
    if isinstance(data, list):
        if len(data) < schema.get("minItems", 0):
            errors.append(f"{where or '$'}: fewer than {schema['minItems']} items")
        if "items" in schema:
            for i, item in enumerate(data):
                errors += validate(item, schema["items"], f"{where}[{i}]")
                if len(errors) > 20:
                    break
    return errors


def validate_files(data_dir=None):
    """{relpath: [errors]} for every data file with a *.schema.json beside it,
    plus header checks for every JSON data file."""
    base = Path(data_dir or DATA_DIR)
    problems = {}
    for path in sorted(base.rglob("*.json")):
        if path.name.endswith(".schema.json"):
            continue
        rel = str(path.relative_to(base))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems[rel] = [f"not valid JSON: {exc}"]
            continue
        errs = [f"header: missing '{k}'" for k in HEADER_KEYS if k not in data]
        schema_path = path.with_name(path.stem + ".schema.json")
        if not schema_path.exists():
            generic = path.parent / ("_" + re_sub_year(path.stem) + ".schema.json")
            schema_path = generic if generic.exists() else None
        if schema_path:
            errs += validate(data, json.loads(schema_path.read_text(encoding="utf-8")))
        if errs:
            problems[rel] = errs
    return problems


def re_sub_year(stem):
    import re
    return re.sub(r"_\d{4}$", "", stem)


# ---------------------------------------------------------------------------
# Upsert with provenance
# ---------------------------------------------------------------------------
def _hash(value):
    if hasattr(value, "pk"):
        value = value.pk
    if isinstance(value, (list, tuple, dict)):
        value = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(str(value).encode()).hexdigest()[:32]


def _empty(value):
    if isinstance(value, bool):
        return False                      # False is a real choice (e.g. deactivated) - never "fill"
    return value in (None, "", 0, [], {})


def _record(obj):
    from .models import SeedRecord

    ct = ContentType.objects.get_for_model(obj.__class__)
    return SeedRecord.objects.filter(content_type=ct, object_id=str(obj.pk)).first()


def _track(ctx, obj, natural_key, values):
    from .models import SeedRecord

    ct = ContentType.objects.get_for_model(obj.__class__)
    SeedRecord.objects.update_or_create(
        content_type=ct, object_id=str(obj.pk),
        defaults={"set_name": ctx.set_name, "natural_key": json.loads(json.dumps(natural_key, default=str)),
                  "field_hashes": {f: _hash(v) for f, v in values.items()}, "seed_version": ctx.version})


def upsert(model, natural_key, values, *, ctx, create_only=False, manager=None):
    """Create the row if missing; otherwise fill empty fields only (and, with
    --update-unedited, refresh fields still equal to what the seed wrote).
    Returns the object."""
    mgr = manager or model._base_manager
    obj = mgr.filter(**natural_key).first()
    if obj is None:
        obj = model(**natural_key, **values)
        obj.save()
        _track(ctx, obj, natural_key, values)
        ctx.res.created += 1
        return obj
    if create_only:
        ctx.res.unchanged += 1
        return obj
    rec = _record(obj)
    filled, refreshed, kept = [], [], 0
    for f, v in values.items():
        cur = getattr(obj, f)
        if _empty(cur) and not _empty(v):
            setattr(obj, f, v)
            filled.append(f)
        elif ctx.update_unedited and rec and f in (rec.field_hashes or {}) and _hash(cur) != _hash(v):
            if rec.field_hashes[f] == _hash(cur):
                setattr(obj, f, v)
                refreshed.append(f)
            else:
                kept += 1
        elif rec and f in (rec.field_hashes or {}) and rec.field_hashes[f] != _hash(cur) and _hash(cur) != _hash(v):
            kept += 1
    if filled or refreshed:
        names = filled + refreshed
        if any(fld.name == "updated_at" for fld in obj._meta.concrete_fields):
            names.append("updated_at")
        obj.save(update_fields=names)
        if rec:
            hashes = dict(rec.field_hashes or {})
            hashes.update({f: _hash(getattr(obj, f)) for f in filled + refreshed})
            rec.field_hashes = hashes
            rec.save(update_fields=["field_hashes", "updated_at"])
        if refreshed and not filled:
            ctx.res.updated_unedited += 1
        else:
            ctx.res.filled += 1
    else:
        ctx.res.unchanged += 1
    if kept:
        ctx.res.kept_edited += 1
    return obj


def add_m2m(obj, relation, items, *, ctx):
    """Add missing M2M links (never removes). Returns how many were added."""
    manager = getattr(obj, relation)
    have = set(manager.values_list("pk", flat=True))
    new = [i for i in items if i is not None and i.pk not in have]
    if new:
        manager.add(*new)
    return len(new)


# ---------------------------------------------------------------------------
# Planning and running
# ---------------------------------------------------------------------------
def _ordered():
    return sorted(REGISTRY.values(), key=lambda s: (s.order, s.name))


def expand(names):
    """Set names or group aliases -> set names (unknown names raise)."""
    out = []
    for raw in names:
        name = raw.strip()
        if not name:
            continue
        if name in REGISTRY:
            out.append(name)
            continue
        group = [s.name for s in _ordered() if name in s.group]
        if not group:
            raise KeyError(name)
        out += group
    return list(dict.fromkeys(out))


def plan(*, only=None, skip=None, no_deps=False, with_demo=False):
    """[(SeedSet, reason)] in run order; reason "" | "dependency"."""
    skipped = set(expand(skip or []))
    if only:
        wanted = set(expand(only))
    else:
        wanted = {s.name for s in REGISTRY.values() if (with_demo or not s.demo_only)}
    reasons = {n: "" for n in wanted}
    if not no_deps:
        stack = list(wanted)
        while stack:
            n = stack.pop()
            for d in REGISTRY[n].depends:
                if d in REGISTRY and d not in reasons:
                    reasons[d] = "dependency"
                    stack.append(d)
    # skipped sets and their dependants
    blocked = set(skipped)
    changed = True
    while changed:
        changed = False
        for n in list(reasons):
            if n not in blocked and any(d in blocked for d in REGISTRY[n].depends):
                blocked.add(n)
                changed = True
    order, seen = [], set()

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        for d in sorted(REGISTRY[n].depends, key=lambda x: (REGISTRY[x].order if x in REGISTRY else 0, x)):
            if d in reasons:
                visit(d)
        order.append(n)
    for s in _ordered():
        if s.name in reasons:
            visit(s.name)
    return [(REGISTRY[n], "skipped" if n in blocked else reasons[n]) for n in order]


def run(*, only=None, skip=None, no_deps=False, dry_run=False, update_unedited=False, with_demo=False,
        data_dir=None, verbosity=1, stdout=None, options=None):
    """Run the planned sets; returns [(name, Result)]."""
    steps = plan(only=only, skip=skip, no_deps=no_deps, with_demo=with_demo)
    results = []
    failed = set()

    def _go():
        for s, reason in steps:
            res = Result(as_dependency=(reason == "dependency"))
            if reason == "skipped":
                res.status = "skipped"
                res.notes.append("skipped (--skip or a skipped dependency)")
                results.append((s.name, res))
                continue
            if any(d in failed for d in s.depends):
                res.status = "dependency-failed"
                res.warnings.append("not run: a set it depends on failed")
                failed.add(s.name)
                results.append((s.name, res))
                continue
            ctx = Context(set_name=s.name, version=s.version, dry_run=dry_run, update_unedited=update_unedited,
                          data_dir=Path(data_dir) if data_dir else DATA_DIR, with_demo=with_demo,
                          verbosity=verbosity, res=res, stdout=stdout, options=dict(options or {}))
            if no_deps:
                missing = [d for d in s.depends if d in REGISTRY and d not in dict((x.name, 1) for x, _r in steps)]
                if missing:
                    res.warnings.append("dependencies not run (--no-deps): " + ", ".join(missing))
            try:
                with transaction.atomic():
                    s.fn(ctx)
            except Exception as exc:  # one failing set rolls back alone
                fresh = Result(as_dependency=res.as_dependency, status="failed", error=f"{type(exc).__name__}: {exc}")
                failed.add(s.name)
                results.append((s.name, fresh))
                continue
            results.append((s.name, res))

    if dry_run:
        with transaction.atomic():
            _go()
            transaction.set_rollback(True)
    else:
        _go()
    return results


def summary_rows(results):
    return [(name, r) for name, r in results]


def totals(results):
    t = Result()
    for _n, r in results:
        for f in ("created", "filled", "unchanged", "updated_unedited", "kept_edited", "skipped"):
            setattr(t, f, getattr(t, f) + getattr(r, f))
        t.warnings += r.warnings
    return t
