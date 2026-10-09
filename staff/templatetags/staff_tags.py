from django import template
from staff import services


register = template.Library()


@register.inclusion_tag("staff/leadership/_preview.html")
def leadership_preview(limit=6):
    return {"leadership": services.featured_leadership(limit=limit)}