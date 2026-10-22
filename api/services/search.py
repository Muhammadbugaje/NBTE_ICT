"""search/ (staff door) - the website's global search (core.search) shaped
for the app.

Scoping is the website's: every group comes from a ``core.search`` provider,
which calls the same visibility helper as the destination list page (tasks,
tickets, requests, staff, assets, documents, memos, chats, attendance). Two
groups the website's bar does not have are added with the website's own
scoping, so the app can open its own screens:

* ``announcements``  announcements.Announcement.objects.visible_to(user)
                     (the /announcements/ page; the website bar searches the
                     older portal notices, kept here as ``notices``)
* ``institutions``   qa_core.permissions.can_view_institutions +
                     Institution.objects.for_user + institutions.services
                     .filter_institutions (the QA Institutions list)

Each result carries a ``target`` in the notification target format
(api/services/notifications.target_for) plus ``web_url``, the website path.
"""
from types import SimpleNamespace

from django.urls import NoReverseMatch, reverse

from .notifications import target_for

MIN_QUERY = 2
MAX_LIMIT = 10
DEFAULT_LIMIT = 5

# core.search provider type -> (app kind, label). Order = order of the groups.
KINDS = (
    ("staff", "people", "People"),
    ("memo", "memos", "Memos"),
    ("task", "tasks", "Tasks"),
    ("ticket", "tickets", "Tickets"),
    ("announcement_app", "announcements", "Announcements"),
    ("request", "requests", "Requests"),
    ("institution", "institutions", "Institutions"),
    ("message", "chats", "Chats"),
    ("asset", "assets", "Assets"),
    ("document", "documents", "Documents"),
    ("announcement", "notices", "Portal notices"),
    ("attendance", "attendance", "Attendance"),
)
KIND_CHOICES = [k for _t, k, _l in KINDS]


def _abs(request, path):
    if not path:
        return None
    return request.build_absolute_uri(path) if request is not None else path


def _target(request, website_type, row):
    """Notification-format target for one core.search row."""
    url = row.get("url") or ""
    pk = row.get("id")
    if website_type == "staff":
        return {"type": "staff", "id": pk, "route": f"/staff/{pk}", "url": _abs(request, url)}
    if website_type == "asset":
        tag = row.get("tag") or ""
        return {"type": "asset", "id": pk, "route": f"/assets/{tag}" if tag else None, "url": _abs(request, url)}
    if website_type == "announcement":  # portal notices have no app screen
        return {"type": "web", "id": None, "route": None, "url": _abs(request, url)} if url else None
    return target_for(SimpleNamespace(url=url, metadata={}), request)


def _website_groups(user, query, limit):
    from core import search as core_search

    payload = core_search.search(user, query, limit=limit * len(core_search.PROVIDERS), per_type=limit)
    return {g["type"]: g["results"] for g in payload["groups"]}


def _announcements(user, query, limit):
    from django.db.models import Q

    from announcements.models import Announcement

    qs = (Announcement.objects.visible_to(user).filter(Q(title__icontains=query) | Q(body__icontains=query))
          .order_by("-pinned", "-published_at")[:limit])
    out = []
    for a in qs:
        out.append({"id": a.pk, "title": a.title, "url": f"/announcements/{a.pk}/",
                    "meta": " · ".join(filter(None, [a.get_kind_display(),
                                                     a.published_at.strftime("%b %d, %Y") if a.published_at
                                                     else ""])),
                    "badge": "Pinned" if a.pinned else ""})
    return out


def _institutions(user, query, limit):
    from institutions import services as isvc
    from institutions.models import Institution
    from qa_core.permissions import can_view_institutions

    if not can_view_institutions(user):
        return []
    qs = isvc.filter_institutions(Institution.objects.for_user(user).select_related("state"), q=query)[:limit]
    out = []
    for inst in qs:
        try:
            url = reverse("qa_inst:detail", args=[inst.code])
        except NoReverseMatch:
            url = ""
        out.append({"id": inst.pk, "code": inst.code, "title": inst.name, "url": url,
                    "meta": " · ".join(filter(None, [inst.code, inst.state.name if inst.state_id else ""])),
                    "badge": str(inst.status_label)})
    return out


def _asset_tags(rows):
    from assets.models import Asset

    tags = dict(Asset.objects.filter(pk__in=[r["id"] for r in rows]).values_list("pk", "asset_tag"))
    for r in rows:
        r["tag"] = tags.get(r["id"], "")
    return rows


def search(user, query, limit, request=None):
    import logging

    logger = logging.getLogger("api")
    query = (query or "").strip()
    raw = _website_groups(user, query, limit)
    for key, fn in (("announcement_app", _announcements), ("institution", _institutions)):
        try:
            raw[key] = fn(user, query, limit)
        except Exception:  # a broken group never breaks the search (as core.search)
            logger.exception("search/: %s failed", key)
            raw[key] = []
    if raw.get("asset"):
        raw["asset"] = _asset_tags(raw["asset"])

    groups = []
    for website_type, kind, label in KINDS:
        rows = raw.get(website_type) or []
        if not rows:
            continue
        results = []
        for row in rows[:limit]:
            target = _target(request, website_type, row)
            if website_type == "institution":
                target = {"type": "institution", "id": row["id"], "route": f"/qa/institutions/{row['code']}",
                          "url": _abs(request, row["url"])}
            results.append({"id": str(row["id"]), "title": row.get("title") or "",
                            "subtitle": row.get("meta") or "", "badge": row.get("badge") or None,
                            "target": target, "web_url": row.get("url") or None})
        groups.append({"kind": kind, "label": label, "count": len(results), "results": results})
    return {"query": query, "total": sum(g["count"] for g in groups), "groups": groups}
