"""v2.3 Phase 2 (owner decision 3): versioned case work.

When a rollback reopens work, every affected record is frozen first as a
read-only ``ArtifactVersion`` (exactly as it was submitted or signed). The live
record then opens as the next version, pre-filled from the frozen one
(copy-forward), so nobody retypes anything and nothing is lost. Dated notes
can be appended to a frozen version; the version itself never changes.

    freeze(case, obj, kind, *, title, change_request=None, actor=None, reason="")
    versions_for(case, kind=None, obj=None)   -> newest first
    append_note(version, user, text)
    can_view(user, case) / can_append(user, case)
"""
from __future__ import annotations

import json

from django.core import serializers
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.utils import timezone

# Fields that describe the workflow, not the content (shown separately).
_SKIP = {"created_at", "updated_at"}


def snapshot(obj):
    """The record's own fields as plain JSON (foreign keys as ids)."""
    raw = serializers.serialize("python", [obj])[0]["fields"]
    data = {k: v for k, v in raw.items() if k not in _SKIP}
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def _next_version(case, label, object_id):
    from .models import ArtifactVersion

    last = (ArtifactVersion.unscoped.filter(case=case, object_label=label, object_id=object_id)
            .order_by("-version").values_list("version", flat=True).first())
    return (last or 0) + 1


def freeze(case, obj, kind, *, title, change_request=None, actor=None, reason=""):
    """Freeze ``obj`` as the next read-only version and return it."""
    from .models import ArtifactVersion

    label = obj._meta.label_lower
    current = getattr(obj, "version", None)
    number = max(_next_version(case, label, obj.pk), int(current or 0) or 1)
    return ArtifactVersion.unscoped.create(
        case=case, kind=kind, object_label=label, object_id=obj.pk, title=title[:255], version=number,
        data=snapshot(obj), change_request=change_request, reason=(reason or "")[:4000],
        frozen_by=actor if getattr(actor, "pk", None) else None, frozen_at=timezone.now())


def versions_for(case, kind=None, obj=None):
    from .models import ArtifactVersion

    qs = ArtifactVersion.unscoped.filter(case=case).select_related("frozen_by", "change_request")
    if kind:
        qs = qs.filter(kind=kind)
    if obj is not None:
        qs = qs.filter(object_label=obj._meta.label_lower, object_id=obj.pk)
    from django.db.models import Prefetch

    from .models import ArtifactAddendum

    return qs.order_by("-frozen_at", "-pk").prefetch_related(
        Prefetch("addenda", queryset=ArtifactAddendum.unscoped.select_related("author").order_by("pk")))


def can_view(user, case):
    from . import case_services as cs

    return cs.visible_cases(user).filter(pk=case.pk).exists()


def can_append(user, case):
    from . import case_services as cs

    return (getattr(user, "user_type", "") == "staff" and can_view(user, case) and not cs.is_monitor(user))


@transaction.atomic
def append_note(version, user, text):
    from .models import ArtifactAddendum

    text = (text or "").strip()
    if len(text) < 3:
        raise ValidationError("Write the note (at least 3 characters).")
    if not can_append(user, version.case):
        raise PermissionDenied("You cannot add notes to this case.")
    note = ArtifactAddendum.unscoped.create(version=version, text=text[:8000], author=user)
    from qa_core import timeline

    timeline.record(version.case.institution, "qa.case.version_note", subject=version.case, actor=user,
                    internal_text=f"Note added to {version.title} v{version.version}: {text[:300]}")
    return note


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
LABELS = {
    "summary": "Summary", "findings": "Findings", "recommendation": "Recommendation",
    "recommendation_note": "Director's recommendation note", "status": "Status", "data": "Form answers",
    "outcome": "Outcome", "weaknesses": "Weaknesses", "note": "Note", "approved_streams": "Approved streams",
    "commencement_date": "Commencement date", "expiry_date": "Expiry date", "sections": "Report sections",
    "review_note": "Team Lead's note", "attended": "Attended", "role": "Role",
}


def readable(version):
    """[(label, value as text)] for the frozen fields worth reading."""
    rows = []
    for key, value in (version.data or {}).items():
        if value in (None, "", [], {}):
            continue
        label = LABELS.get(key, key.replace("_", " ").capitalize())
        if isinstance(value, (dict, list)):
            text = _flatten(value)
        else:
            text = str(value)
        rows.append({"key": key, "label": label, "value": text, "long": len(text) > 120 or "\n" in text})
    order = list(LABELS)
    rows.sort(key=lambda r: (order.index(r["key"]) if r["key"] in order else len(order), r["label"]))
    return rows


def _flatten(value, depth=0):
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            if v in (None, "", [], {}):
                continue
            parts.append(f"{'  ' * depth}{str(k).replace('_', ' ').capitalize()}: "
                         + (("\n" + _flatten(v, depth + 1)) if isinstance(v, (dict, list)) else str(v)))
        return "\n".join(parts)
    if isinstance(value, list):
        return "\n".join(f"{'  ' * depth}• " + (_flatten(v, depth + 1).strip() if isinstance(v, (dict, list))
                                                 else str(v)) for v in value)
    return str(value)


def version_of(case, version_id):
    """One frozen version of ``case`` (the caller has already opened the case)."""
    from .models import ArtifactVersion

    return ArtifactVersion.unscoped.filter(pk=version_id, case=case).first()
