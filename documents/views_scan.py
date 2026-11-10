"""Scan and verify (X26; design 27). Served on both hosts:

* ``/scan/``          - staff host: the QA shell (signed-in staff); visitors go to
                        the portal's public Scan page. Portal host: the portal shell
                        for signed-in users, the public layout for visitors.
* ``/scan/lookup/``   - POST {raw, via}: the result card (HTMX fragment). Rate-limited.
* ``/v/<token>/``     - the URL inside every QR: the full page with the result.

Camera access is allowed by ``Permissions-Policy: camera=(self)`` on these
paths only (documents.middleware).
"""
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from qa_core.hosts import is_portal_request

from . import issue as issuing
from . import verify


def _is_staff(user):
    return user.is_authenticated and getattr(user, "user_type", "staff") == "staff"


def _headers(response):
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    if response.status_code == 429:
        response["Retry-After"] = "3600"
    return response


def _page(request, ctx, status=200):
    """Pick the layout for the host and the visitor."""
    ctx.setdefault("scan_lookup_url", "/scan/lookup/")
    ctx.setdefault("verify_base", issuing.verify_base())
    if is_portal_request(request):
        if request.user.is_authenticated:
            from qa_core.portal_views import _shell_context

            ctx = {**_shell_context(request, "scan"), **ctx}
            return render(request, "documents/scan/page_portal.html", ctx, status=status)
        from qa_core.portal_public import public_context

        ctx = {**public_context(request, "scan"), **ctx}
        return render(request, "documents/scan/page_public.html", ctx, status=status)
    from qa_core.ui import render_qa

    from . import services

    ctx.setdefault("can_register", request.user.is_authenticated and services.can_view_register(request.user))
    return render_qa(request, "documents/scan/page_staff.html", ctx, status=status)


@never_cache
@require_http_methods(["GET"])
def scan(request):
    if not is_portal_request(request) and not _is_staff(request.user):
        if request.user.is_authenticated:  # institution users belong on the portal
            return HttpResponseRedirect(issuing.scan_url())
        return HttpResponseRedirect(issuing.scan_url())
    return _headers(_page(request, {"card": None}))


@never_cache
@require_POST
def lookup(request):
    if not is_portal_request(request) and not _is_staff(request.user):
        return _headers(render(request, "documents/scan/_result.html",
                               {"card": verify.not_found_card()}, status=403))
    raw = (request.POST.get("raw") or request.POST.get("code") or "").strip()[:600]
    via = request.POST.get("via") or "code"
    card, status = verify.check(request, raw, via)
    # HTMX swaps only 2xx by default; the card itself says "not found" / "too many".
    status = status if not request.headers.get("HX-Request") else 200
    return _headers(render(request, "documents/scan/_result.html", {"card": card}, status=status))


@never_cache
@require_http_methods(["GET"])
def verify_token(request, token):
    card, status = verify.check(request, f"/v/{token}/", "link")
    return _headers(_page(request, {"card": card, "from_link": True}, status=status))
