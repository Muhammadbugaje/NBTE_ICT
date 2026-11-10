"""{% add_to_calendar kind obj %} — the "Add to calendar" menu (templates/partials/add_to_calendar.html).

kind: "inspection" | "task" | "complaint" | "query". The tag picks the door
(internal vs QA portal) from the request, builds the .ics link, a Google
Calendar link and the person's subscribable feed, and renders nothing when
the item has no date (a task without a due date, a complaint stage without
an SLA clock, a cancelled visit).

    {% load nbte_calendar %}{% add_to_calendar "task" task %}
    {% add_to_calendar "inspection" inspection size="sm" %}
"""
from django import template
from django.urls import NoReverseMatch, reverse

from core import calendar_views as cv
from core.ics import google_calendar_url

register = template.Library()

INTERNAL = {"inspection": ("nbte_cal:inspection", cv.inspection_event, "qa_insp:inspection"),
            "task": ("nbte_cal:task", cv.task_event, "tasks:detail"),
            "complaint": ("nbte_cal:complaint", cv.complaint_event, "qa_complaints:detail")}
PORTAL = {"inspection": ("qa_portal:cal_inspection", cv.inspection_event, None),
          "query": ("qa_portal:cal_query", cv.query_event, "qa_portal:query")}


def _has_date(kind, obj):
    if kind == "inspection":
        return getattr(obj, "status", "") != "cancelled" and bool(obj.scheduled_start)
    if kind == "task":
        return bool(getattr(obj, "due_date", None)) and not getattr(obj, "is_closed", False)
    if kind == "complaint":
        return getattr(obj, "closed_at", None) is None and cv.complaint_due(obj)[1] is not None
    if kind == "query":
        return bool(getattr(obj, "due_at", None)) and getattr(obj, "status", "") != "closed"
    return False


@register.inclusion_tag("partials/add_to_calendar.html", takes_context=True)
def add_to_calendar(context, kind, obj, size="sm", label="Add to calendar"):
    request = context.get("request")
    empty = {"show": False}
    if request is None or obj is None or not getattr(request, "user", None) or not request.user.is_authenticated:
        return empty
    try:
        from qa_core.hosts import is_portal_request

        portal = is_portal_request(request)
    except Exception:  # noqa: BLE001 — never break a page over a calendar link
        portal = False
    table = PORTAL if portal else INTERNAL
    if kind not in table or not _has_date(kind, obj):
        return empty
    name, build, detail_name = table[kind]
    try:
        ics_url = reverse(name, args=[obj.pk])
        detail = reverse(detail_name, args=[obj.pk]) if detail_name else ""
        if kind == "inspection":
            event = build(obj, request, detail, internal=not portal)
        else:
            event = build(obj, request, detail)
        # No private feed link while someone is viewing as another person.
        feed_url = "" if getattr(request, "is_impersonating", False) else request.build_absolute_uri(
            reverse("qa_portal:cal_feed" if portal else "nbte_cal:feed", args=[cv.feed_token(request.user)]))
    except (NoReverseMatch, AttributeError, TypeError, ValueError):
        return empty
    return {
        "show": True, "ics_url": ics_url, "google_url": google_calendar_url(event), "size": size, "label": label,
        "feed_url": feed_url, "webcal_url": ("webcal://" + feed_url.split("://", 1)[-1]) if feed_url else "",
        "all_day": event.all_day, "uid": f"{kind}-{obj.pk}",
    }
