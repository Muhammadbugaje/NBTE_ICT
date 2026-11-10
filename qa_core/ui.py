"""Shared view helpers that implement the §20 interaction contract on the
server side.

* :func:`render_qa` — one view, three shapes: the full page for a normal
  request (deep links, refresh, accessibility tools) and an HTML partial for an
  HTMX request, chosen by the ``HX-Target`` header.
* :func:`paginate` — 25 rows per page by default, or "Show all" up to
  ``qa.ui.max_show_all_rows``. The limit is enforced here, so a crafted URL
  cannot bypass it.
* :func:`toast` — an ``HX-Trigger`` header that qa.js turns into an
  ``NBTE.Toast`` (and an aria-live announcement).
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.shortcuts import render

import config

PER_PAGE = 25


def is_htmx(request):
    return request.headers.get("HX-Request", "").lower() == "true"


def hx_target(request):
    return request.headers.get("HX-Target", "") if is_htmx(request) else ""


def layout(request):
    """'mobile' or 'desktop' from the X-NBTE-Layout header qa.js sends."""
    return "mobile" if request.headers.get("X-NBTE-Layout", "").lower() == "mobile" else "desktop"


def render_qa(request, full_template, context, partials=None, status=200):
    """Render `full_template`, or the partial registered for the HTMX target."""
    partials = partials or {}
    target = hx_target(request)
    context = dict(context)
    context.setdefault("qa_layout", layout(request))
    context.setdefault("qa_timeout_ms", timeout_ms())
    if target and target in partials:
        return render(request, partials[target], context, status=status)
    return render(request, full_template, context, status=status)


def timeout_ms():
    try:
        return max(3, int(config.get("qa.ui.request_timeout_seconds", 15))) * 1000
    except (TypeError, ValueError):
        return 15000


def max_show_all():
    try:
        return max(PER_PAGE, int(config.get("qa.ui.max_show_all_rows", 500)))
    except (TypeError, ValueError):
        return 500


def paginate(request, queryset_or_list, per_page=PER_PAGE):
    """Return a dict for templates/qa_core/partials/_pagination.html."""
    items = queryset_or_list
    total = items.count() if hasattr(items, "count") and not isinstance(items, list) else len(items)
    limit = max_show_all()
    wants_all = request.GET.get("all") == "1"
    show_all_allowed = total <= limit
    show_all = wants_all and show_all_allowed
    params = request.GET.copy()
    params.pop("page", None)
    params.pop("all", None)
    base_qs = params.urlencode()

    if show_all:
        rows = list(items)
        return {"rows": rows, "total": total, "show_all": True, "show_all_allowed": True,
                "show_all_refused": False, "max_rows": limit, "page": None, "base_qs": base_qs,
                "start": 1 if total else 0, "end": total, "links": [], "num_pages": 1,
                "prev_qs": "", "next_qs": "", "all_qs": querystring(dict(params.items()), all="1")}

    paginator = Paginator(items, per_page)
    try:
        page = paginator.page(request.GET.get("page") or 1)
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        page = paginator.page(paginator.num_pages)
    links = []
    if paginator.num_pages > 1:
        for num in paginator.get_elided_page_range(page.number, on_each_side=1, on_ends=1):
            if num == Paginator.ELLIPSIS:
                links.append({"ellipsis": True})
            else:
                links.append({"num": num, "current": num == page.number,
                              "qs": querystring({k: v for k, v in params.items()}, page=num)})
    prev_qs = querystring(dict(params.items()), page=page.previous_page_number()) if page.has_previous() else ""
    next_qs = querystring(dict(params.items()), page=page.next_page_number()) if page.has_next() else ""
    all_qs = querystring(dict(params.items()), all="1")
    return {
        "links": links,
        "prev_qs": prev_qs,
        "next_qs": next_qs,
        "all_qs": all_qs,
        "num_pages": paginator.num_pages,
        "rows": list(page.object_list),
        "total": total,
        "show_all": False,
        "show_all_allowed": show_all_allowed,
        "show_all_refused": wants_all and not show_all_allowed,
        "max_rows": limit,
        "page": page,
        "base_qs": base_qs,
        "start": page.start_index() if total else 0,
        "end": page.end_index() if total else 0,
    }


def querystring(params, **changes):
    data = {k: v for k, v in params.items() if v not in (None, "")}
    data.update({k: v for k, v in changes.items() if v not in (None, "")})
    for key, value in changes.items():
        if value in (None, ""):
            data.pop(key, None)
    return urlencode(data)


def toast(response, kind, title, body=""):
    """Attach an HX-Trigger that qa.js shows as a toast and announces."""
    payload = {}
    existing = response.get("HX-Trigger")
    if existing:
        try:
            payload = json.loads(existing)
        except ValueError:
            payload = {existing: True}
    payload["qa:toast"] = {"kind": kind, "title": title, "body": body}
    response["HX-Trigger"] = json.dumps(payload)
    return response


def htmx_fragment(parent, anchor=""):
    """Phase 0 (N2, V6): a view that renders only a fragment for HTMX must
    still give a full, styled page to a normal request (deep link, new tab,
    Back after a cache miss, no-JS form post).

    ``parent`` is the URL name (reversed with the view's own arguments) or a
    callable ``(request, *args, **kwargs) -> url`` of the page that hosts the
    fragment. A normal GET is redirected there; a normal POST
    runs the view (so no-JS forms keep working) and then redirects there
    (POST/redirect/GET), with an error message when the view refused the data.
    """
    from functools import wraps

    from django.contrib import messages
    from django.shortcuts import redirect, resolve_url

    def deco(view):
        @wraps(view)
        def inner(request, *args, **kwargs):
            if is_htmx(request):
                return view(request, *args, **kwargs)
            if callable(parent):
                url = parent(request, *args, **kwargs)
            else:
                from django.urls import NoReverseMatch
                try:
                    url = resolve_url(parent, *args, **kwargs)
                except NoReverseMatch:
                    # The host page takes no arguments (e.g. /application/ for
                    # /application/programmes/<pk>/remove/).
                    url = resolve_url(parent)
            url += anchor
            if request.method in ("GET", "HEAD"):
                return redirect(url)
            response = view(request, *args, **kwargs)
            if response.status_code >= 500 or response.status_code in (301, 302, 303):
                return response
            if response.status_code >= 400:
                if response.status_code in (403, 404, 405):
                    return response
                messages.error(request, "Not saved. Please check the form and try again.")
            return redirect(url)
        inner.htmx_fragment_parent = parent
        return inner
    return deco
