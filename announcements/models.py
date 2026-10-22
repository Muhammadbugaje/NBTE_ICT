"""Official announcements (Phase 9).

A one-way channel ("NBTE Announcements"): authorised posters publish notices
to all staff, one department, or a set of roles. Staff read them, acknowledge
them when asked to, and vote in an optional poll. Posters see who has and
has not acknowledged.

Design notes:
- `published_at` is the publish switch: NULL means draft. Drafts are never
  visible to the audience.
- Only internal staff (`user_type == "staff"`) are ever in an audience;
  institution users and resource persons never see announcements.
- Polls are generic: `AnnouncementPoll.announcement` is nullable so a poll can
  later hang off something else (e.g. a chat message) without changing these
  tables.
- Business logic lives in `services.py` so a future API can reuse it.
"""
from django.conf import settings
from django.db import models
from django.db.models import Exists, OuterRef, Q
from django.urls import reverse
from django.utils import timezone

from core.models import TimeStampedModel
from core.restricted import RestrictedFileModel


# ---------------------------------------------------------------------------
# Announcement
# ---------------------------------------------------------------------------
class AnnouncementQuerySet(models.QuerySet):
    def published(self):
        return self.filter(published_at__isnull=False, published_at__lte=timezone.now())

    def drafts(self):
        return self.filter(published_at__isnull=True)

    def live(self):
        """Published and not yet expired."""
        now = timezone.now()
        return self.published().filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))

    def for_audience_of(self, user):
        """Announcements whose audience includes `user` (ignores publish state).

        Only active internal staff are ever in an audience.
        """
        if (
            user is None
            or not getattr(user, "is_authenticated", False)
            or not getattr(user, "is_active", False)
            or getattr(user, "user_type", None) != "staff"
        ):
            return self.none()
        q = Q(audience=Announcement.ALL_STAFF)
        if user.department_id:
            q |= Q(audience=Announcement.DEPARTMENT, department_id=user.department_id)
        if user.role_id:
            role_rows = Announcement.roles.through.objects.filter(role_id=user.role_id)
            q |= Q(audience=Announcement.ROLES, pk__in=role_rows.values("announcement_id"))
        return self.filter(q)

    def visible_to(self, user, *, include_expired=False):
        """What `user` can read: published, in their audience, not expired
        (unless `include_expired`)."""
        base = self.published() if include_expired else self.live()
        return base.for_audience_of(user)

    def unread_for(self, user):
        """Live announcements for `user` that they have not opened yet."""
        if not getattr(user, "pk", None):
            return self.none()
        return self.visible_to(user).exclude(
            Exists(AnnouncementRead.objects.filter(announcement=OuterRef("pk"), user=user))
        )

    def needing_ack_from(self, user):
        if not getattr(user, "pk", None):
            return self.none()
        return self.visible_to(user).filter(requires_ack=True).exclude(
            Exists(AnnouncementAck.objects.filter(announcement=OuterRef("pk"), user=user))
        )

    def with_user_state(self, user):
        """Annotate `is_read` / `is_acked` for `user` (two EXISTS subqueries)."""
        return self.annotate(
            is_read=Exists(AnnouncementRead.objects.filter(announcement=OuterRef("pk"), user=user)),
            is_acked=Exists(AnnouncementAck.objects.filter(announcement=OuterRef("pk"), user=user)),
        )


class Announcement(TimeStampedModel):
    AUDITED = True

    NOTICE, ACTION_NEEDED, URGENT = "notice", "action_needed", "urgent"
    KIND_CHOICES = [
        (NOTICE, "Notice"),
        (ACTION_NEEDED, "Action needed"),
        (URGENT, "Urgent"),
    ]

    ALL_STAFF, DEPARTMENT, ROLES = "all_staff", "department", "roles"
    AUDIENCE_CHOICES = [
        (ALL_STAFF, "All staff"),
        (DEPARTMENT, "One department"),
        (ROLES, "Specific roles"),
    ]

    title = models.CharField(max_length=200)
    body = models.TextField(help_text="Plain text. Line breaks are kept.")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=NOTICE, db_index=True)
    requires_ack = models.BooleanField(
        "requires acknowledgement", default=False,
        help_text="Staff must tap “Acknowledge”. Posters see who has not.",
    )
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default=ALL_STAFF)
    department = models.ForeignKey(
        "staff.Department", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="official_announcements",
    )
    roles = models.ManyToManyField(
        "accounts.Role", blank=True, related_name="official_announcements",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="announcements_posted",
    )
    published_at = models.DateTimeField(null=True, blank=True, db_index=True,
                                        help_text="Empty = draft.")
    pinned = models.BooleanField(default=False, help_text="Pinned notices stay at the top.")
    expires_at = models.DateTimeField(null=True, blank=True,
                                      help_text="Hidden from the list after this time.")

    objects = AnnouncementQuerySet.as_manager()

    class Meta:
        ordering = ["-pinned", "-published_at", "-created_at"]
        permissions = [
            ("post_announcement", "Can post announcements"),
            ("view_announcement_report", "Can see acknowledgement reports"),
        ]
        indexes = [models.Index(fields=["audience", "published_at"], name="ann_audience_pub_idx")]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("announcements:detail", args=[self.pk])

    @property
    def is_published(self):
        return self.published_at is not None

    @property
    def is_draft(self):
        return self.published_at is None

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    def audience_label(self):
        if self.audience == self.DEPARTMENT:
            return self.department.name if self.department_id else "A department"
        if self.audience == self.ROLES:
            names = [r.name for r in self.roles.all()]
            return ", ".join(names) if names else "Specific roles"
        return "All staff"

    def poll_or_none(self):
        try:
            return self.poll
        except AnnouncementPoll.DoesNotExist:
            return None


def audience_users(announcement):
    """Every active internal staff user in `announcement`'s audience."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    qs = User.objects.filter(is_active=True, user_type="staff")
    if announcement.audience == Announcement.DEPARTMENT:
        if not announcement.department_id:
            return qs.none()
        return qs.filter(department_id=announcement.department_id)
    if announcement.audience == Announcement.ROLES:
        if not announcement.pk:
            return qs.none()
        role_ids = announcement.roles.values("pk")
        return qs.filter(role_id__in=role_ids)
    return qs


# ---------------------------------------------------------------------------
# Attachment (protected storage)
# ---------------------------------------------------------------------------
class AnnouncementAttachment(RestrictedFileModel):
    MEDIA_SUBDIR = "announcements/attachments"

    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE,
                                     related_name="attachments")

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return self.original_name or self.file.name

    def save(self, *args, **kwargs):
        from core.uploads import enforce_allowed_extension

        enforce_allowed_extension(self)
        super().save(*args, **kwargs)

    def can_download(self, user):
        if not getattr(user, "is_authenticated", False) or self.is_deleted:
            return False
        ann = self.announcement
        if Announcement.objects.visible_to(user, include_expired=True).filter(pk=ann.pk).exists():
            return True
        from .permissions import can_edit

        return can_edit(user, ann)


# ---------------------------------------------------------------------------
# Per-user state: acknowledged / read
# ---------------------------------------------------------------------------
class AnnouncementAck(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="acks")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="announcement_acks")
    acknowledged_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-acknowledged_at"]
        constraints = [
            models.UniqueConstraint(fields=["announcement", "user"], name="ann_ack_once"),
        ]
        verbose_name = "acknowledgement"

    def __str__(self):
        return f"{self.user} acknowledged {self.announcement_id}"


class AnnouncementRead(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="announcement_reads")
    read_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-read_at"]
        constraints = [
            models.UniqueConstraint(fields=["announcement", "user"], name="ann_read_once"),
        ]

    def __str__(self):
        return f"{self.user} read {self.announcement_id}"


# ---------------------------------------------------------------------------
# Polls (generic: the announcement link is optional)
# ---------------------------------------------------------------------------
class AnnouncementPoll(TimeStampedModel):
    AUDITED = True

    announcement = models.OneToOneField(
        Announcement, null=True, blank=True, on_delete=models.CASCADE, related_name="poll",
    )
    question = models.CharField(max_length=255)
    allow_multiple = models.BooleanField(default=False,
                                         help_text="Voters may pick more than one option.")
    closes_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "poll"

    def __str__(self):
        return self.question

    @property
    def is_open(self):
        return self.closes_at is None or self.closes_at > timezone.now()

    def results(self):
        """[{option, votes, percent}] in option order, plus voter totals.

        Percentages are of distinct voters, so a multi-choice poll can add up
        to more than 100%.
        """
        options = list(self.options.annotate(n=models.Count("votes")).order_by("position", "id"))
        voters = self.votes.values("user_id").distinct().count()
        rows = []
        for opt in options:
            pct = round(100 * opt.n / voters) if voters else 0
            rows.append({"option": opt, "votes": opt.n, "percent": pct})
        return {"rows": rows, "voters": voters, "total_votes": sum(r["votes"] for r in rows)}


class PollOption(models.Model):
    poll = models.ForeignKey(AnnouncementPoll, on_delete=models.CASCADE, related_name="options")
    text = models.CharField(max_length=200)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return self.text


class PollVote(models.Model):
    poll = models.ForeignKey(AnnouncementPoll, on_delete=models.CASCADE, related_name="votes")
    option = models.ForeignKey(PollOption, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="poll_votes")
    voted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-voted_at"]
        constraints = [
            models.UniqueConstraint(fields=["poll", "user", "option"], name="poll_vote_once"),
        ]

    def __str__(self):
        return f"{self.user} -> {self.option}"
