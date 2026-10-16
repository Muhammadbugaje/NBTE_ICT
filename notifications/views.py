"""Notification views — bell payload, dropdown, list, read actions, SSE stream."""
import json
import time

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.sorting import apply_sort

from .models import Notification

POLL_INTERVAL = 5      # seconds between unread checks in the SSE stream
BELL_LIMIT = 8         # notifications shown in the topbar dropdown
LIST_PER_PAGE = 30     # notifications per page on the full list


SORTABLE_NOTIFICATIONS = {
    "created": "created_at",
    "level":   "level",
    "read":    "is_read",
}


# ---------------------------------------------------------------------------
# Request-shape helpers
# ---------------------------------------------------------------------------
def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _wants_json(request):
    """True for classic XMLHttpRequest / fetch(..., Accept: application/json)."""
    if _is_htmx(request):
        return False
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = (request.headers.get("Accept") or "").lower()
    return "application/json" in accept


def _unread_count(user):
    return Notification.objects.filter(recipient=user, is_read=False).count()


def _trigger_and_redirect(request, fallback="notifications:list", notification=None):
    """Return the right response for a mark-read style action.

      HTMX           → 204, fires `notificationCountChanged` on <body>
      JSON client    → JsonResponse with the new unread count
      Plain form     → 302 redirect back to the list
    """
    user = request.user
    unread = _unread_count(user)
    payload = {"unread": unread}
    if notification is not None:
        payload.update({
            "id": notification.pk,
            "is_read": notification.is_read,
        })

    if _is_htmx(request):
        resp = HttpResponse(status=204)
        # Fires on document.body — the bell JS and any listener picks it up.
        resp["HX-Trigger"] = json.dumps({
            "notificationCountChanged": payload
        })
        return resp

    if _wants_json(request):
        return JsonResponse({"ok": True, **payload})

    return redirect(reverse(fallback))


# ---------------------------------------------------------------------------
# Bell
# ---------------------------------------------------------------------------
@login_required
def bell(request):
    """JSON: {unread: N, items: [...]} — what the dropdown shows."""
    qs = Notification.objects.filter(recipient=request.user)
    unread = qs.filter(is_read=False).count()
    items = [
        {
            "id": n.pk,
            "title": n.title,
            "body": n.body,
            "url": n.url,
            "level": n.level,
            "category": n.category,
            "is_read": n.is_read,
            "when": _humanise(n.created_at),
        }
        for n in qs.select_related("actor")[:BELL_LIMIT]
    ]
    return JsonResponse({"unread": unread, "items": items})


def _humanise(dt):
    from django.utils import timezone
    delta = timezone.now() - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 604800:
        return f"{seconds // 86400}d ago"
    return dt.strftime("%b %d")


# ---------------------------------------------------------------------------
# List page — search + sort + HTMX-ready
# ---------------------------------------------------------------------------
@login_required
def list_view(request):
    qs = (
        Notification.objects
        .filter(recipient=request.user)
        .select_related("actor")
    )

    filter_ = request.GET.get("filter", "all")
    if filter_ == "unread":
        qs = qs.filter(is_read=False)
    elif filter_ == "read":
        qs = qs.filter(is_read=True)

    q = (request.GET.get("q") or "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(title__icontains=q) | Q(body__icontains=q))

    qs, sort_key, sort_dir = apply_sort(
        qs, request.GET.get("sort"), request.GET.get("direction"),
        SORTABLE_NOTIFICATIONS, default="created",
    )

    paginator = Paginator(qs, LIST_PER_PAGE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "notifications/list.html", {
        "page": page,
        "filter": filter_,
        "q": q,
        "sort_key": sort_key,
        "sort_dir": sort_dir,
        "unread_count": _unread_count(request.user),
    })


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
@login_required
@require_POST
def mark_read(request, pk):
    n = get_object_or_404(Notification, pk=pk, recipient=request.user)
    n.mark_read()
    return _trigger_and_redirect(request, notification=n)


@login_required
@require_POST
def mark_unread(request, pk):
    n = get_object_or_404(Notification, pk=pk, recipient=request.user)
    n.mark_unread()
    return _trigger_and_redirect(request, notification=n)


@login_required
@require_POST
def mark_all_read(request):
    from django.utils import timezone
    Notification.objects.filter(
        recipient=request.user, is_read=False
    ).update(is_read=True, read_at=timezone.now())
    return _trigger_and_redirect(request)


@login_required
def open_and_read(request, pk):
    """Click target — mark read then redirect to the notification's url."""
    n = get_object_or_404(Notification, pk=pk, recipient=request.user)
    n.mark_read()
    target = n.url or "/"
    return redirect(target)


# ---------------------------------------------------------------------------
# SSE stream — real-time unread count
# ---------------------------------------------------------------------------
@login_required
def stream(request):
    """Unread-count poll (was an endless SSE stream - review H2).

    Answers immediately: JSON for the polling bell, or a single SSE frame
    with a `retry:` hint for any old EventSource client. Never sleeps.
    """
    from core.polling import unread_response

    unread = Notification.objects.filter(recipient=request.user, is_read=False).count()
    return unread_response(request, unread)


