"""Settings → QA → Workload limits (Phase 8): a small table editor for
``qa.workload.limits`` and its two switches, mounted in the "qa_set"
namespace at /qa/settings/workload/.

Every change goes through ``qa_core.settings_service.save_setting`` — the
same audited write path (and dual control, if an administrator turns it on)
as Rules & thresholds. Adding or removing a row only re-renders the editor;
nothing is stored until "Save limits".
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from config.models import ConfigEntry
from qa_core import settings_service
from qa_core.permissions import can_view_qa_settings, require
from qa_core.ui import render_qa, toast

from . import workload

SWITCHES = [
    ("qa.workload.staff_first", "List NBTE staff before resource persons",
     "In the availability finder, eligible staff come first; resource persons follow."),
    ("qa.workload.resource_persons_only_when_no_staff", "Use resource persons only when no staff member is free",
     "Booking a resource person is refused while an eligible NBTE staff member is free for the same work."),
]


def _entry(key):
    return ConfigEntry.objects.filter(key=key, scope="global").first()


def _general_values():
    out = {}
    for key, _label, _help, setting, fallback in workload.FIELDS:
        out[key] = workload._general(setting, fallback)
    return out


def _rows_from_post(post):
    keys = post.getlist("key")
    labels = post.getlist("label")
    columns = {f: post.getlist(f) for f in workload.FIELD_KEYS}
    rows = []
    for i, key in enumerate(keys):
        row = {"key": (key or "").strip().lower(), "label": (labels[i] if i < len(labels) else "").strip()}
        for f in workload.FIELD_KEYS:
            values = columns[f]
            row[f] = (values[i] if i < len(values) else "").strip()
        rows.append(row)
    return rows


def _validate(rows):
    errors, seen = [], set()
    for n, row in enumerate(rows, start=1):
        if not row["key"]:
            errors.append(f"Row {n}: choose who the row is for.")
            continue
        if row["key"] in seen:
            errors.append(f"Row {n}: {row['key']} appears twice.")
        seen.add(row["key"])
        for f in workload.FIELD_KEYS:
            value = row.get(f, "")
            if value not in ("", None) and not str(value).isdigit():
                label = next(x[1] for x in workload.FIELDS if x[0] == f)
                errors.append(f"Row {n}: “{label}” must be a whole number, or blank.")
    return errors


def _ctx(request, rows=None, **extra):
    from qa_core.views import _settings_context

    entry = _entry(workload.SETTING)
    editable, why = settings_service.can_edit(request.user, entry) if entry else (False, "Run seed_startup.")
    rows = rows if rows is not None else workload.table()
    labels = dict(workload.key_choices())
    for row in rows:
        row["who"] = labels.get(row["key"], row["key"])
        row["cells"] = [{"field": f, "value": "" if row.get(f) in (None, "") else row.get(f)}
                        for f in workload.FIELD_KEYS]
    switches = []
    for key, label, help_text in SWITCHES:
        e = _entry(key)
        switches.append({"key": key, "label": label, "help": help_text,
                         "on": workload.bool_setting(key, key == "qa.workload.staff_first"),
                         "exists": e is not None})
    return _settings_context(request, "workload", rows=rows, fields=workload.FIELDS, choices=workload.key_choices(),
                             general=_general_values(), editable=editable, why=why, entry=entry,
                             switches=switches, dual=bool(entry and entry.dual_control), **extra)


@require(can_view_qa_settings)
@require_http_methods(["GET", "POST"])
def workload_limits(request):
    if request.method == "GET":
        return render_qa(request, "inspection/settings/workload.html", _ctx(request),
                         {"qa-panel": "inspection/settings/_workload_panel.html"})
    rows = _rows_from_post(request.POST)
    op = request.POST.get("op", "save")
    if op == "add":
        rows.append({"key": "", "label": "", **{f: "" for f in workload.FIELD_KEYS}})
        return render(request, "inspection/settings/_workload_panel.html", _ctx(request, rows=rows, dirty=True))
    if op.startswith("remove:"):
        try:
            index = int(op.split(":", 1)[1])
        except ValueError:
            index = -1
        if 0 <= index < len(rows) and rows[index]["key"] != "default":
            rows.pop(index)
        return render(request, "inspection/settings/_workload_panel.html", _ctx(request, rows=rows, dirty=True))
    entry = _entry(workload.SETTING)
    errors = _validate(rows)
    reason = (request.POST.get("reason") or "").strip()
    saved, pending = [], []
    if not errors:
        try:
            new_value = workload.serialise(rows)
            if entry is None:
                raise ValidationError("The setting qa.workload.limits does not exist yet. Run seed_startup.")
            if new_value != entry.value:
                outcome, obj = settings_service.save_setting(request.user, entry, new_value, reason=reason,
                                                             impersonating=bool(getattr(request, "is_impersonating",
                                                                                        False)))
                (saved if outcome == "saved" else pending).append(obj)
            for key, _label, _help in SWITCHES:
                e = _entry(key)
                if e is None:
                    continue
                value = "true" if request.POST.get(key) == "1" else "false"
                if settings_service.normalise_input(e, value) != e.value:
                    outcome, obj = settings_service.save_setting(request.user, e, value, reason=reason)
                    (saved if outcome == "saved" else pending).append(obj)
        except (ValidationError, PermissionDenied) as exc:
            errors = list(getattr(exc, "messages", None) or [str(exc)])
    if errors:
        response = render(request, "inspection/settings/_workload_panel.html",
                          _ctx(request, rows=rows, errors=errors, dirty=True), status=422)
        return toast(response, "error", "Not saved", errors[0])
    response = render(request, "inspection/settings/_workload_panel.html", _ctx(request))
    if pending:
        return toast(response, "success", "Sent for confirmation",
                     "A second officer confirms the change in Pending confirmations.")
    if saved:
        return toast(response, "success", "Workload limits saved", "They apply to the next assignment.")
    return toast(response, "info", "Nothing changed")
