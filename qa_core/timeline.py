"""The unified institution timeline (Master Plan §5.1 TimelineEvent, D12).

`record()` is the single writer. Rows are append-only; `public` rows are what
an institution sees, `internal` rows only NBTE staff with
`qa_core.view_timeline_internal`.
"""
from django.contrib.contenttypes.models import ContentType


def record(institution, event_code, *, public_text="", internal_text="", visibility="internal",
           subject=None, actor=None):
    from .models import TimelineEvent

    if institution is None:
        return None
    ct, oid = None, ""
    if subject is not None and getattr(subject, "pk", None) is not None:
        ct = ContentType.objects.get_for_model(subject, for_concrete_model=True)
        oid = str(subject.pk)
    return TimelineEvent.unscoped.create(
        institution=institution,
        content_type=ct,
        object_id=oid,
        event_code=event_code,
        public_text=public_text,
        internal_text=internal_text,
        visibility=visibility,
        actor=actor if getattr(actor, "pk", None) else None,
    )
