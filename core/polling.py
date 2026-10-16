"""Short-poll responses for the former SSE endpoints (review finding H2).

The old endpoints looped forever with time.sleep(), holding one server worker
per open browser tab. They now answer immediately:

  * JSON (default for the new JS):  {"last_pk": N, "count": K}
  * SSE (old clients / EventSource): one frame, then the connection closes;
    the `retry:` field tells the browser when to ask again, and the `id:`
    field (sent back as Last-Event-ID) stops the same activity firing twice.

No request ever sleeps, so no worker is held.
"""
import json

from django.http import HttpResponse, JsonResponse

ACTIVITY_RETRY_MS = 10_000
UNREAD_RETRY_MS = 20_000


def _wants_sse(request):
    accept = request.headers.get("Accept") or ""
    return "text/event-stream" in accept and "application/json" not in accept


def since_value(request):
    """`?since=` or the Last-Event-ID header, whichever is larger."""
    values = [request.GET.get("since"), request.headers.get("Last-Event-ID")]
    best = 0
    for value in values:
        try:
            best = max(best, int(value or 0))
        except (TypeError, ValueError):
            continue
    return best


def _sse(body, retry_ms):
    resp = HttpResponse(f"retry: {retry_ms}\n{body}\n", content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


def activity_response(request, queryset):
    """Answer one poll for an activity timeline.

    `queryset` must already be filtered to the object being watched.
    """
    since = since_value(request)
    new_pks = list(
        queryset.filter(pk__gt=since).order_by("pk").values_list("pk", flat=True)[:50]
    )
    last = new_pks[-1] if new_pks else since
    payload = {"last_pk": last, "count": len(new_pks)}
    if not _wants_sse(request):
        resp = JsonResponse(payload)
        resp["Cache-Control"] = "no-store"
        return resp
    if new_pks:
        body = f"id: {last}\nevent: activity\ndata: {json.dumps(payload)}\n"
    else:
        body = f"id: {last}\n: no new activity\n"
    return _sse(body, ACTIVITY_RETRY_MS)


def unread_response(request, unread):
    """Answer one poll for the notification badge."""
    payload = {"unread": unread}
    if not _wants_sse(request):
        resp = JsonResponse(payload)
        resp["Cache-Control"] = "no-store"
        return resp
    return _sse(f"event: unread\ndata: {json.dumps(payload)}\n", UNREAD_RETRY_MS)
