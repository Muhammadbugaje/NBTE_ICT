"""The unified institution timeline (Master Plan §5.1 TimelineEvent, D12).

`record()` is the single writer. Rows are append-only; `public` rows are what
an institution sees, `internal` rows only NBTE staff with
`qa_core.view_timeline_internal`.
"""
from django.contrib.contenttypes.models import ContentType


def _department_of(subject, actor):
    """X3: the department whose part of the journey an event belongs to: the
    subject's ``DEPARTMENT_PATH`` when it has one, else the actor's department."""
    path = getattr(type(subject), "DEPARTMENT_PATH", None) if subject is not None else None
    if path:
        obj = subject
        for part in path.split("__"):
            obj = getattr(obj, part, None)
            if obj is None:
                break
        if obj is not None and getattr(obj, "pk", None):
            return obj
    if actor is not None and getattr(actor, "user_type", "staff") == "staff" and getattr(actor, "department_id", None):
        return actor.department
    return None


def default_audience(visibility, department):
    if visibility == "public":
        return "public"
    return "department" if department is not None else "full"


def record(institution, event_code, *, public_text="", internal_text="", visibility="internal",
           subject=None, actor=None, at=None, department=None, audience=None):
    """``at`` (optional) dates an event in the past — used only when history
    from another system is brought in; everything else is recorded now.

    v2.1 X3/X6: ``department`` defaults to the subject's department (or the
    actor's); ``audience`` to public / department / full from ``visibility``."""
    from .models import TimelineEvent

    if institution is None:
        return None
    ct, oid = None, ""
    if subject is not None and getattr(subject, "pk", None) is not None:
        ct = ContentType.objects.get_for_model(subject, for_concrete_model=True)
        oid = str(subject.pk)
    if department is None:
        try:
            department = _department_of(subject, actor)
        except Exception:
            department = None
    audience = audience or default_audience(visibility, department)
    return TimelineEvent.unscoped.create(
        department=department,
        audience=audience,
        institution=institution,
        content_type=ct,
        object_id=oid,
        event_code=event_code,
        public_text=public_text,
        internal_text=internal_text,
        visibility=visibility,
        actor=actor if getattr(actor, "pk", None) else None,
        **({"at": at} if at is not None else {}),
    )
