"""Import diff engine (v2.1 X45, S-15, S-16).

Every import is an upsert with a preview: New / Changed (field old -> new) /
Unchanged / Missing from file / Errors. Nothing is deleted, nothing is
duplicated, and the person ticks exactly which changes to apply.

    class StaffDiff(DiffSpec):          # see staff/diffspecs.py
        name = "staff"
        key_fields = ("employee_no",)
        alt_keys = (("email",),)
        compare_fields = ("first_name", "phone", ...)

    run = preview(spec, upload, user=request.user)        # ImportRun + ImportRow rows
    apply(run, user=request.user)                          # optimistic, per-row

Rules
* Natural key first, then each alternative key in order.
* A duplicate key inside the file is an error row (the first one wins).
* Blank cells mean "no change" (``qa.import.blank_means_no_change``, default on).
* Rows "missing from the file" are listed and NEVER deleted; a spec that allows
  it may offer "mark inactive" (second click), never delete.
* Optimistic lock: a record edited after the preview is reported as a conflict
  and not overwritten.
* Limits: ``qa.import.max_rows`` (5000), ``qa.import.max_file_mb`` (10),
  ``qa.import.preview_ttl_hours`` (24); workbooks are opened read-only.
* Round trip: ``export_workbook(spec)`` writes the spec's columns plus a hidden
  ``__key`` column and a hidden ``__meta`` sheet (spec name, version, header
  signature); formula-like cells are neutralised (core/excel.py, S-16).
"""
import datetime
import decimal
import hashlib
import io
from collections import Counter

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from config import registry as config

from . import audit, excel
from .importexport import ImportSpec

KEY_COLUMN = "__key"
META_SHEET = "__meta"

DIFF_REGISTRY = {}


class ImportRejected(ValidationError):
    """The whole file is refused (wrong template, too big, too many rows)."""


def register_diff(spec_cls):
    if not spec_cls.name:
        raise ValueError(f"{spec_cls.__name__} must declare a name.")
    DIFF_REGISTRY[spec_cls.name] = spec_cls
    return spec_cls


def get_spec(name):
    cls = DIFF_REGISTRY.get(name)
    return cls() if cls else None


def _setting_int(key, default):
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if hasattr(value, "pk"):
        return str(value)
    return str(value)


class DiffSpec(ImportSpec):
    """An ImportSpec that can preview its changes. Subclasses declare keys and
    compare fields and implement queryset/apply_row (current/display have
    sensible defaults)."""

    version = 1
    label = ""
    permission = ""                      # needed to upload / see / apply
    key_fields: tuple = ()
    alt_keys: tuple = ()
    compare_fields: tuple = ()
    allow_deactivate_missing = False
    requires_confirmation = None         # ActionPolicy code (X47) - later phases
    match_note = ""                      # "Matched by staff number, then e-mail"

    # ---- to implement ---------------------------------------------------
    def queryset(self):
        raise NotImplementedError

    def apply_row(self, obj, clean, fields, actor):
        """Create (obj None) or update ``fields`` of obj from ``clean``."""
        raise NotImplementedError

    def deactivate(self, obj, actor):
        raise NotImplementedError

    # ---- overridable defaults ------------------------------------------
    def can_use(self, user):
        return bool(user and user.is_authenticated and (not self.permission or user.has_perm(self.permission)))

    def normalise_key_value(self, field, value):
        if value is None:
            return ""
        if hasattr(value, "pk"):
            return str(value.pk)
        return " ".join(str(value).split()).casefold()

    def key_for(self, clean, fields=None):
        fields = fields or self.key_fields
        parts = [self.normalise_key_value(f, clean.get(f)) for f in fields]
        return "|".join(parts) if all(parts) else ""

    def object_key(self, obj):
        return "|".join(self.normalise_key_value(f, getattr(obj, f, "")) for f in self.key_fields)

    def lookup(self, clean):
        qs = self.queryset()
        for fields in (self.key_fields, *self.alt_keys):
            if not all(clean.get(f) not in (None, "") for f in fields):
                continue
            q = {}
            for f in fields:
                v = clean.get(f)
                q[f"{f}__iexact" if isinstance(v, str) else f] = v
            found = list(qs.filter(**q)[:2])
            if len(found) == 1:
                return found[0], fields
        return None, None

    def current(self, obj, field):
        return getattr(obj, field, None)

    def normalise(self, field, value):
        if value is None or value == "":
            return ""
        if hasattr(value, "pk"):
            return f"#{value.pk}"
        if isinstance(value, datetime.datetime):
            return value.date().isoformat()
        if isinstance(value, datetime.date):
            return value.isoformat()
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float, decimal.Decimal)):
            return str(int(value)) if float(value).is_integer() else str(value)
        return " ".join(str(value).split()).casefold()

    def display(self, field, value):
        if value is None or value == "":
            return ""
        if isinstance(value, (datetime.date, datetime.datetime)):
            return value.strftime("%d %b %Y")
        return str(value)

    def field_label(self, field):
        for c in self.columns:
            if c.field == field:
                return c.name
        return field.replace("_", " ").capitalize()

    def row_label(self, clean, obj=None):
        return str(obj) if obj is not None else " ".join(str(clean.get(f) or "") for f in self.key_fields)

    def row_sublabel(self, clean, obj=None):
        return ""

    def version_of(self, obj):
        stamp = getattr(obj, "updated_at", None)
        if stamp is not None:
            return stamp.isoformat()
        values = "|".join(self.normalise(f, self.current(obj, f)) for f in self.compare_fields)
        return hashlib.sha256(values.encode()).hexdigest()

    def field_warnings(self, field, old, new, obj, clean):
        """{field: message} hints shown under a change; the change starts unticked."""
        return None

    # ---- export (round trip) ---------------------------------------------
    def export_queryset(self):
        return self.queryset()

    def export_value(self, obj, column):
        value = self.current(obj, column.field)
        if hasattr(value, "pk"):
            return str(value)
        return value

    def header(self):
        return [c.name for c in self.columns]


def header_signature(names):
    clean = sorted(str(n).strip().casefold() for n in names if n and str(n) != KEY_COLUMN)
    return hashlib.sha256("|".join(clean).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------
def _read(spec, upload):
    max_mb = _setting_int("qa.import.max_file_mb", 10)
    size = getattr(upload, "size", None)
    if size is not None and size > max_mb * 1024 * 1024:
        raise ImportRejected(f"The file is larger than {max_mb} MB.")
    data = upload.read()
    sha = hashlib.sha256(data).hexdigest()
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ImportRejected("That is not an Excel workbook (.xlsx) we can read.")
    try:
        if META_SHEET in wb.sheetnames:
            meta = {str(r[0]): r[1] for r in wb[META_SHEET].iter_rows(values_only=True) if r and r[0]}
            if meta.get("spec") and meta.get("spec") != spec.name:
                raise ImportRejected(f"This file was exported for '{meta.get('spec')}', not '{spec.name}'.")
        ws = wb.worksheets[0]
        rows_iter = ws.iter_rows(values_only=True)
        try:
            header = [excel._clean(h) for h in next(rows_iter)]
        except StopIteration:
            raise ImportRejected("The file is empty.")
        expected = {c.name for c in spec.columns} | {c.field for c in spec.columns} | {KEY_COLUMN}
        unknown = [h for h in header if h and h not in expected]
        if unknown:
            raise ImportRejected("Unknown column(s): " + ", ".join(map(str, unknown))
                                 + ". Download the template or an export and use its columns.")
        max_rows = getattr(spec, "max_rows", None) or _setting_int("qa.import.max_rows", 5000)
        rows = []
        for n, row in enumerate(rows_iter, start=2):
            if all(excel._clean(c) in (None, "") for c in row):
                continue
            if len(rows) >= max_rows:
                raise ImportRejected(f"The file has more than {max_rows} rows. Split it and import each part.")
            rows.append((n, {header[i]: excel.unneutralise(row[i]) for i in range(min(len(header), len(row)))
                             if header[i] and header[i] != KEY_COLUMN}))
    finally:
        wb.close()
    return rows, header, data, sha


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------
def preview(spec, upload, *, user, options=None):
    from django.core.files.base import ContentFile

    from .models import ImportRow, ImportRun

    rows, header, data, sha = _read(spec, upload)
    ttl = _setting_int("qa.import.preview_ttl_hours", 24)
    blank_no_change = bool(config.get("qa.import.blank_means_no_change", True))
    run = ImportRun(spec=spec.name, spec_version=spec.version, uploaded_by=user, original_name=(
        getattr(upload, "name", "") or "")[:255], file_sha256=sha, header_signature=header_signature(header),
        options=options or {}, expires_at=timezone.now() + datetime.timedelta(hours=ttl))
    run.file.save("upload.xlsx", ContentFile(data), save=False)
    run.save()

    out, seen, seen_targets = [], set(), set()
    for number, raw in rows:
        raw_json = {k: _jsonable(v) for k, v in raw.items()}
        try:
            clean = spec.clean_row(raw)
            spec.validate_row(clean)
        except ValidationError as exc:
            out.append(ImportRow(run=run, row_number=number, action=ImportRow.ERROR, raw=raw_json,
                                 error="; ".join(exc.messages)))
            continue
        key = spec.key_for(clean)
        if not key:
            out.append(ImportRow(run=run, row_number=number, action=ImportRow.ERROR, raw=raw_json,
                                 error="The key column is empty."))
            continue
        if key in seen:
            out.append(ImportRow(run=run, row_number=number, key=key[:200], action=ImportRow.ERROR, raw=raw_json,
                                 error="Duplicate key in file: this record appears more than once."))
            continue
        seen.add(key)
        obj, matched_by = spec.lookup(clean)
        clean_json = {f: spec.display(f, clean.get(f)) for f in clean}
        if obj is None:
            out.append(ImportRow(run=run, row_number=number, key=key[:200], action=ImportRow.NEW, raw=raw_json,
                                 clean=clean_json, accepted=True, label=spec.row_label(clean)[:200],
                                 sublabel=spec.row_sublabel(clean)[:255]))
            continue
        if obj.pk in seen_targets:
            out.append(ImportRow(run=run, row_number=number, key=key[:200], action=ImportRow.ERROR, raw=raw_json,
                                 error=f"Matches the same record as an earlier row ({obj})."))
            continue
        seen_targets.add(obj.pk)
        fields = list(spec.compare_fields)
        if matched_by and tuple(matched_by) != tuple(spec.key_fields):
            fields = list(spec.key_fields) + [f for f in fields if f not in spec.key_fields]
        diff, warnings = {}, {}
        for f in fields:
            if f not in clean:
                continue
            new = clean.get(f)
            if new in (None, "") and blank_no_change:
                continue
            old = spec.current(obj, f)
            if spec.normalise(f, old) == spec.normalise(f, new):
                continue
            diff[f] = {"old": spec.display(f, old), "new": spec.display(f, new), "label": spec.field_label(f)}
            hint = spec.field_warnings(f, old, new, obj, clean)
            if hint:
                warnings[f] = hint
        ct = ContentType.objects.get_for_model(obj.__class__)
        out.append(ImportRow(
            run=run, row_number=number, key=key[:200], action=ImportRow.CHANGED if diff else ImportRow.UNCHANGED,
            target_ct=ct, target_id=str(obj.pk), target_version=spec.version_of(obj)[:64], raw=raw_json,
            clean=clean_json, diff=diff, warnings=warnings, accepted=bool(diff),
            accepted_fields=[f for f in diff if f not in warnings] if diff else None,
            label=spec.row_label(clean, obj)[:200], sublabel=spec.row_sublabel(clean, obj)[:255]))
    # Missing from file: never deleted.
    universe = spec.queryset()
    model = universe.model
    ct = ContentType.objects.get_for_model(model)
    for obj in universe.iterator():
        if obj.pk in seen_targets:
            continue
        okey = spec.object_key(obj)
        if okey and okey in seen:
            continue
        out.append(ImportRow(run=run, action=ImportRow.MISSING, key=okey[:200], target_ct=ct, target_id=str(obj.pk),
                             target_version=spec.version_of(obj)[:64], accepted=False,
                             label=spec.row_label({}, obj)[:200], sublabel=spec.row_sublabel({}, obj)[:255]))
    ImportRow.objects.bulk_create(out, batch_size=500)
    run.counts = dict(Counter(r.action for r in out))
    run.status = ImportRun.PREVIEWED
    run.save(update_fields=["counts", "status"])
    return run


def summary(run):
    counts = run.counts or {}
    rows = run.rows.filter(action__in=("new", "changed"), accepted=True)
    changes = 0
    new = 0
    for r in rows.only("action", "accepted_fields", "diff"):
        if r.action == "new":
            new += 1
        else:
            changes += len(r.accepted_fields if r.accepted_fields is not None else r.diff)
    total_changes = sum(len(r.diff or {}) for r in run.rows.filter(action="changed").only("diff"))
    return {"new": counts.get("new", 0), "changed": counts.get("changed", 0), "unchanged": counts.get("unchanged", 0),
            "missing": counts.get("missing", 0), "error": counts.get("error", 0), "conflict": counts.get("conflict", 0),
            "selected_new": new, "selected_changes": changes, "total_changes": total_changes}


# ---------------------------------------------------------------------------
# Selection (checkboxes)
# ---------------------------------------------------------------------------
def set_row(run, row_id, accepted):
    row = run.rows.filter(pk=row_id, action__in=("new", "changed")).first()
    if row is None:
        return None
    row.accepted = bool(accepted)
    if row.action == "changed":
        row.accepted_fields = list(row.diff) if accepted else []
    row.save(update_fields=["accepted", "accepted_fields"])
    return row


def set_field(run, row_id, field, accepted):
    row = run.rows.filter(pk=row_id, action="changed").first()
    if row is None or field not in (row.diff or {}):
        return None
    fields = list(row.accepted_fields if row.accepted_fields is not None else row.diff)
    if accepted and field not in fields:
        fields.append(field)
    if not accepted and field in fields:
        fields.remove(field)
    row.accepted_fields = [f for f in row.diff if f in fields]
    row.accepted = bool(row.accepted_fields)
    row.save(update_fields=["accepted", "accepted_fields"])
    return row


def set_all(run, action, accepted):
    for row in run.rows.filter(action=action):
        set_row(run, row.pk, accepted)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
def is_open(run):
    from .models import ImportRun

    if run.status != ImportRun.PREVIEWED:
        return False
    if run.expires_at and run.expires_at < timezone.now():
        run.status = ImportRun.EXPIRED
        run.save(update_fields=["status"])
        return False
    return True


def apply(run, *, user, spec=None):
    from .models import ImportRow, ImportRun

    spec = spec or get_spec(run.spec)
    if spec is None:
        raise ValidationError("This kind of import is no longer available.")
    if not is_open(run):
        raise ValidationError("This preview can no longer be applied (already applied, discarded or expired). "
                              "Upload the file again.")
    if spec.requires_confirmation:
        raise ValidationError("This import needs a second officer; confirmation arrives with X47 (Phase F).")
    applied = conflicts = failed = 0
    with transaction.atomic():
        run = ImportRun.objects.select_for_update().get(pk=run.pk)
        run.status = ImportRun.APPLYING
        run.save(update_fields=["status"])
        for row in run.rows.filter(action__in=(ImportRow.NEW, ImportRow.CHANGED), accepted=True).order_by(
                "row_number"):
            target = row.target if row.action == ImportRow.CHANGED else None
            if row.action == ImportRow.CHANGED:
                if target is None or spec.version_of(target)[:64] != row.target_version:
                    row.action, row.apply_error = ImportRow.CONFLICT, "Changed by someone else since the preview."
                    row.save(update_fields=["action", "apply_error"])
                    conflicts += 1
                    continue
            fields = list(row.accepted_fields if row.accepted_fields is not None else row.diff)
            try:
                with transaction.atomic():
                    clean = spec.clean_row(row.raw)
                    spec.validate_row(clean)
                    spec.apply_row(target, clean, fields, user)
            except ValidationError as exc:
                row.apply_error = "; ".join(exc.messages)
                row.save(update_fields=["apply_error"])
                failed += 1
                continue
            except Exception as exc:  # one bad row must not stop the others
                row.apply_error = f"Could not save: {exc}"
                row.save(update_fields=["apply_error"])
                failed += 1
                continue
            row.applied = True
            row.save(update_fields=["applied"])
            applied += 1
        if run.options.get("deactivate_missing") and spec.allow_deactivate_missing:
            for row in run.rows.filter(action=ImportRow.MISSING, accepted=True):
                target = row.target
                if target is not None:
                    spec.deactivate(target, user)
                    row.applied = True
                    row.save(update_fields=["applied"])
        counts = Counter(run.rows.values_list("action", flat=True))
        run.counts = dict(counts)
        run.status = ImportRun.APPLIED if not (conflicts or failed) else ImportRun.PARTIAL
        run.applied_at, run.applied_by = timezone.now(), user
        run.save(update_fields=["counts", "status", "applied_at", "applied_by"])
        audit.log("other", run, actor=user, changes={"applied": applied, "conflicts": conflicts, "failed": failed},
                  note=f"import applied: {spec.name}")
    return {"applied": applied, "conflicts": conflicts, "failed": failed}


def discard(run, *, user):
    from .models import ImportRun

    if run.status in (ImportRun.PREVIEWED, ImportRun.PARSED):
        run.status = ImportRun.DISCARDED
        run.save(update_fields=["status"])


# ---------------------------------------------------------------------------
# Export (round trip) and the errors workbook
# ---------------------------------------------------------------------------
def export_workbook(spec):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = (spec.label or spec.name)[:31]
    header = spec.header() + [KEY_COLUMN]
    for i, name in enumerate(header, start=1):
        cell = ws.cell(row=1, column=i, value=name)
        cell.font, cell.fill = excel._HEADER_FONT, excel._HEADER_FILL
        ws.column_dimensions[excel.get_column_letter(i)].width = max(len(str(name)) + 4, 16)
    ws.column_dimensions[excel.get_column_letter(len(header))].hidden = True
    for r, obj in enumerate(spec.export_queryset().iterator(), start=2):
        for c, col in enumerate(spec.columns, start=1):
            ws.cell(row=r, column=c, value=excel.neutralise(excel.to_cell(spec.export_value(obj, col))))
        ws.cell(row=r, column=len(header), value=spec.object_key(obj))
    meta = wb.create_sheet(META_SHEET)
    for k, v in (("spec", spec.name), ("version", spec.version), ("header_signature", header_signature(header)),
                 ("exported_at", timezone.now().isoformat())):
        meta.append([k, v])
    meta.sheet_state = "hidden"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def errors_workbook(run, spec):
    rows = run.rows.filter(action__in=("error", "conflict")).order_by("row_number")
    header = ["Row"] + [c.name for c in spec.columns] + ["Errors"]
    data = [[r.row_number] + [r.raw.get(c.name, r.raw.get(c.field, "")) for c in spec.columns]
            + [r.error or r.apply_error] for r in rows]
    return excel.write_rejected(header, data, sheet_title="Rows to fix")
