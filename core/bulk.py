"""Shared plumbing for "select and act in bulk" endpoints (Phase 8 UX).

Every bulk endpoint:
  * is POST-only and login-protected (the view's own decorators);
  * reads the selected primary keys from ``ids`` (repeated field or a
    comma-separated string) and the verb from ``action``;
  * filters the ids through a queryset of rows THIS user may act on, so a
    forged id for someone else's row is simply skipped ("not yours");
  * writes an audit entry;
  * answers JSON for the bulk-select.js fetch call (Accept: application/json)
    and a redirect + flash message for a plain form post (no-JS fallback).

Response JSON: {"ok": true, "action": "...", "done": [ids], "skipped":
[{"id": 7, "reason": "..."}], "message": "3 notifications deleted."}
"""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme

MAX_IDS = 500


def parse_ids(request, limit=MAX_IDS):
    raw = request.POST.getlist("ids")
    if len(raw) == 1 and "," in raw[0]:
        raw = raw[0].split(",")
    ids = []
    for value in raw:
        value = str(value).strip()
        if value.isdigit():
            ids.append(int(value))
    # keep order, drop duplicates
    seen = set()
    out = [i for i in ids if not (i in seen or seen.add(i))]
    return out[:limit]


def wants_json(request):
    accept = request.headers.get("Accept", "")
    return "application/json" in accept or request.headers.get("X-Requested-With") == "fetch"


def plural(n, noun, nouns=None):
    return f"{n} {noun if n == 1 else (nouns or noun + 's')}"


def safe_next(request, fallback):
    nxt = request.POST.get("next") or request.META.get("HTTP_REFERER") or ""
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                                require_https=request.is_secure()):
        return nxt
    return fallback


def respond(request, *, action, done, skipped=(), message="", fallback="/", status=200, level=None):
    skipped = [s if isinstance(s, dict) else {"id": s, "reason": ""} for s in skipped]
    ok = status < 400
    if wants_json(request):
        return JsonResponse({"ok": ok, "action": action, "done": list(done), "skipped": skipped,
                             "message": message}, status=status)
    level = level or (messages.SUCCESS if ok and done else messages.WARNING if ok else messages.ERROR)
    if message:
        messages.add_message(request, level, message)
    return redirect(safe_next(request, fallback))


def bad_request(request, message, fallback="/"):
    return respond(request, action=request.POST.get("action", ""), done=[], message=message,
                   fallback=fallback, status=400)
