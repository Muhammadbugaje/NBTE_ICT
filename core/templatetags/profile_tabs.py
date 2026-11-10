from django import template

from core.profile_tabs import tabs_for

register = template.Library()


@register.inclusion_tag("partials/profile_tabs.html", takes_context=True)
def profile_tabs(context, active=""):
    request = context.get("request")
    user = getattr(request, "user", None)
    tabs = tabs_for(user, active) if user is not None and user.is_authenticated else []
    return {"tabs": tabs if len(tabs) > 1 else [], "active": active}
