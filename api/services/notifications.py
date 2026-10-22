"""notifications/ for both doors: shaping notifications.Notification rows.

Rules come from the notifications app: a user only ever sees their own rows
(``recipient=user``, as the website's list and the portal's page), bulk
actions are ``notifications.services.bulk_apply`` (the website's bulk
endpoint), "kind" is the preference kind of ``notifications.preferences``
(``kind_for(category)``; announcement notifications are "announcements").

``target`` maps the notification's website link to an app screen:
    {"type": "task", "id": 418, "route": "/tasks/418", "url": "https://…/tasks/418/"}
Unknown links -> {"type": "web", "id": null, "route": null, "url": "<absolute>"};
no link -> null.
"""
import re

from django.db.models import Q

# (regex on the notification's relative url, target type, app route template)
# Order matters: the more specific patterns come first.
TARGETS = (
    (r"^/(?:messages|messaging)/memos/(\d+)/", "memo", "/memos/{id}"),
    (r"^/messages/(\d+)/", "thread", "/chats/{id}"),
    (r"^/tasks/(\d+)/", "task", "/tasks/{id}"),
    (r"^/workflow/(\d+)/", "approval", "/approvals/{id}"),
    (r"^/tickets/(\d+)/", "ticket", "/tickets/{id}"),
    (r"^/(?:portal/)?announcements/(\d+)/", "announcement", "/announcements/{id}"),
    (r"^/qa/pending/(\d+)/", "confirmation", "/confirmations/{id}"),
    (r"^/qa/cases/(\d+)/", "case", "/cases/{id}"),
    (r"^/qa/requests/queries/(\d+)/", "query", "/queries/{id}"),
    (r"^/qa/requests/ssq/(\d+)/", "ssq", "/ssq/{id}"),
    (r"^/qa/requests/(\d+)/", "qa_request", "/qa-requests/{id}"),
    (r"^/qa/complaints/(\d+)/", "complaint", "/complaints/{id}"),
    (r"^/qa/finance/queue/(\d+)/", "finance_item", "/finance/queue/{id}"),
    # QA portal links (institutions and resource persons)
    (r"^/qa-requests/\d+/ssq/(\d+)/", "ssq", "/ssq/{id}"),
    (r"^/qa-requests/(\d+)/", "qa_request", "/qa-requests/{id}"),
    (r"^/queries/(\d+)/", "query", "/queries/{id}"),
    (r"^/complaints/(\d+)/", "complaint", "/complaints/{id}"),
    (r"^/payments/(\d+)/", "bill", "/bills/{id}"),
)
_COMPILED = tuple((re.compile(p), t, r) for p, t, r in TARGETS)
NO_ID_TARGETS = {"/application/": ("application", "/application")}
TARGET_TYPES = sorted({t for _p, t, _r in TARGETS} | {"application", "web"})


def _absolute(request, url):
    if not url or url.startswith(("http://", "https://")):
        return url or None
    return request.build_absolute_uri(url) if request is not None else url


def target_for(notification, request=None):
    url = (notification.url or "").strip()
    meta = notification.metadata if isinstance(notification.metadata, dict) else {}
    if not url and meta.get("announcement"):
        url = f"/announcements/{meta['announcement']}/"
    if not url:
        return None
    path = url.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        return {"type": "web", "id": None, "route": None, "url": _absolute(request, url)}
    if not path.endswith("/"):
        path += "/"
    for pattern, kind, route in _COMPILED:
        m = pattern.match(path)
        if m:
            pk = int(m.group(1))
            return {"type": kind, "id": pk, "route": route.format(id=pk), "url": _absolute(request, url)}
    if path in NO_ID_TARGETS:
        kind, route = NO_ID_TARGETS[path]
        return {"type": kind, "id": None, "route": route, "url": _absolute(request, url)}
    return {"type": "web", "id": None, "route": None, "url": _absolute(request, url)}


def kind_of(notification):
    from notifications.preferences import kind_for

    meta = notification.metadata if isinstance(notification.metadata, dict) else {}
    if meta.get("announcement"):
        return "announcements"
    return kind_for(notification.category)


def kind_q(kind):
    """Q for notifications of one preference kind (mirrors kind_of)."""
    from notifications.preferences import CATEGORY_TO_KIND, KIND_FIELDS, kind_for

    if kind not in KIND_FIELDS:
        return Q(pk__in=[])
    announcement = Q(metadata__has_key="announcement")
    categories = [c for c, k in CATEGORY_TO_KIND.items() if k == kind]
    if kind == "general":
        # everything that maps to "general", including unknown categories
        others = [c for c in CATEGORY_TO_KIND if kind_for(c) != "general"]
        return ~Q(category__in=others) & ~announcement
    q = Q(category__in=categories)
    if kind == "announcements":
        return q | announcement
    return q & ~announcement


def mine(user):
    from notifications.models import Notification

    return Notification.objects.filter(recipient=user)


def notification_dict(n, request=None):
    return {
        "id": n.pk,
        "title": n.title,
        "body": n.body,
        "level": n.level,
        "kind": kind_of(n),
        "category": n.category,
        "created_at": n.created_at,
        "read": n.is_read,
        "read_at": n.read_at,
        "actor": ({"id": n.actor_id, "name": n.actor.get_full_name() or n.actor.email}
                  if n.actor_id else None),
        "target": target_for(n, request),
    }


def unread_count(user):
    return mine(user).filter(is_read=False).count()
