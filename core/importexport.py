"""Reusable import/export (doc 33 cross-cutting #1).

Owns:
- `Column`, `RowError`, `ImportResult`, `ImportSpec`, `ExportSpec`, `REGISTRY`
- Excel bridge (`run_from_file`, `template_xlsx`, `rejected_xlsx`, `export_xlsx`)
- `all_specs()` — the wizard's module list, merging import + export by name.

Lookup columns:
  A Column can declare a `lookup` callable that returns the list of valid
  values (typically queried from the DB). This drives three things:
    1. The Excel template gets a Data Validation dropdown on that column.
    2. A "Reference" sheet lists every valid value.
    3. The rejection message names the valid values instead of just saying
       "not valid" — so the user knows exactly what to fix.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from django.core.exceptions import ValidationError
from django.db import transaction

from . import excel


@dataclass
class Column:
    name: str
    field: str
    required: bool = False
    parse: Callable[[Any], Any] = lambda v: v
    help: str = ""
    example: str = ""
    # If set, returns the list of valid string values for this column.
    # Used for template dropdowns, the reference sheet, and clearer errors.
    lookup: Optional[Callable[[], list]] = None


@dataclass
class RowError:
    row_number: int
    errors: list
    data: dict


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    rejected: list = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self):
        return not self.rejected

    @property
    def total(self):
        return self.created + self.updated + len(self.rejected)

    def rejected_table(self, columns):
        header = ["Row"] + [c.name for c in columns] + ["Errors"]
        rows = [
            [r.row_number] + [r.data.get(c.field, r.data.get(c.name, "")) for c in columns]
            + ["; ".join(r.errors)]
            for r in self.rejected
        ]
        return header, rows


class ImportSpec:
    name = ""
    label = ""
    description = ""
    columns: list = []
    key_fields: tuple = ()

    def template_header(self):
        return [c.name for c in self.columns]

    def clean_row(self, raw):
        cleaned, errors = {}, []
        lookup = {c.name: c for c in self.columns}
        for col in self.columns:
            value = raw.get(col.field, raw.get(col.name))
            if isinstance(value, str):
                value = value.strip()
            if value in (None, ""):
                if col.required:
                    errors.append(f"{col.name} is required")
                cleaned[col.field] = None
                continue
            try:
                cleaned[col.field] = col.parse(value)
            except (ValueError, TypeError, ValidationError) as exc:
                msg = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
                errors.append(f"{col.name}: {msg or 'invalid value'}")
        unknown = set(raw) - {c.field for c in self.columns} - set(lookup)
        if unknown:
            errors.append(f"Unknown column(s): {', '.join(sorted(map(str, unknown)))}")
        if errors:
            raise ValidationError(errors)
        return cleaned

    def validate_row(self, cleaned):
        """Cross-field / DB checks. Raise ValidationError to reject."""

    def save_row(self, cleaned):
        raise NotImplementedError

    def run(self, rows: Iterable[dict], dry_run=False):
        result = ImportResult(dry_run=dry_run)
        for number, raw in enumerate(rows, start=1):
            try:
                cleaned = self.clean_row(raw)
                self.validate_row(cleaned)
                with transaction.atomic():
                    outcome = self.save_row(cleaned)
                    if dry_run:
                        transaction.set_rollback(True)
            except ValidationError as exc:
                result.rejected.append(RowError(number, list(exc.messages), dict(raw)))
                continue
            except Exception as exc:
                result.rejected.append(RowError(number, [f"Could not save: {exc}"], dict(raw)))
                continue
            if outcome == "created":
                result.created += 1
            else:
                result.updated += 1
        return result

    def run_from_file(self, file_like, dry_run=False):
        rows = excel.read_rows(file_like)
        return self.run(rows, dry_run=dry_run)

    def template_xlsx(self):
        return excel.write_template(self.columns, sheet_title=self.label or self.name or "Template")

    def rejected_xlsx(self, result):
        header, rows = result.rejected_table(self.columns)
        return excel.write_rejected(header, rows, sheet_title="Rejected rows")


class ExportSpec:
    name = ""
    label = ""
    description = ""
    columns: list = []

    def get_queryset(self):
        raise NotImplementedError

    def value(self, obj, column):
        return getattr(obj, column.field)

    def header(self):
        return [c.name for c in self.columns]

    def rows(self):
        for obj in self.get_queryset().iterator():
            yield [self.value(obj, c) for c in self.columns]

    def export_xlsx(self):
        return excel.write_export(self.header(), self.rows(), sheet_title=self.label or self.name or "Export")


REGISTRY = {"import": {}, "export": {}}


def register(spec_cls):
    kind = "import" if issubclass(spec_cls, ImportSpec) else "export"
    if not spec_cls.name:
        raise ValueError(f"{spec_cls.__name__} must declare a `name`.")
    REGISTRY[kind][spec_cls.name] = spec_cls
    return spec_cls


def all_specs():
    """{name: {"import": cls|None, "export": cls|None, "label": str, ...}}"""
    names = set(REGISTRY["import"]) | set(REGISTRY["export"])
    out = {}
    for name in sorted(names):
        imp = REGISTRY["import"].get(name)
        exp = REGISTRY["export"].get(name)
        exemplar = imp or exp
        out[name] = {
            "name": name,
            "import": imp,
            "export": exp,
            "label": getattr(exemplar, "label", "") or name.replace("-", " ").title(),
            "description": getattr(exemplar, "description", ""),
            "column_count": len(getattr(exemplar, "columns", []) or []),
        }
    return out