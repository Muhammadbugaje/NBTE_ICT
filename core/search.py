"""Global search — a small, extensible provider registry.

Each provider knows how to search ONE model type. The view calls
`search(user, query, types=..., limit=...)` and gets back grouped results.

Permissions are NOT reimplemented here. Every provider calls the SAME
scoping function its list view calls, so the search can never return a
row the user couldn't open themselves. If a destination view tightens
its scoping tomorrow, the search tightens with it — for free.

Adding a new searchable model:
    1. Write a class with `type`, `label`, `icon`, `search(user, query, limit)`.
    2. Append it to PROVIDERS below.
Nothing else changes — the view, URL, template, and JS are generic.
"""
from __future__ import annotations

import logging

from django.db.models import Q
from django.urls import NoReverseMatch, reverse

logger = logging.getLogger(__name__)


# ===========================================================================
# Base
# ===========================================================================
class BaseSearchProvider:
    """Subclass me. Each provider searches exactly one model type."""

    type = ""           # machine name, e.g. "task"
    label = ""          # human label, e.g. "Tasks"
    icon = "search"     # icon key the frontend uses
    min_query = 2       # minimum query length to run this provider

    def search(self, user, query, limit):
        """Return a list of result dicts. Subclasses MUST override."""
        raise NotImplementedError

    # -----------------------------------------------------------------
    # Subclass helpers
    # -----------------------------------------------------------------
    @staticmethod
    def _clip(text, length=120):
        if not text:
            return ""
        text = " ".join(str(text).split())
        return text if len(text) <= length else text[: length - 1] + "…"

    @staticmethod
    def _url(name, *args):
        try:
            return reverse(name, args=args)
        except NoReverseMatch:
            return ""


# ===========================================================================
# Tasks
# ===========================================================================
class TaskSearchProvider(BaseSearchProvider):
    type = "task"
    label = "Tasks"
    icon = "check-square"

    def search(self, user, query, limit):
        from tasks.models import Task
        from tasks.views import _visible_tasks_qs

        qs = _visible_tasks_qs(user).filter(
            Q(reference__icontains=query)
            | Q(title__icontains=query)
            | Q(description__icontains=query)
        ).order_by("-updated_at")[:limit]

        out = []
        for task in qs:
            out.append({
                "id": task.pk,
                "title": task.title,
                "url": self._url("tasks:detail", task.pk),
                "meta": f"{task.reference} · {task.get_state_display()}",
                "badge": task.get_priority_display() if task.priority != Task.NORMAL else "",
                "snippet": self._clip(task.description),
            })
        return out


# ===========================================================================
# Tickets
# ===========================================================================
class TicketSearchProvider(BaseSearchProvider):
    type = "ticket"
    label = "Tickets"
    icon = "life-buoy"

    def search(self, user, query, limit):
        from tickets.models import Ticket
        from tickets.views import _visible_tickets_qs

        qs = _visible_tickets_qs(user).filter(
            Q(reference__icontains=query)
            | Q(subject__icontains=query)
            | Q(body__icontains=query)
            | Q(current_location__icontains=query)
        ).order_by("-updated_at")[:limit]

        out = []
        for t in qs:
            out.append({
                "id": t.pk,
                "title": t.subject,
                "url": self._url("tickets:detail", t.pk),
                "meta": f"{t.reference} · {t.get_state_display()}",
                "badge": t.get_priority_display() if t.priority != Ticket.NORMAL else "",
                "snippet": self._clip(t.body),
            })
        return out


# ===========================================================================
# Workflow requests
# ===========================================================================
class RequestSearchProvider(BaseSearchProvider):
    type = "request"
    label = "Requests"
    icon = "file-text"

    def search(self, user, query, limit):
        from workflow.models import Request

        base = Request.objects.select_related(
            "requester", "request_type", "receiving_department",
        )

        if user.is_superuser:
            qs = base
        else:
            # Mirrors workflow.views._can_view's queryset-shaped subset:
            # requester, current assignee, anyone who has acted, or the
            # receiving department's director. The runtime "current step
            # approver" check is not replicated here — those users will
            # still find the request via their approval history.
            q = Q(requester=user) | Q(assigned_to=user) | Q(approvals__actor=user)
            if (
                user.department_id
                and user.role_id
                and user.role.code == "director"
            ):
                q |= Q(receiving_department_id=user.department_id)
            qs = base.filter(q).distinct()

        qs = qs.filter(
            Q(reference__icontains=query)
            | Q(subject__icontains=query)
            | Q(body__icontains=query)
        ).order_by("-updated_at")[:limit]

        out = []
        for req in qs:
            out.append({
                "id": req.pk,
                "title": req.subject,
                "url": self._url("workflow:detail", req.pk),
                "meta": f"{req.reference} · {req.get_status_display()}",
                "badge": "",
                "snippet": self._clip(req.body),
            })
        return out


# ===========================================================================
# Staff directory
# ===========================================================================
class StaffSearchProvider(BaseSearchProvider):
    type = "staff"
    label = "Staff"
    icon = "users"

    def search(self, user, query, limit):
        try:
            from staff import permissions
        except Exception:
            logger.exception("Staff search: could not import staff.permissions")
            return []

        try:
            qs = permissions.staff_visible_to(user)
        except Exception:
            logger.exception("Staff search: staff_visible_to() failed")
            return []

        qs = qs.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(employee_no__icontains=query)
            | Q(email__icontains=query)
            | Q(designation__icontains=query)
        ).order_by("last_name", "first_name")[:limit]

        out = []
        for s in qs:
            out.append({
                "id": s.pk,
                "title": s.full_name,
                "url": self._url("staff:detail", s.pk),
                "meta": " · ".join(filter(None, [
                    s.employee_no,
                    s.designation,
                    s.department.name if s.department_id else "",
                ])),
                "badge": "",
                "snippet": "",
            })
        return out


# ===========================================================================
# Assets
# ===========================================================================
class AssetSearchProvider(BaseSearchProvider):
    type = "asset"
    label = "Assets"
    icon = "box"

    def search(self, user, query, limit):
        try:
            from assets.views import _visible_assets_qs
        except Exception:
            logger.exception("Asset search: could not import visibility helper")
            return []

        qs = _visible_assets_qs(user).filter(
            Q(asset_tag__icontains=query)
            | Q(serial_number__icontains=query)
            | Q(brand__icontains=query)
            | Q(model__icontains=query)
            | Q(description__icontains=query)
            | Q(current_location__icontains=query)
        ).order_by("asset_tag")[:limit]

        out = []
        for a in qs:
            out.append({
                "id": a.pk,
                "title": a.display_name,
                "url": self._url("assets:detail", a.asset_tag),
                "meta": f"{a.asset_tag} · {a.get_state_display()}",
                "badge": a.category.name if a.category_id else "",
                "snippet": self._clip(a.current_location),
            })
        return out


# ===========================================================================
# Portal documents
# ===========================================================================
class DocumentSearchProvider(BaseSearchProvider):
    type = "document"
    label = "Documents"
    icon = "folder"

    def search(self, user, query, limit):
        from portal import services as portal_services

        qs = portal_services.published_documents(user=user).filter(
            Q(title__icontains=query)
            | Q(summary__icontains=query)
            | Q(description__icontains=query)
        )[:limit]

        out = []
        for doc in qs:
            out.append({
                "id": doc.pk,
                "title": doc.title,
                "url": self._url("portal:document_detail", doc.pk),
                "meta": " · ".join(filter(None, [
                    doc.category.name if doc.category_id else "",
                    f"v{doc.current_version_number}" if doc.current_version_number else "",
                ])),
                "badge": "",
                "snippet": self._clip(doc.summary or doc.description),
            })
        return out


# ===========================================================================
# Portal announcements
# ===========================================================================
class AnnouncementSearchProvider(BaseSearchProvider):
    type = "announcement"
    label = "Announcements"
    icon = "megaphone"

    def search(self, user, query, limit):
        from portal import services as portal_services

        qs = portal_services.live_announcements(user=user).filter(
            Q(title__icontains=query)
            | Q(summary__icontains=query)
            | Q(body__icontains=query)
        )[:limit]

        out = []
        for ann in qs:
            out.append({
                "id": ann.pk,
                "title": ann.title,
                "url": self._url("portal:announcement_detail", ann.pk),
                "meta": " · ".join(filter(None, [
                    ann.category.name if ann.category_id else "",
                    ann.publish_at.strftime("%b %d, %Y") if ann.publish_at else "",
                ])),
                "badge": (
                    ann.get_priority_display()
                    if ann.priority != ann.PRIORITY_NORMAL else ""
                ),
                "snippet": self._clip(ann.summary or ann.body),
            })
        return out


# ===========================================================================
# Messaging — threads (metadata-only for non-participant superusers)
# ===========================================================================
class MessageSearchProvider(BaseSearchProvider):
    """Search the messaging app.

    Two modes:

      Participants  — search their own threads. Match subject, message
                      body, and participant names. Show a snippet of the
                      matching message.

      Superusers    — search every thread, but METADATA ONLY. Match on
                      subject and participant names only (never on
                      message body), and never return a snippet. This
                      lets a superuser know a conversation happened
                      without reading it. Clicking through is blocked
                      at the detail view with a friendly message.

    This keeps the privacy model honest: search cannot become a side
    channel for reading messages you aren't a participant of.
    """
    type = "message"
    label = "Messages"
    icon = "message-circle"

    def search(self, user, query, limit):
        from messaging.models import Thread

        is_metadata_reader = user.is_superuser

        if is_metadata_reader:
            base = Thread.objects.all()
        else:
            base = Thread.objects.filter(
                participants__user=user,
                participants__is_deleted=False,
            ).distinct()

        # Everyone can match on subject + participant names. These are
        # metadata, visible to any authenticated user who could find
        # the thread in the first place.
        common_q = (
            Q(subject__icontains=query)
            | Q(participants__user__first_name__icontains=query)
            | Q(participants__user__last_name__icontains=query)
            | Q(participants__user__email__icontains=query)
        )

        if is_metadata_reader:
            # Superusers: metadata only. NO message-body matching —
            # otherwise "search for password" becomes a content oracle.
            matched = (
                base.filter(common_q)
                .distinct()
                .order_by("-last_message_at", "-updated_at")[:limit]
            )
        else:
            # Participants: they can search their own message content.
            matched = (
                base.filter(common_q | Q(messages__body__icontains=query))
                .distinct()
                .order_by("-last_message_at", "-updated_at")[:limit]
            )

        out = []
        for thread in matched:
            try:
                title = thread.display_title_for(user)
            except Exception:
                title = thread.display_title()

            msg_count = thread.messages.filter(
                is_deleted_for_everyone=False,
            ).count()
            plural = "" if msg_count == 1 else "s"

            if is_metadata_reader:
                # Metadata only. Empty snippet + "private" marker so
                # the superuser knows the content is gated.
                out.append({
                    "id": thread.pk,
                    "title": title,
                    "url": self._url("messaging:thread", thread.pk),
                    "meta": f"{msg_count} message{plural} · private",
                    "badge": "",
                    "snippet": "",
                })
            else:
                # Participant view: show a real snippet.
                best = (
                    thread.messages
                    .filter(
                        is_deleted_for_everyone=False,
                        body__icontains=query,
                    )
                    .order_by("-sent_at")
                    .first()
                )
                if best is None:
                    best = thread.last_message()

                out.append({
                    "id": thread.pk,
                    "title": title,
                    "url": self._url("messaging:thread", thread.pk),
                    "meta": f"{msg_count} message{plural}",
                    "badge": "",
                    "snippet": self._clip(best.body) if best else "",
                })
        return out


# ===========================================================================
# Attendance records
# ===========================================================================
class AttendanceSearchProvider(BaseSearchProvider):
    """Search attendance records.

    Visibility mirrors attendance.views:
      - Superuser / Super Admin / ES / accounts.access_all_departments
        → every record, every department
      - Director / deputy / office head  → records of users in their dept
      - Everyone else                    → only their own records

    Match: user first name, last name, email, or status keyword
    ("present", "late", "absent", "on_leave").
    """
    type = "attendance"
    label = "Attendance"
    icon = "calendar"

    def search(self, user, query, limit):
        from attendance.models import AttendanceRecord
        from attendance.views import _can_view_all_attendance, _is_director

        if user.is_superuser or _can_view_all_attendance(user):
            base = AttendanceRecord.objects.all()
        elif _is_director(user) and user.department_id:
            base = AttendanceRecord.objects.filter(
                user__department_id=user.department_id,
            )
        else:
            base = AttendanceRecord.objects.filter(user=user)

        qs = (
            base.select_related("user", "user__department")
            .filter(
                Q(user__first_name__icontains=query)
                | Q(user__last_name__icontains=query)
                | Q(user__email__icontains=query)
                | Q(status__icontains=query)
            )
            .order_by("-date", "user__first_name")[:limit]
        )

        out = []
        for rec in qs:
            name = rec.user.get_full_name() or rec.user.email
            dept = rec.user.department.name if rec.user.department_id else ""
            meta_bits = [rec.date.strftime("%b %d, %Y")]
            if dept:
                meta_bits.append(dept)
            if rec.source:
                meta_bits.append(rec.get_source_display())

            out.append({
                "id": rec.pk,
                "title": f"{name} — {rec.get_status_display()}",
                "url": self._url("attendance:department")
                       + f"?year={rec.date.year}&month={rec.date.month}",
                "meta": " · ".join(meta_bits),
                "badge": rec.get_status_display(),
                "snippet": self._clip(rec.notes),
            })
        return out


# ===========================================================================
# Registry
# ===========================================================================
PROVIDERS = [
    TaskSearchProvider(),
    TicketSearchProvider(),
    RequestSearchProvider(),
    StaffSearchProvider(),
    AssetSearchProvider(),
    DocumentSearchProvider(),
    AnnouncementSearchProvider(),
    MessageSearchProvider(),
    AttendanceSearchProvider(),
]

PROVIDER_BY_TYPE = {p.type: p for p in PROVIDERS}
DEFAULT_TYPES = [p.type for p in PROVIDERS]


def search(user, query, *, types=None, limit=5, per_type=None):
    """Run the search across every requested provider.

    `types`    — iterable of provider type strings, or None for all.
    `limit`    — overall cap on results.
    `per_type` — cap per provider (defaults to `limit`).

    Returns:
        {
            "query": str,
            "total": int,
            "groups": [
                {"type": ..., "label": ..., "icon": ...,
                 "results": [ {id, title, url, meta, badge, snippet}, ... ]},
            ],
        }
    """
    query = (query or "").strip()
    out = {"query": query, "total": 0, "groups": []}

    if not query:
        return out

    wanted = set(types) if types else set(DEFAULT_TYPES)
    per = per_type if per_type is not None else limit
    total_budget = limit

    for provider in PROVIDERS:
        if provider.type not in wanted:
            continue
        if len(query) < provider.min_query:
            continue
        if total_budget <= 0:
            break

        try:
            results = provider.search(user, query, min(per, total_budget))
        except Exception:
            logger.exception("Search provider %s failed", provider.type)
            results = []

        if not results:
            continue

        total_budget -= len(results)
        out["total"] += len(results)
        out["groups"].append({
            "type": provider.type,
            "label": provider.label,
            "icon": provider.icon,
            "results": results,
        })

    return out