"""Shared building blocks for every Django admin page (Phase 8 admin theme).

Every app's ``admin.py`` builds on these instead of ``django.contrib.admin``'s
own classes, so the whole admin gets the same look (django-unfold plus the
NBTE theme in ``static/admin-theme``) and the same everyday tools:

* ``ModelAdmin`` — unfold's ModelAdmin with sensible defaults (50 rows a page,
  a filter "Apply" button, a warning before leaving an unsaved form) and an
  "Export selected to CSV" bulk action on every list.
* ``ReadOnlyModelAdmin`` — the same, but nothing can be added, changed or
  deleted (system records, audit trails, money records).
* ``UnscopedModelAdmin`` / ``UnscopedReadOnlyModelAdmin`` — for QA models with
  institution-scoped managers. Admin is system code, so it reads through the
  explicit ``unscoped`` manager (Master Plan §4).
* ``TabularInline`` / ``StackedInline`` — unfold's inlines.
* ``badge()`` — a status column rendered as a coloured label.
"""
from __future__ import annotations

import csv
import datetime as _dt

from django.contrib import admin, messages
from django.db import models
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.text import slugify
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import StackedInline as UnfoldStackedInline
from unfold.admin import TabularInline as UnfoldTabularInline
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    DropdownFilter,
    RangeDateFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)

__all__ = [
    "ModelAdmin", "ReadOnlyModelAdmin", "UnscopedModelAdmin", "UnscopedReadOnlyModelAdmin",
    "TabularInline", "StackedInline", "badge", "export_as_csv",
    "ChoicesDropdownFilter", "RangeDateFilter", "RangeDateTimeFilter", "RelatedDropdownFilter",
    "TONES", "values_filter", "StatusFilter",
]

# Never written to an export, whatever the model.
_SECRET_FIELD_HINTS = ("password", "token", "secret", "otp", "api_key")
EXPORT_ROW_LIMIT = 50_000


# ---------------------------------------------------------------------------
# Bulk action: export selected rows to CSV
# ---------------------------------------------------------------------------
def _export_fields(model):
    fields = []
    for f in model._meta.concrete_fields:
        if any(h in f.name.lower() for h in _SECRET_FIELD_HINTS):
            continue
        if isinstance(f, (models.FileField, models.BinaryField)):
            fields.append(f)  # exported as the stored name only
            continue
        fields.append(f)
    return fields


def _cell(obj, field):
    if field.is_relation:
        value = getattr(obj, field.attname, None)
        if value is None:
            return ""
        try:
            related = getattr(obj, field.name)
        except Exception:  # broken FK / scoped manager — keep the id
            return value
        return str(related) if related is not None else ""
    value = getattr(obj, field.attname, None)
    if value is None:
        return ""
    if isinstance(value, _dt.datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if hasattr(value, "name") and isinstance(field, models.FileField):
        return value.name or ""
    if field.choices:
        display = getattr(obj, f"get_{field.name}_display", None)
        if callable(display):
            return display()
    text = str(value)
    # Spreadsheet formula injection guard.
    if text[:1] in ("=", "+", "-", "@") and not text.lstrip("-+").replace(".", "", 1).isdigit():
        text = "'" + text
    return text


@admin.action(description="Export selected to CSV")
def export_as_csv(modeladmin, request, queryset):
    model = queryset.model
    fields = _export_fields(model)
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    name = slugify(model._meta.verbose_name_plural) or model._meta.model_name
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}-{stamp}.csv"'
    response["Cache-Control"] = "private, no-store"
    response.write("﻿")  # Excel opens UTF-8 correctly with a BOM
    writer = csv.writer(response)
    writer.writerow([str(f.verbose_name).capitalize() for f in fields])
    count = 0
    for obj in queryset.iterator(chunk_size=500):
        writer.writerow([_cell(obj, f) for f in fields])
        count += 1
        if count >= EXPORT_ROW_LIMIT:
            break
    try:
        from core import audit
        audit.log("other", request.user, actor=request.user,
                  changes={"model": model._meta.label, "rows": count}, note="Admin CSV export")
    except Exception:  # auditing must never block an export
        pass
    return response


# ---------------------------------------------------------------------------
# Status badges
# ---------------------------------------------------------------------------
TONES = ("success", "warning", "danger", "info", "neutral", "primary")

_DEFAULT_TONES = {
    # positive / finished
    "active": "success", "approved": "success", "completed": "success", "complete": "success",
    "confirmed": "success", "done": "success", "resolved": "success", "closed": "neutral",
    "present": "success", "paid": "success", "sent": "success", "ok": "success", "cleared": "success",
    "accepted": "success", "published": "success", "endorsed": "success", "delivered": "success",
    "in_use": "success", "available": "success",
    # in motion
    "open": "info", "new": "info", "assigned": "info", "in_progress": "info", "submitted": "info",
    "under_review": "info", "reviewing": "info", "pending": "warning", "awaiting": "warning",
    "awaiting_approval": "warning", "awaiting_receiving": "warning", "awaiting_response": "warning",
    "awaiting_review": "warning", "awaiting_director": "warning", "awaiting_fulfillment": "warning",
    "queued": "warning", "draft": "neutral", "proposed": "neutral", "late": "warning",
    "partial": "warning", "returned": "warning", "reopened": "warning", "escalated": "danger",
    "ready_for_handover": "primary", "in_repair": "warning", "maintenance": "warning",
    # negative
    "rejected": "danger", "failed": "danger", "absent": "danger", "cancelled": "neutral",
    "suspended": "danger", "delisted": "danger", "expired": "neutral", "breached": "danger",
    "overdue": "danger", "lost": "danger", "disposed": "neutral", "retired": "neutral",
    "declined": "danger", "blocked": "danger", "bounced": "danger", "refunded": "neutral",
}


def badge(field_name, description=None, tones=None, ordering=None):
    """A list column showing ``field_name`` as a coloured label.

    ``tones`` maps a stored value to one of TONES; anything unmapped falls back
    to a sensible default for common status words, then "neutral"."""
    tones = {**_DEFAULT_TONES, **(tones or {})}

    def _column(self, obj):
        value = getattr(obj, field_name, None)
        if value in (None, ""):
            return "—"
        display = getattr(obj, f"get_{field_name}_display", None)
        label = display() if callable(display) else str(value)
        tone = tones.get(str(value).lower(), "neutral")
        return format_html('<span class="nbte-badge nbte-badge--{}">{}</span>', tone, label)

    _column.short_description = description or field_name.replace("_", " ").capitalize()
    _column.admin_order_field = ordering or field_name
    _column.__name__ = f"{field_name}_badge"
    return _column


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def values_filter(field_name, title=None):
    """A dropdown filter over the values that actually occur in ``field_name``
    (for free-text status fields on flow-driven models, which declare no
    choices)."""

    class _Filter(DropdownFilter):
        parameter_name = field_name

        def lookups(self, request, model_admin):
            values = (model_admin.get_queryset(request).order_by()
                      .values_list(field_name, flat=True).distinct())
            return [(v, str(v).replace("_", " ").capitalize()) for v in sorted(set(values)) if v]

        def queryset(self, request, queryset):
            if self.value():
                return queryset.filter(**{field_name: self.value()})
            return queryset

    _Filter.title = title or field_name.replace("_", " ")
    _Filter.__name__ = f"{field_name.title().replace('_', '')}ValuesFilter"
    return _Filter


StatusFilter = values_filter("status")


# ---------------------------------------------------------------------------
# Restricted files and institution-scoped relations
# ---------------------------------------------------------------------------
_DOWNLOAD_SUFFIX = "__download"


def _is_restricted(field):
    from core.storage import RestrictedMediaStorage

    storage = getattr(field, "storage", None)
    storage = getattr(storage, "_wrapped", storage)
    return isinstance(storage, RestrictedMediaStorage)


def _download_link(obj, field_name):
    value = getattr(obj, field_name, None)
    if not obj or not getattr(obj, "pk", None) or not value:
        return "—"
    url = ""
    if hasattr(obj, "download_url"):
        try:
            url = obj.download_url()
        except Exception:
            url = ""
    if not url:
        from django.urls import reverse

        try:
            url = reverse("core:protected_download", args=[obj._meta.label, obj.pk])
        except Exception:
            url = ""
    name = getattr(obj, "original_name", "") or str(value.name).rsplit("/", 1)[-1]
    if not url:
        return name
    return format_html('<a class="nbte-file" href="{}"><span class="material-symbols-outlined" '
                       'aria-hidden="true">download</span>{}</a>', url, name)


def _empty_json_as_default(to_python, db_field):
    def parse(value):
        result = to_python(value)
        return db_field.get_default() if result is None else result
    return parse


class SafeFieldsMixin:
    """Keeps admin forms working with two project conventions:

    * **Restricted files** (``core.storage.RestrictedMediaStorage``) have no
      public URL, so the stock file widgets and read-only displays — which
      link to ``file.url`` — would crash. Editable ones get a plain file input;
      read-only ones show a link through the permission-checked download view.
    * **Institution-scoped models** refuse unscoped reads on their default
      manager, so drop-downs pointing at them read the ``unscoped`` manager
      (admin is system code, Master Plan §4).
    * **Blank JSON fields** (``JSONField(default=list, blank=True)``) are NOT
      NULL, but a cleared box parses to ``None``; save the field's default
      instead of failing with an IntegrityError."""

    def _restricted_files(self):
        return [f.name for f in self.model._meta.concrete_fields
                if isinstance(f, models.FileField) and _is_restricted(f)]

    def __getattr__(self, name):
        if name.endswith(_DOWNLOAD_SUFFIX):
            field_name = name[: -len(_DOWNLOAD_SUFFIX)]

            def _show(obj):
                return _download_link(obj, field_name)

            try:
                label = self.model._meta.get_field(field_name).verbose_name
            except Exception:
                label = field_name
            _show.short_description = str(label).capitalize()
            return _show
        raise AttributeError(name)

    def _can_edit(self, request, obj):
        try:
            if obj is None:
                try:
                    return self.has_add_permission(request, obj)
                except TypeError:
                    return self.has_add_permission(request)
            return self.has_change_permission(request, obj)
        except Exception:
            return True

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        restricted = self._restricted_files()
        if not restricted:
            return ro
        out = []
        for name in ro:
            out.append(name + _DOWNLOAD_SUFFIX if name in restricted else name)
        if not self._can_edit(request, obj):
            out += [n + _DOWNLOAD_SUFFIX for n in restricted if n + _DOWNLOAD_SUFFIX not in out]
        return out

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        restricted = self._restricted_files()
        if not restricted:
            return fieldsets
        ro = set(self.get_readonly_fields(request, obj))
        swap = {n for n in restricted if n + _DOWNLOAD_SUFFIX in ro}
        if not swap:
            return fieldsets

        def _fix(fields):
            seen, out = set(), []
            for f in fields:
                if isinstance(f, (list, tuple)):
                    f = tuple(_fix(f))
                elif f in swap:
                    f = f + _DOWNLOAD_SUFFIX
                if isinstance(f, str) and f in seen:
                    continue
                seen.add(f) if isinstance(f, str) else None
                out.append(f)
            return out

        return [(title, {**opts, "fields": _fix(opts.get("fields", ()))}) for title, opts in fieldsets]

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, models.FileField) and _is_restricted(db_field):
            from django import forms

            kwargs["widget"] = forms.ClearableFileInput if db_field.blank else forms.FileInput
            return db_field.formfield(**kwargs)
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if field is not None and isinstance(db_field, models.JSONField) and db_field.blank and not db_field.null:
            field.to_python = _empty_json_as_default(field.to_python, db_field)
        return field

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        related = db_field.remote_field.model
        if "queryset" not in kwargs and hasattr(related, "unscoped"):
            kwargs["queryset"] = related.unscoped.all()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        related = db_field.remote_field.model
        if "queryset" not in kwargs and hasattr(related, "unscoped"):
            kwargs["queryset"] = related.unscoped.all()
        return super().formfield_for_manytomany(db_field, request, **kwargs)


# ---------------------------------------------------------------------------
# Base classes
# ---------------------------------------------------------------------------
class ModelAdmin(SafeFieldsMixin, UnfoldModelAdmin):
    list_per_page = 50
    list_max_show_all = 500
    list_filter_submit = True
    warn_unsaved_form = True
    compressed_fields = True
    actions = [export_as_csv]

    def get_actions(self, request):
        actions = super().get_actions(request)
        # A subclass that sets its own `actions` keeps the CSV export too.
        if "export_as_csv" not in actions and self.has_view_permission(request):
            actions["export_as_csv"] = (export_as_csv, "export_as_csv", export_as_csv.short_description)
        return actions


class ReadOnlyModelAdmin(ModelAdmin):
    """System records: view and export only."""
    warn_unsaved_form = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class UnscopedModelAdmin(ModelAdmin):
    """Institution-scoped QA models: admin reads through ``unscoped``."""

    def get_queryset(self, request):
        manager = getattr(self.model, "unscoped", None) or self.model._default_manager
        qs = manager.all()
        ordering = self.get_ordering(request)
        if ordering:
            qs = qs.order_by(*ordering)
        return qs


class UnscopedReadOnlyModelAdmin(UnscopedModelAdmin, ReadOnlyModelAdmin):
    pass


class TabularInline(SafeFieldsMixin, UnfoldTabularInline):
    pass


class StackedInline(SafeFieldsMixin, UnfoldStackedInline):
    pass


def bulk_update_action(field, value, description, *, success="{n} record(s) updated."):
    """Build a bulk action that sets ``field`` to ``value`` on the selection."""

    def _action(modeladmin, request, queryset):
        n = queryset.update(**{field: value})
        modeladmin.message_user(request, success.format(n=n), messages.SUCCESS)

    _action.short_description = description
    _action.__name__ = f"set_{field}_{slugify(str(value)).replace('-', '_') or 'value'}"
    _action.allowed_permissions = ("change",)
    return _action


# ---------------------------------------------------------------------------
# Safety net: give every remaining stock ModelAdmin the theme
# ---------------------------------------------------------------------------
def _themed_inline(inline):
    from django.contrib.admin.options import StackedInline as DjStacked
    from unfold.admin import InlineModelAdmin as UnfoldInline

    if issubclass(inline, UnfoldInline):
        return inline
    base = StackedInline if issubclass(inline, DjStacked) else TabularInline
    return type(inline.__name__, (inline, base), {"__module__": inline.__module__})


def theme_registry(site=None):
    """Re-register any ModelAdmin that is not an unfold ModelAdmin (apps this
    project does not edit, such as the internal ``workflow`` app) as a
    subclass of both, so behaviour is kept and the look matches. Called from
    ``core.apps.CoreConfig.ready()`` after admin autodiscovery."""
    from django.contrib import admin as dj_admin

    site = site or dj_admin.site
    changed = 0
    for model, model_admin in list(site._registry.items()):
        cls = type(model_admin)
        if isinstance(model_admin, UnfoldModelAdmin):
            continue
        attrs = {"__module__": cls.__module__}
        if cls.inlines:
            attrs["inlines"] = [_themed_inline(i) for i in cls.inlines]
        if "export_as_csv" not in [getattr(a, "__name__", a) for a in (cls.actions or [])]:
            attrs["actions"] = list(cls.actions or []) + [export_as_csv]
        themed = type(cls.__name__, (cls, ModelAdmin), attrs)
        site.unregister(model)
        site.register(model, themed)
        changed += 1
    return changed
