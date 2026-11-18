"""/me/tours/ (both hosts). See qa_core/tours.py.

GET   this person's guided tours, for tour.js. The address carries a hash of
      the registry (``?v=``), so the browser may keep the answer.
POST  remember which tours a person has finished or dismissed, in
      ``User.preferences["tours"]``.
"""
import json

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from . import tours


def _host(request):
    try:
        from .hosts import is_portal_request

        return "portal" if is_portal_request(request) else "staff"
    except Exception:
        return "staff"


@require_http_methods(["GET", "POST"])
def tour_state(request):
    user = request.user
    if not user.is_authenticated:
        return JsonResponse({"error": "Sign in first."}, status=401)
    if request.method == "GET":
        response = JsonResponse(tours.data(request, host=_host(request)))
        # Private to this person; a changed registry changes the address, so a day is safe.
        response["Cache-Control"] = "private, max-age=86400" if request.GET.get("v") else "private, no-cache"
        response["Vary"] = "Cookie"
        return response
    if getattr(request, "is_impersonating", False):
        return JsonResponse({"error": "Not saved while viewing as someone else."}, status=403)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    tour_id = str(data.get("id") or request.POST.get("id") or "")
    state = str(data.get("state") or request.POST.get("state") or "")
    if state == "reset":
        return JsonResponse({"ok": True, "seen": tours.reset(user)})
    try:
        seen = tours.record(user, tour_id, state)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "seen": seen})
