"""Shared list-page template tags.

One `sortable_th` tag that generates the entire sortable table header —
including HTMX attributes and the direction toggle — from the current
`request.GET`. Used on every list page in the project.
"""
from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag(takes_context=True)
def sortable_th(context, key, label, container_id="wf-list-container"):
    """Render a sortable <th> for a query-param-driven list page.

    Usage:
        {% sortable_th "created"   "Raised" %}
        {% sortable_th "reference" "Ref" container_id="asset-list-container" %}

    Behavior:
      - Reads current sort state from request.GET ("sort" + "direction")
      - Clicking the header flips direction (desc -> asc -> desc)
      - Preserves every other query parameter (q, status, department, ...)
      - Emits HTMX attributes so the swap happens in-place
      - The target container must have id = container_id

    Defaults: sort_key="created", sort_dir="desc" — match these in the
    view's `apply_sort(... default="created")` call.
    """
    request = context.get("request")
    if request is None:
        return ""

    current_sort = request.GET.get("sort", "")
    current_dir = request.GET.get("direction", "desc")
    is_sorted = current_sort == key
    next_dir = "asc" if (is_sorted and current_dir == "desc") else "desc"

    params = request.GET.copy()
    params["sort"] = key
    params["direction"] = next_dir
    qs = params.urlencode()

    arrow = ""
    if is_sorted:
        arrow = "▼" if current_dir == "desc" else "▲"

    classes = "wf-th-sortable"
    if is_sorted:
        classes += " is-sorted"

    target = f"#{container_id}"
    return format_html(
        '<th class="{}">'
        '<a href="?{}" '
        '   hx-get="?{}" '
        '   hx-target="{}" '
        '   hx-select="{}" '
        '   hx-swap="outerHTML" '
        '   hx-push-url="true">'
        '{} <span class="wf-sort-arrow">{}</span>'
        '</a></th>',
        classes, qs, qs, target, target, label, arrow,
    )