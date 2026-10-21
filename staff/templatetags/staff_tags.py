from django import template
from staff import services


register = template.Library()


def silhouette_kind(staff):
    """"male", "female" or "neutral" for the faint avatar background.

    Reads Staff.gender ("M" / "F", blank = unknown). Purely a visual hint: it is
    not exposed anywhere else, not searched and not exported.
    """
    gender = (getattr(staff, "gender", "") or "").strip().upper()
    return {"M": "male", "F": "female"}.get(gender, "neutral")


@register.inclusion_tag("staff/_avatar_fallback.html")
def staff_avatar_fallback(staff):
    """The no-photo avatar: a faint gender silhouette with the initials on top.

    Use ONLY in the `{% else %}` branch of an `{% if ...photo %}` check. When a
    photo exists the photo is shown alone, with no silhouette.
    """
    first = (getattr(staff, "first_name", "") or "")[:1].upper()
    last = (getattr(staff, "last_name", "") or "")[:1].upper()
    return {"kind": silhouette_kind(staff), "initials": f"{first}{last}"}


@register.inclusion_tag("staff/leadership/_preview.html")
def leadership_preview(limit=6):
    return {"leadership": services.featured_leadership(limit=limit)}