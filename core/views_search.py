"""JSON view for the global search endpoint.

    GET /search/?q=<query>&types=task,ticket&limit=5

Response shape:
    {
      "ok": true,
      "query": "printer",
      "total": 4,
      "groups": [
        {"type": "task", "label": "Tasks", "icon": "check-square",
         "results": [{"id": 12, "title": "...", "url": "/tasks/12/",
                      "meta": "TSK-2025-00012 · Open",
                      "badge": "High", "snippet": "..."}, ...]}
      ]
    }

Every result is scoped to what the user could open themselves — the
providers call the same scoping helper the destination list views call.
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from . import search as search_service


MAX_QUERY_LEN = 200
MAX_LIMIT = 20
DEFAULT_LIMIT = 5


@login_required
@require_GET
def search_view(request):
    raw_q = (request.GET.get("q") or "").strip()
    if len(raw_q) > MAX_QUERY_LEN:
        raw_q = raw_q[:MAX_QUERY_LEN]

    types_param = (request.GET.get("types") or "").strip()
    types = [t.strip() for t in types_param.split(",") if t.strip()] or None

    try:
        limit = int(request.GET.get("limit") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    payload = search_service.search(
        request.user, raw_q, types=types, limit=limit,
    )
    return JsonResponse({"ok": True, **payload})