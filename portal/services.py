"""Portal services — visibility, lifecycle, and reminder fan-out."""
from __future__ import annotations

from datetime import date, timedelta

from django.db.models import Q
from django.utils import timezone

from .models import (
    Announcement, AnnouncementCategory, AnnouncementLink,
    Award, AwardCategory,
    Document, DocumentCategory, DocumentVersion,
    Event, EventCategory,
    GalleryAlbum, GalleryCategory, GalleryItem,
    Holiday,
    Reminder, ReminderRecipient,
    Scope,
)


# ============================================================================
# SHARED SCOPE HELPER
# ============================================================================
def scope_filter(user):
    """The Q object matching every portal record `user` is allowed to see.

    Rules:
      - Anonymous       → PUBLIC only
      - Superuser       → everything
      - Signed-in staff → PUBLIC + STAFF + own department + own roles +
                          personally-targeted
    """
    if user is None or not user.is_authenticated:
        return Q(visibility=Scope.PUBLIC)

    if user.is_superuser:
        return Q()

    q = Q(visibility=Scope.PUBLIC) | Q(visibility=Scope.STAFF)

    if getattr(user, "department_id", None):
        q |= Q(visibility=Scope.DEPARTMENT, department_id=user.department_id)

    group_ids = list(user.groups.values_list("pk", flat=True))
    if group_ids:
        q |= Q(visibility=Scope.ROLE, roles__in=group_ids)

    q |= Q(visibility=Scope.INDIVIDUAL, recipients=user)

    return q


# ============================================================================
# ANNOUNCEMENTS
# ============================================================================
def live_announcements(*, user=None, include_pinned_first=True):
    now = timezone.now()
    qs = (
        Announcement.objects
        .filter(is_draft=False, archived_at__isnull=True, publish_at__lte=now)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .filter(scope_filter(user))
        .select_related("category", "author", "department")
        .prefetch_related("links")
        .distinct()
    )
    if include_pinned_first:
        qs = qs.order_by("-is_pinned", "-publish_at", "-pk")
    return qs


def announcements_by_author(user, author):
    """All announcements authored by `author`, if `user` may see them."""
    if not user or not user.is_authenticated or author is None:
        return Announcement.objects.none()
    if author.pk != user.pk and not (
        user.is_superuser or user.has_perm("portal.change_announcement")
    ):
        return Announcement.objects.none()
    return (
        Announcement.objects
        .filter(author=author, archived_at__isnull=True)
        .select_related("category", "author", "department")
        .prefetch_related("links")
        .order_by("-is_pinned", "-publish_at", "-pk")
    )


def drafts_visible_to(user):
    """Drafts this user can see. Authors see their own; superusers and
    `portal.change_announcement` holders see everyone's."""
    if not user or not user.is_authenticated:
        return Announcement.objects.none()
    qs = (
        Announcement.objects
        .filter(is_draft=True, archived_at__isnull=True)
        .select_related("category", "author", "department")
        .prefetch_related("links")
    )
    if user.is_superuser or user.has_perm("portal.change_announcement"):
        return qs.order_by("-publish_at", "-pk")
    return qs.filter(author=user).order_by("-publish_at", "-pk")


def is_announcement_visible_to(ann, user) -> bool:
    if ann is None or ann.archived_at:
        return False
    if user and user.is_authenticated and ann.author_id == user.pk:
        return True
    if user and user.is_authenticated and user.is_superuser:
        return True
    if ann.is_draft:
        return False
    now = timezone.now()
    if ann.publish_at > now:
        return False
    if ann.expires_at and ann.expires_at <= now:
        return False

    if ann.visibility == Scope.PUBLIC:
        return True
    if not user or not user.is_authenticated:
        return False
    if ann.visibility == Scope.STAFF:
        return True
    if ann.visibility == Scope.DEPARTMENT:
        return ann.department_id and ann.department_id == getattr(user, "department_id", None)
    if ann.visibility == Scope.ROLE:
        return ann.roles.filter(pk__in=user.groups.values_list("pk", flat=True)).exists()
    if ann.visibility == Scope.INDIVIDUAL:
        return ann.recipients.filter(pk=user.pk).exists()
    return False


def dashboard_announcements(user, limit=3):
    return live_announcements(user=user)[:limit]


# ============================================================================
# EVENTS
# ============================================================================
def live_events(*, user=None, when=None):
    now = timezone.now()
    qs = (
        Event.objects
        .filter(is_draft=False)
        .filter(scope_filter(user))
        .select_related("category", "organizer", "department")
        .distinct()
    )
    if when == "upcoming":
        qs = qs.filter(starts_at__gt=now).order_by("starts_at")
    elif when == "ongoing":
        qs = qs.filter(
            Q(starts_at__lte=now) &
            (Q(ends_at__gte=now) |
             Q(ends_at__isnull=True,
               starts_at__gte=now - timedelta(hours=2)))
        ).order_by("starts_at")
    elif when == "past":
        qs = qs.filter(
            Q(ends_at__lt=now) |
            Q(ends_at__isnull=True,
              starts_at__lt=now - timedelta(hours=2))
        ).order_by("-starts_at")
    else:
        qs = qs.order_by("starts_at")
    return qs


def events_by_organizer(user, organizer):
    """All events organized by `organizer`, if `user` may see them."""
    if not user or not user.is_authenticated or organizer is None:
        return Event.objects.none()
    if organizer.pk != user.pk and not (
        user.is_superuser or user.has_perm("portal.change_event")
    ):
        return Event.objects.none()
    return (
        Event.objects
        .filter(organizer=organizer)
        .select_related("category", "organizer", "department")
        .order_by("-starts_at")
    )


def draft_events_visible_to(user):
    """Draft events this user can see."""
    if not user or not user.is_authenticated:
        return Event.objects.none()
    qs = (
        Event.objects
        .filter(is_draft=True)
        .select_related("category", "organizer", "department")
    )
    if user.is_superuser or user.has_perm("portal.change_event"):
        return qs.order_by("-starts_at")
    return qs.filter(organizer=user).order_by("-starts_at")


def is_event_visible_to(ev, user) -> bool:
    if ev is None:
        return False
    if user and user.is_authenticated and ev.organizer_id == user.pk:
        return True
    if user and user.is_authenticated and user.is_superuser:
        return True
    if ev.is_draft:
        return False

    if ev.visibility == Scope.PUBLIC:
        return True
    if not user or not user.is_authenticated:
        return False
    if ev.visibility == Scope.STAFF:
        return True
    if ev.visibility == Scope.DEPARTMENT:
        return ev.department_id and ev.department_id == getattr(user, "department_id", None)
    if ev.visibility == Scope.ROLE:
        return ev.roles.filter(pk__in=user.groups.values_list("pk", flat=True)).exists()
    if ev.visibility == Scope.INDIVIDUAL:
        return ev.recipients.filter(pk=user.pk).exists()
    return False


def dashboard_events(user, limit=3):
    return live_events(user=user, when="upcoming")[:limit]


# ============================================================================
# REMINDERS
# ============================================================================
def visible_reminders(*, user):
    if user is None or not user.is_authenticated:
        return Reminder.objects.none()
    now = timezone.now()

    personal = Q(kind=Reminder.Kind.PERSONAL, creator=user)
    org = Q(kind=Reminder.Kind.ORGANISATION)

    dept = Q(kind=Reminder.Kind.DEPARTMENT)
    if getattr(user, "department_id", None):
        dept &= Q(department_id=user.department_id)
    else:
        dept &= Q(pk__in=[])

    custom = Q(kind=Reminder.Kind.CUSTOM)
    group_ids = list(user.groups.values_list("pk", flat=True))
    if group_ids:
        custom &= (Q(recipients=user) | Q(roles__in=group_ids))
    else:
        custom &= Q(recipients=user)

    scope_q = personal | org | dept | custom

    qs = (
        Reminder.objects
        .filter(scope_q)
        .filter(
            Q(remind_at__lte=now),
            Q(expires_at__isnull=True) | Q(expires_at__gt=now),
            is_dismissed=False,
        )
        .select_related("creator", "department", "related_event",
                        "related_task", "related_announcement")
        .distinct()
        .order_by("-remind_at")
    )
    return qs


def dashboard_reminders(user, limit=3):
    return visible_reminders(user=user)[:limit]


def expand_reminder_recipients(reminder: Reminder):
    from django.contrib.auth import get_user_model
    User = get_user_model()
    k = reminder.kind

    if k == Reminder.Kind.PERSONAL:
        return User.objects.filter(pk=reminder.creator_id, is_active=True)
    if k == Reminder.Kind.ORGANISATION:
        return User.objects.filter(is_active=True)
    if k == Reminder.Kind.DEPARTMENT and reminder.department_id:
        return User.objects.filter(is_active=True, department_id=reminder.department_id)

    q = Q(pk__in=reminder.recipients.values_list("pk", flat=True))
    group_ids = list(reminder.roles.values_list("pk", flat=True))
    if group_ids:
        q |= Q(groups__in=group_ids)
    return User.objects.filter(q, is_active=True).distinct()


def fire_reminder(reminder: Reminder):
    from notifications.models import Notification
    from notifications.services import notify

    users = expand_reminder_recipients(reminder)
    now = timezone.now()
    created = 0

    for u in users:
        _, made = ReminderRecipient.objects.get_or_create(
            reminder=reminder, user=u, defaults={"delivered_at": now},
        )
        if made:
            created += 1
            try:
                notify(
                    recipient=u,
                    title=reminder.title,
                    body=reminder.body[:400] if reminder.body else "",
                    url=reminder.url or "",
                    category=Notification.SYSTEM,
                    level=Notification.INFO,
                    actor=reminder.creator,
                )
            except Exception:
                pass

    reminder.is_fired = True
    reminder.fired_at = now
    reminder.save(update_fields=["is_fired", "fired_at"])
    return created


# ============================================================================
# DOCUMENTS
# ============================================================================
def published_documents(*, user=None):
    qs = (
        Document.objects
        .filter(status=Document.STATUS_PUBLISHED, deleted_at__isnull=True)
        .select_related("category", "owner", "department", "current_version")
        .distinct()
    )
    if user and user.is_authenticated:
        qs = qs.filter(scope_filter(user))
    else:
        qs = qs.filter(visibility=Scope.PUBLIC)
    return qs.order_by("-is_featured", "-published_at", "-pk")


def drafts_visible_to_documents(user):
    if not user or not user.is_authenticated:
        return Document.objects.none()
    qs = Document.objects.filter(
        status=Document.STATUS_DRAFT, deleted_at__isnull=True,
    )
    if user.is_superuser or user.has_perm("portal.change_document"):
        return qs.order_by("-updated_at")
    return qs.filter(owner=user).order_by("-updated_at")


def archived_documents(user):
    if not user or not user.is_authenticated:
        return Document.objects.none()
    qs = Document.objects.filter(
        status=Document.STATUS_ARCHIVED, deleted_at__isnull=True,
    )
    if user.is_superuser or user.has_perm("portal.change_document"):
        return qs.order_by("-updated_at")
    return qs.filter(owner=user).order_by("-updated_at")


def documents_owned_by(user, owner):
    if not user or not user.is_authenticated or owner is None:
        return Document.objects.none()
    if owner.pk != user.pk and not (
        user.is_superuser or user.has_perm("portal.change_document")
    ):
        return Document.objects.none()
    return (
        Document.objects
        .filter(owner=owner, deleted_at__isnull=True)
        .order_by("-updated_at")
    )


def is_document_visible_to(doc, user) -> bool:
    if doc is None or doc.deleted_at:
        return False
    if user and user.is_authenticated:
        if doc.owner_id == user.pk or user.is_superuser:
            return True
        if user.has_perm("portal.change_document"):
            return True
    if doc.status != doc.STATUS_PUBLISHED:
        return False
    if doc.visibility == Scope.PUBLIC:
        return True
    if not user or not user.is_authenticated:
        return False
    if doc.visibility == Scope.STAFF:
        return True
    if doc.visibility == Scope.DEPARTMENT:
        return doc.department_id and doc.department_id == getattr(user, "department_id", None)
    if doc.visibility == Scope.ROLE:
        return doc.roles.filter(pk__in=user.groups.values_list("pk", flat=True)).exists()
    if doc.visibility == Scope.INDIVIDUAL:
        return doc.recipients.filter(pk=user.pk).exists()
    return False


def dashboard_documents(user, limit=3):
    if user:
        return published_documents(user=user).filter(is_featured=False)[:limit]
    return published_documents(user=None)[:limit]


def public_documents(limit=None):
    qs = published_documents(user=None)
    return qs[:limit] if limit else qs


def featured_public_documents(limit=3):
    return published_documents(user=None).filter(is_featured=True)[:limit]


# ============================================================================
# AWARDS
# ============================================================================
def live_awards(*, user=None, kind=None, featured_only=False):
    base = Award.objects.select_related(
        "recipient", "recipient__department", "category", "awarded_by",
    )
    if user and user.is_superuser:
        scoped = base
    elif user is None or not user.is_authenticated:
        scoped = base.filter(visibility=Scope.PUBLIC)
    else:
        scoped = base.filter(scope_filter(user)).distinct()

    if kind:
        scoped = scoped.filter(kind=kind)
    if featured_only:
        scoped = scoped.filter(is_featured=True, visibility=Scope.PUBLIC)
    return scoped.order_by("-awarded_on", "-pk")


def awards_for_staff(staff, *, user=None):
    return live_awards(user=user).filter(recipient=staff)


# ============================================================================
# HOLIDAYS & ABSENCE
# ============================================================================
def live_holidays(*, user=None, from_date=None, to_date=None):
    today = timezone.localdate()
    from_date = from_date or date(today.year, 1, 1)
    to_date   = to_date   or date(today.year, 12, 31)

    base = Holiday.objects.select_related("department")
    if user and user.is_superuser:
        scoped = base
    elif user is None or not user.is_authenticated:
        scoped = base.filter(visibility=Scope.PUBLIC)
    else:
        scoped = base.filter(scope_filter(user)).distinct()

    scoped = scoped.filter(date_start__lte=to_date, date_end__gte=from_date)
    return scoped.order_by("date_start", "name")


def upcoming_holidays(*, user=None, limit=5):
    today = timezone.localdate()
    return live_holidays(user=user, from_date=today)[:limit]


def absent_staff_today(*, user=None):
    """Staff currently 'absent' today, derived from state + leave + attendance."""
    from staff.models import Staff

    today = timezone.localdate()

    if user and user.is_superuser:
        staff_qs = Staff.objects.filter(is_deleted=False)
    elif user and getattr(user, "department_id", None):
        staff_qs = Staff.objects.filter(
            is_deleted=False, department_id=user.department_id,
        )
    else:
        return Staff.objects.none()

    ids = set()

    # 1. Staff state flagged as counts_as_absent
    ids.update(
        staff_qs.filter(state__counts_as_absent=True)
        .values_list("pk", flat=True)
    )

    # 2. Open leave today
    try:
        from workflow.models import Request as _Req
        leave_user_ids = _Req.objects.filter(
            kind="leave",
            status__in=["approved", "in_progress", "approved_in_progress"],
            start_date__lte=today,
            end_date__gte=today,
        ).values_list("requester__staff_profile__pk", flat=True)
        ids.update(x for x in leave_user_ids if x)
    except Exception:
        pass

    # 3. Attendance marked absent today
    try:
        from attendance.models import AttendanceRecord
        att_ids = AttendanceRecord.objects.filter(
            date=today, status="absent",
        ).values_list("user__staff_profile__pk", flat=True)
        ids.update(x for x in att_ids if x)
    except Exception:
        pass

    return (
        staff_qs
        .filter(pk__in=ids)
        .select_related("department", "state")
        .order_by("last_name", "first_name")
    )


# ============================================================================
# GALLERY
# ============================================================================
def live_albums(*, user=None, category=None, featured_only=False, q=None):
    base = (
        GalleryAlbum.objects
        .select_related("category", "owner", "department")
        .prefetch_related("items")
    )

    if user and user.is_superuser:
        scoped = base
    elif user is None or not user.is_authenticated:
        scoped = base.filter(visibility=Scope.PUBLIC, is_published=True)
    else:
        vis_q = (
            Q(visibility=Scope.PUBLIC, is_published=True)
            | Q(visibility=Scope.STAFF, is_published=True)
            | Q(owner=user)
        )
        if getattr(user, "department_id", None):
            vis_q |= Q(visibility=Scope.DEPARTMENT,
                       department_id=user.department_id, is_published=True)
        group_ids = list(user.groups.values_list("pk", flat=True))
        if group_ids:
            vis_q |= Q(visibility=Scope.ROLE, roles__in=group_ids, is_published=True)
        vis_q |= Q(visibility=Scope.INDIVIDUAL, recipients=user, is_published=True)
        scoped = base.filter(vis_q).distinct()

    if category:
        scoped = scoped.filter(category__slug=category)
    if featured_only:
        scoped = scoped.filter(is_featured=True, visibility=Scope.PUBLIC)
    if q:
        scoped = scoped.filter(Q(title__icontains=q) | Q(description__icontains=q))
    return scoped.order_by("-is_featured", "-event_date", "-created_at", "-pk")


def featured_albums(limit=6, user=None):
    return live_albums(user=user, featured_only=True)[:limit]
