"""WorkAssignment is a grant source (P.5): the hourly sweep extends grants of
open work and revokes grants whose work closed."""
from . import registry


def _label(wa):
    return f"{wa.get_kind_display()} {wa.subject_label}".strip()


def _url(wa):
    from django.urls import reverse
    try:
        return reverse("assignments:my_work")
    except Exception:
        return ""


registry.register_source("assignments.WorkAssignment", is_open=lambda wa: wa.is_working,
                         due_at=lambda wa: wa.due_at, label=_label, url=_url)
