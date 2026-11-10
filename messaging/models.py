"""Internal messaging (doc 30 §19.8). Non-real-time inbox — direct and
group threads. Every routed action elsewhere in the app can drop a
message here in addition to firing email, so outcomes stay visible
in-system.

Design notes:
- `ThreadParticipant.last_read_at` drives unread counts per user, per thread.
- `ThreadParticipant.is_archived` archives per-participant (my archive ≠ yours).
- `Message.is_system` marks auto-generated notices.
- No hard deletes; threads and messages are preserved for audit.

Memos (added):
- One-way formal declarations with a routing chain, mirroring the
  workflow app's approval engine.
- A `MemoRoute` is a named chain of `MemoRouteStep`s.
- A `MemoRouteRule` decides which route applies for a given
  (sender_role, target_kind) pair.
- `MemoApproval` records every decision, append-only.
- `Memo.status` is the source of truth; `is_draft` is synced for
  backward compatibility.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from core.restricted import RestrictedFileModel
from core.storage import restricted_storage, uuid_upload_to

# Module-level visibility choices — shared by MemoRouteRule and Memo.
MEMO_VISIBILITY_CHOICES = [
    ("specific_user", "One specific person"),
    ("all_staff",     "All staff"),
    ("all_directors", "All Directors"),
    ("department",    "A department"),
    ("role",          "Specific roles"),
    ("individuals",   "A list of people"),
    # v2.1 Phase E (M6): staff picked by department, rank range, designation and role.
    ("staff_filter",  "Staff matching filters"),
    # v2.1 Phase E (M1): an external memo goes to institutions only.
    ("no_staff",      "No staff (institutions only)"),
]

# v2.1 Phase E (M1, M7): what kind of memo this is.
MEMO_KIND_CHOICES = [
    ("internal", "Internal memo"),
    ("external", "External memo (to institutions)"),
    ("general",  "General memo (staff and institutions)"),
]

# v2.1 Phase E (M4): which institutions an external or general memo goes to.
MEMO_INSTITUTION_SCOPE_CHOICES = [
    ("", "No institutions"),
    ("all", "All institutions"),
    ("selected", "Selected institutions"),
    ("filter", "Institutions matching filters"),
]

# ===========================================================================
# Threads & messages (unchanged)
# ===========================================================================
class Thread(TimeStampedModel):
    AUDITED = True

    DIRECT, GROUP = "direct", "group"
    TYPE_CHOICES = [(DIRECT, "Direct"), (GROUP, "Group")]

    subject = models.CharField(max_length=255, blank=True)
    thread_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=GROUP)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="threads_created",
    )
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # ---- v2.1 Phase E (L3): group information and settings ----------------
    # Who may do what in a group: everyone, or the owner and admins only.
    ALL, ADMINS = "all", "admins"
    POLICY_CHOICES = [(ALL, "All members"), (ADMINS, "Only admins")]
    description = models.TextField(blank=True, max_length=500)
    photo = models.FileField(upload_to=uuid_upload_to, storage=restricted_storage, blank=True,
                             help_text="Group photo (served to participants only).")
    send_policy = models.CharField(max_length=8, choices=POLICY_CHOICES, default=ALL,
                                   help_text="Who can send messages.")
    edit_info_policy = models.CharField(max_length=8, choices=POLICY_CHOICES, default=ADMINS,
                                        help_text="Who can change the name, photo and description.")
    add_members_policy = models.CharField(max_length=8, choices=POLICY_CHOICES, default=ADMINS,
                                          help_text="Who can add members and share the invite link.")

    # ---- v2.1 X41: groups that belong to a subject (SSQ team, visit panel) --
    # `subject` stays the human title (existing column); the linked object is
    # `subject_object` (generic FK over subject_ct / subject_id).
    PURPOSE_SSQ_TEAM, PURPOSE_VISIT_PANEL = "ssq_team", "visit_panel"
    subject_ct = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    subject_id = models.CharField(max_length=64, blank=True, default="")
    subject_object = GenericForeignKey("subject_ct", "subject_id")
    purpose = models.CharField(max_length=20, blank=True, default="",
                               help_text="Why the system made this group, e.g. ssq_team or visit_panel.")
    auto_managed = models.BooleanField(default=False,
                                       help_text="Members follow the subject's team; nobody edits them by hand.")
    archived_at = models.DateTimeField(null=True, blank=True,
                                       help_text="Set when the subject closes: the group becomes read-only.")

    class Meta:
        ordering = ["-last_message_at", "-updated_at"]
        constraints = [
            models.UniqueConstraint(fields=["subject_ct", "subject_id", "purpose"],
                                    condition=models.Q(subject_ct__isnull=False),
                                    name="messaging_thread_unique_subject_purpose"),
        ]

    @property
    def is_read_only(self):
        return self.archived_at is not None

    def __str__(self):
        if self.subject:
            return self.subject
        participants = ", ".join(
            p.user.get_full_name() or p.user.email
            for p in self.participants.all()[:3]
        )
        return participants or f"Thread #{self.pk}"

    def display_title_for(self, user):
        if self.subject:
            return self.subject
        others = [p.user for p in self.participants.select_related("user")
                  if p.user_id != user.pk]
        if not others:
            return "Self thread"
        names = ", ".join((u.get_full_name() or u.email) for u in others[:3])
        if len(others) > 3:
            names += f" +{len(others) - 3}"
        return names

    def display_title(self):
        if self.subject:
            return self.subject
        names = []
        for p in self.participants.all()[:3]:
            try:
                names.append(p.user.get_full_name() or p.user.email)
            except Exception:
                continue
        if not names:
            return f"Thread #{self.pk}"
        more = self.participants.count() - len(names)
        return ", ".join(names) + (f" +{more}" if more > 0 else "")

    def unread_count_for(self, user):
        participant = self.participants.filter(user=user).first()
        if not participant:
            return 0
        qs = self.messages.exclude(sender=user).filter(is_deleted_for_everyone=False)
        qs = qs.exclude(hidden_for__user=user).distinct()
        if participant.last_read_at:
            qs = qs.filter(sent_at__gt=participant.last_read_at)
        return qs.count()

    def last_message(self, user=None):
        qs = self.messages.filter(is_deleted_for_everyone=False)
        if user is not None:
            qs = qs.exclude(hidden_for__user=user).distinct()
        return qs.order_by("-sent_at", "-id").first()


class ThreadParticipant(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="thread_participations")
    joined_at = models.DateTimeField(default=timezone.now)
    last_read_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    # Phase 9: chat upgrade.
    # Set whenever this user's client fetches the thread or polls for updates;
    # a message is "delivered" to them once this is >= its sent_at.
    last_delivered_at = models.DateTimeField(null=True, blank=True)
    is_pinned = models.BooleanField(default=False)
    pinned_at = models.DateTimeField(null=True, blank=True)
    # Null = not muted. "Always" is stored as a far-future date.
    muted_until = models.DateTimeField(null=True, blank=True)
    # v2.1 Phase E (L3): the member's role in a group.
    OWNER, ADMIN, MEMBER = "owner", "admin", "member"
    ROLE_CHOICES = [(OWNER, "Owner"), (ADMIN, "Admin"), (MEMBER, "Member")]
    role = models.CharField(max_length=8, choices=ROLE_CHOICES, default=MEMBER)

    MAX_PINNED = 5

    @property
    def is_admin(self):
        return self.role in (self.OWNER, self.ADMIN)

    class Meta:
        unique_together = ("thread", "user")
        ordering = ["joined_at"]

    def __str__(self):
        return f"{self.user} in {self.thread_id}"


class Message(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="messages_sent",
    )
    body = models.TextField()
    sent_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_system = models.BooleanField(default=False,
                                    help_text="Auto-generated (not typed by a human).")
    reply_to = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="replies",
    )
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted_for_everyone = models.BooleanField(default=False)
    # Phase 9: chat upgrade.
    forwarded = models.BooleanField(default=False)
    # Bumped whenever anything a viewer sees on the message changes (edit,
    # delete, reaction, poll vote, transcript, task/memo link) so the
    # short-poll updates endpoint can send the new version.
    changed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    linked_task = models.ForeignKey(
        "tasks.Task", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="source_messages",
    )
    # v2.1 Phase E (L6): pinned inside the chat (at most the setting
    # messaging.pins_per_chat, default 3) - shown in the bar under the header.
    pinned_at = models.DateTimeField(null=True, blank=True, db_index=True)
    pinned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="+")
    linked_memo = models.ForeignKey(
        "Memo", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="source_messages",
    )

    class Meta:
        ordering = ["sent_at", "id"]

    def touch(self, save=True):
        """Mark the message as changed for the live-updates endpoint."""
        self.changed_at = timezone.now()
        if save:
            Message.objects.filter(pk=self.pk).update(changed_at=self.changed_at)
        return self.changed_at

    def __str__(self):
        return f"[{self.thread_id}] {self.sender}: {self.body[:40]}"


class MessageDeletion(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE,
                                related_name="hidden_for")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="hidden_messages")
    deleted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "user"],
                name="messaging_unique_message_hidden_per_user",
            ),
        ]


class MessageRead(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE,
                                related_name="read_receipts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="message_read_receipts")
    read_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["message", "user"],
                name="messaging_unique_message_read_per_user",
            ),
        ]


def attachment_expiry():
    from datetime import timedelta
    return timezone.now() + timedelta(days=30)


class MessageAttachment(RestrictedFileModel):
    MEDIA_SUBDIR = "messaging/attachments"

    message = models.ForeignKey(Message, on_delete=models.CASCADE,
                                related_name="attachments")
    expires_at = models.DateTimeField(default=attachment_expiry, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def can_download(self, user):
        if (
            not user.is_authenticated
            or self.is_deleted
            or self.expires_at <= timezone.now()
            or self.message.is_deleted_for_everyone
            or self.message.hidden_for.filter(user=user).exists()
        ):
            return False
        return self.message.thread.participants.filter(
            user=user, is_deleted=False,
        ).exists()


# ---------------------------------------------------------------------------
# Phase 9: reactions, stars, polls, voice notes
# ---------------------------------------------------------------------------
REACTION_EMOJIS = ["\U0001F44D", "\u2764\uFE0F", "\U0001F602", "\U0001F62E",
                   "\U0001F622", "\U0001F64F", "\U0001F44F", "\u2705"]


class MessageReaction(models.Model):
    """One reaction per user per message; a new emoji replaces the old one."""
    message = models.ForeignKey(Message, on_delete=models.CASCADE,
                                related_name="reactions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="message_reactions")
    emoji = models.CharField(max_length=16)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["message", "user"],
                                    name="messaging_unique_reaction_per_user"),
        ]


class MessageStar(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE,
                                related_name="stars")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="starred_messages")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["message", "user"],
                                    name="messaging_unique_star_per_user"),
        ]


class ChatPoll(models.Model):
    message = models.OneToOneField(Message, on_delete=models.CASCADE,
                                   related_name="poll")
    question = models.CharField(max_length=300)
    allow_multiple = models.BooleanField(default=False)
    closes_at = models.DateTimeField(null=True, blank=True)

    def is_closed(self):
        return bool(self.closes_at and self.closes_at <= timezone.now())


class ChatPollOption(models.Model):
    poll = models.ForeignKey(ChatPoll, on_delete=models.CASCADE,
                             related_name="options")
    text = models.CharField(max_length=120)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]


class ChatPollVote(models.Model):
    poll = models.ForeignKey(ChatPoll, on_delete=models.CASCADE,
                             related_name="votes")
    option = models.ForeignKey(ChatPollOption, on_delete=models.CASCADE,
                               related_name="votes")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="chat_poll_votes")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["option", "user"],
                                    name="messaging_unique_poll_vote_per_option"),
        ]


class VoiceNote(RestrictedFileModel):
    """A recorded voice message, normalised to AAC-LC mono 16 kHz (.m4a)."""
    MEDIA_SUBDIR = "messaging/voice"

    PENDING, DONE, FAILED, DISABLED, UNAVAILABLE = (
        "pending", "done", "failed", "disabled", "unavailable",
    )
    TRANSCRIPT_STATUS_CHOICES = [
        (PENDING, "Transcribing"),
        (DONE, "Done"),
        (FAILED, "Failed"),
        (DISABLED, "Turned off by the sender"),
        (UNAVAILABLE, "Unavailable"),
    ]

    message = models.OneToOneField(Message, on_delete=models.CASCADE,
                                   related_name="voice_note")
    duration_ms = models.PositiveIntegerField(default=0)
    size_bytes = models.PositiveIntegerField(default=0)
    waveform = models.JSONField(default=list, blank=True)
    mime = models.CharField(max_length=60, default="audio/mp4")
    transcript_status = models.CharField(max_length=12, choices=TRANSCRIPT_STATUS_CHOICES,
                                         default=PENDING)
    transcript = models.TextField(blank=True)
    transcript_language = models.CharField(max_length=12, blank=True)
    transcript_confidence = models.FloatField(null=True, blank=True)
    transcript_edited = models.BooleanField(default=False)
    transcribed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]

    def can_download(self, user):
        # Same rules as MessageAttachment (participants only; not deleted for
        # everyone; not hidden by this user) - voice notes do not expire.
        if (
            not user.is_authenticated
            or self.is_deleted
            or self.message.is_deleted_for_everyone
            or self.message.hidden_for.filter(user=user).exists()
        ):
            return False
        return self.message.thread.participants.filter(
            user=user, is_deleted=False,
        ).exists()

    @property
    def duration_label(self):
        seconds = int(round((self.duration_ms or 0) / 1000))
        return f"{seconds // 60}:{seconds % 60:02d}"


class VoiceNotePlay(models.Model):
    """First time a listener played a voice note (drives the unplayed dot)."""
    voice_note = models.ForeignKey(VoiceNote, on_delete=models.CASCADE,
                                   related_name="plays")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="voice_note_plays")
    played_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["voice_note", "user"],
                                    name="messaging_unique_voice_play_per_user"),
        ]


# ===========================================================================
# v2.1 Phase E: reminders (L7) and invite links (L4)
# ===========================================================================
class MessageReminder(models.Model):
    """"Remind me about this message" - a notification at `remind_at`.
    Sent by the beat task messaging.send_due_reminders (every minute)."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="message_reminders")
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="reminders")
    remind_at = models.DateTimeField(db_index=True)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["remind_at", "id"]
        indexes = [models.Index(fields=["sent_at", "remind_at"], name="messaging_reminder_due_idx")]

    def __str__(self):
        return f"Reminder {self.pk} for {self.user_id} at {self.remind_at:%Y-%m-%d %H:%M}"

    @property
    def is_pending(self):
        return self.sent_at is None


class ThreadInvite(models.Model):
    """A join link for a group: random token (stored, so it can be revoked),
    an expiry and an optional use limit. Staff only (services check)."""
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="invites")
    token = models.CharField(max_length=43, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    max_uses = models.PositiveIntegerField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invite to thread {self.thread_id}"

    def is_usable(self, now=None):
        now = now or timezone.now()
        if self.revoked_at is not None or self.expires_at <= now:
            return False
        return self.max_uses is None or self.use_count < self.max_uses


def start_thread(*, creator, participants, subject="", initial_body="",
                 thread_type=None):
    from django.db import transaction
    users = list({creator, *participants})
    if thread_type is None:
        thread_type = Thread.DIRECT if len(users) == 2 else Thread.GROUP

    with transaction.atomic():
        thread = Thread.objects.create(subject=subject[:255],
                                       thread_type=thread_type,
                                       created_by=creator)
        for user in users:
            ThreadParticipant.objects.create(
                thread=thread, user=user,
                last_read_at=timezone.now() if user == creator else None,
                # v2.1 L3: whoever starts a group owns it.
                role=(ThreadParticipant.OWNER if user == creator and thread_type == Thread.GROUP
                      else ThreadParticipant.MEMBER),
            )
        Message.objects.create(thread=thread, sender=creator, body=initial_body)
        thread.last_message_at = timezone.now()
        thread.save(update_fields=["last_message_at"])
    return thread


# ===========================================================================
# MEMO ROUTING — data-driven chains for memos
# ===========================================================================
class MemoRoute(TimeStampedModel):
    """A named, ordered set of steps a memo walks before it's delivered.

    Routes are reusable across routing rules. Editing a route's steps
    affects only future memos — in-flight memos track their position by
    `MemoRouteStep.pk`, so a step that's been deleted is treated as
    "chain complete".

    `same_rank_behaviour` decides what happens when a step's resolved
    actor is the same rank as the sender:
      - 'skip': bypass the step (default — same rank = no need to approve)
      - 'keep': the actor acts on it anyway
    """
    AUDITED = True

    SAME_RANK_SKIP = "skip"
    SAME_RANK_KEEP = "keep"
    SAME_RANK_CHOICES = [
        (SAME_RANK_SKIP, "Skip the step"),
        (SAME_RANK_KEEP, "Keep the step (actor still acts)"),
    ]

    name = models.CharField(max_length=150)
    code = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    same_rank_behaviour = models.CharField(
        max_length=8,
        choices=SAME_RANK_CHOICES,
        default=SAME_RANK_SKIP,
        help_text=(
            "When the resolved actor has the same rank as the memo's "
            "sender. Lower ranks are always skipped; higher ranks always act."
        ),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        permissions = [
            ("manage_memoroute", "Can manage memo routes and routing rules"),
        ]

    def __str__(self):
        return self.name

    def step_count(self):
        return self.steps.count()


class MemoRouteStep(models.Model):
    """One hop in a `MemoRoute`."""
    AUDITED = True

    APPROVER_DIVISION_HEAD = "division_head"
    APPROVER_DIRECTOR = "director"
    APPROVER_DEPUTY = "deputy"
    APPROVER_ES = "es"
    APPROVER_SPECIFIC = "specific_user"
    APPROVER_ROLE_IN_DEPT = "role_in_department"
    # The director of the department the memo is addressed to (the target
    # person's department, or the department of a department circular).
    # Skipped automatically when that is the sender's own department, when
    # that director is the recipient, or when nobody outside the sender's
    # department receives the memo.
    APPROVER_RECEIVING_DIRECTOR = "receiving_director"
    # Kinds that walk up the SENDER's own department.
    SENDER_SIDE_KINDS = ("division_head", "deputy", "director")
    APPROVER_CHOICES = [
        (APPROVER_DIVISION_HEAD, "Sender's division head"),
        (APPROVER_DIRECTOR, "Sender's department director"),
        (APPROVER_DEPUTY, "Sender's deputy director"),
        (APPROVER_ES, "Executive Secretary"),
        (APPROVER_SPECIFIC, "A specific user"),
        (APPROVER_ROLE_IN_DEPT, "Anyone with a role in a department"),
        (APPROVER_RECEIVING_DIRECTOR, "Recipient's department director"),
    ]

    route = models.ForeignKey(MemoRoute, on_delete=models.CASCADE,
                              related_name="steps")
    position = models.PositiveIntegerField(
        help_text="Order within the route (1 = first).",
    )
    name = models.CharField(
        max_length=150,
        help_text="Human label shown on the memo (e.g. 'Director approval').",
    )
    approver_kind = models.CharField(max_length=32, choices=APPROVER_CHOICES)
    approver_role = models.ForeignKey(
        "accounts.Role", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
        help_text="Required when approver_kind = 'role_in_department'.",
    )
    approver_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
        help_text="Required when approver_kind = 'specific_user'.",
    )
    scope = models.CharField(
        max_length=50, default="requester",
        help_text=(
            "'requester' (sender's department), 'es' (no scope), or a "
            "Department.code for role_in_department steps."
        ),
    )
    is_optional = models.BooleanField(
        default=False,
        help_text=(
            "If ticked, this step is silently skipped when no one matches "
            "the role or user. Required steps with no actor park the memo "
            "and alert admins."
        ),
    )
    is_hierarchical = models.BooleanField(
        default=True,
        help_text=(
            "If on, this step participates in the rank system: it's skipped "
            "when the resolved actor is junior to the sender, or equal-ranked "
            "and the route's same-rank behaviour says to skip. Turn this OFF "
            "for mandatory gates (ES, Records filing) that must always run."
        ),
    )
    instructions = models.TextField(
        blank=True,
        help_text="Shown to the acting user as guidance on the memo page.",
    )

    class Meta:
        ordering = ["route", "position"]
        unique_together = ("route", "position")
        verbose_name = "memo route step"

    def __str__(self):
        return f"{self.route.code}:{self.position} — {self.name}"


class MemoRouteRule(TimeStampedModel):
    """Decides which `MemoRoute` applies for a given (sender role, target).

    Matching is permissive: an empty `sender_role_codes` or
    `target_role_codes` list means "any".

    When several rules match, the one with the highest `priority` wins.
    Within the same priority, the most recently updated wins.
    """
    AUDITED = True

    name = models.CharField(max_length=150)
    route = models.ForeignKey(MemoRoute, on_delete=models.PROTECT,
                              related_name="rules")
    sender_role_codes = models.JSONField(
        default=list, blank=True,
        help_text=(
            "Role codes of the sender this rule applies to. "
            "Empty list = any role. Example: [\"admin-staff\", \"officer\"]."
        ),
    )
    target_kind = models.CharField(
        max_length=20,
        choices=MEMO_VISIBILITY_CHOICES,
        help_text="The memo's visibility/target kind.",
    )
    target_role_codes = models.JSONField(
        default=list, blank=True,
        help_text=(
            "When target_kind = 'specific_user', restrict this rule to a "
            "target whose role code is in this list. Empty list = any "
            "target. Ignored for broadcast target kinds."
        ),
    )
    RELATION_ANY = "any"
    RELATION_SAME = "same"
    RELATION_OTHER = "other"
    RELATION_CHOICES = [
        (RELATION_ANY, "Any department"),
        (RELATION_SAME, "Recipients are in the sender's own department"),
        (RELATION_OTHER, "Recipients are (partly) in another department"),
    ]
    department_relation = models.CharField(
        max_length=8, choices=RELATION_CHOICES, default=RELATION_ANY,
        help_text=(
            "Match only memos that stay inside the sender's department, or only "
            "memos that leave it. Used for one person, a list of people and a "
            "department circular; broadcasts always count as 'any'."
        ),
    )
    priority = models.PositiveIntegerField(
        default=0,
        help_text="Higher wins when several rules match.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-priority", "-updated_at", "name"]

    def __str__(self):
        return self.name




class MemoApproval(models.Model):
    """One decision at one step. Append-only."""
    APPROVE = "approve"
    RETURN = "return"
    REJECT = "reject"
    SKIP = "skip"
    DECISION_CHOICES = [
        (APPROVE, "Approved"),
        (RETURN, "Returned"),
        (REJECT, "Rejected"),
        (SKIP, "Skipped"),
    ]

    memo = models.ForeignKey("Memo", on_delete=models.CASCADE,
                             related_name="approvals")
    # A step can be deleted from its route later; the decision is kept and
    # remembers the step's name (step_name), instead of blocking the delete.
    step = models.ForeignKey(MemoRouteStep, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="approvals")
    step_name = models.CharField(max_length=150, blank=True)
    # Empty for automatic decisions (a step skipped by the routing rules).
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.PROTECT, related_name="+")
    actor_role = models.CharField(max_length=150, blank=True)
    decision = models.CharField(max_length=16, choices=DECISION_CHOICES)
    comment = models.TextField(blank=True)
    decided_at = models.DateTimeField(default=timezone.now, db_index=True)
    duration_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["decided_at", "id"]

    def __str__(self):
        actor = (
            (self.actor.get_full_name() or self.actor.email)
            if self.actor_id else "—"
        )
        step_label = self.step.name if self.step_id else (self.step_name or "(deleted step)")
        return f"{self.get_decision_display()} · {step_label} · {actor}"


# ===========================================================================
# MEMO — one-way formal declarations
# ===========================================================================
class Memo(TimeStampedModel):
    """A formal internal memo. Sender declares, routing chain approves,
    then the memo is delivered to the target audience.

    Lifecycle:
        DRAFT → AWAITING → PUBLISHED
              → RETURNED → (edited) → AWAITING
              → REJECTED
              → CANCELLED
    """
    AUDITED = True
    # Review M8: memo text never goes into the audit log.
    AUDIT_EXCLUDE_FIELDS = ("subject", "body")

    # ---- Priority ---------------------------------------------------
    NORMAL, HIGH, URGENT = "normal", "high", "urgent"
    PRIORITY_CHOICES = [
        (NORMAL, "Normal"),
        (HIGH, "High"),
        (URGENT, "Urgent"),
    ]

    # ---- Status -----------------------------------------------------
    DRAFT = "draft"
    AWAITING = "awaiting"
    RETURNED = "returned"
    PUBLISHED = "published"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (AWAITING, "Awaiting approval"),
        (RETURNED, "Returned for revision"),
        (PUBLISHED, "Published"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
    ]
    OPEN_STATUSES = {DRAFT, AWAITING, RETURNED}

    # ---- Visibility / target kind ---------------------------------
    SPECIFIC_USER = "specific_user"
    ALL_STAFF = "all_staff"
    ALL_DIRECTORS = "all_directors"
    DEPARTMENT = "department"
    ROLE = "role"
    INDIVIDUALS = "individuals"
    STAFF_FILTER = "staff_filter"
    NO_STAFF = "no_staff"
    VISIBILITY_CHOICES = MEMO_VISIBILITY_CHOICES

    # ---- Kind (v2.1 Phase E, M1) -----------------------------------
    INTERNAL, EXTERNAL, GENERAL = "internal", "external", "general"
    KIND_CHOICES = MEMO_KIND_CHOICES
    INST_NONE, INST_ALL, INST_SELECTED, INST_FILTER = "", "all", "selected", "filter"
    INSTITUTION_SCOPE_CHOICES = MEMO_INSTITUTION_SCOPE_CHOICES

    # ---- Identity ---------------------------------------------------
    reference = models.CharField(max_length=20, unique=True, db_index=True)
    subject = models.CharField(max_length=255)
    body = models.TextField()
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="memos_sent",
    )
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES,
                                default=NORMAL)

    # ---- Target -----------------------------------------------------
    visibility = models.CharField(
        max_length=20, choices=VISIBILITY_CHOICES, default=SPECIFIC_USER,
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="targeted_memos",
        help_text="Set when visibility = 'specific_user'.",
    )
    department = models.ForeignKey(
        "staff.Department", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="memos_addressed",
        help_text="Set when visibility = 'department'.",
    )
    roles = models.ManyToManyField(
        "auth.Group", blank=True, related_name="memos_addressed_by_role",
        help_text="Set when visibility = 'role'.",
    )
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True,
        related_name="memos_addressed_individually",
        help_text="Set when visibility = 'individuals'.",
    )

    # Staff filter (visibility = staff_filter, M6): {"departments": [ids],
    # "rank_min": int, "rank_max": int, "designations": [ids], "roles": [codes]}.
    staff_filter = models.JSONField(default=dict, blank=True)

    # ---- Kind and institutions (v2.1 Phase E, M1-M5) -----------------
    kind = models.CharField(max_length=10, choices=MEMO_KIND_CHOICES, default="internal", db_index=True)
    institution_scope = models.CharField(max_length=10, choices=MEMO_INSTITUTION_SCOPE_CHOICES,
                                         default="", blank=True)
    # Filter builder (scope = filter): see messaging.memo_audience.INSTITUTION_FILTER_KEYS.
    institution_filter = models.JSONField(default=dict, blank=True)
    target_institutions = models.ManyToManyField(
        "institutions.Institution", blank=True, related_name="memos_addressed",
        help_text="Set when institution_scope = 'selected' (one or more).")
    # The Executive Secretary's sign-off on an external / general memo: the
    # sender when the ES sends it, else the ES who approved it (M3).
    es_signatory = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    es_signed_at = models.DateTimeField(null=True, blank=True)

    # ---- Routing ----------------------------------------------------
    route = models.ForeignKey(
        MemoRoute, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="memos",
        help_text="The approval chain this memo walks. Null = publish directly.",
    )
    current_step = models.ForeignKey(
        MemoRouteStep, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    current_step_key = models.CharField(
        max_length=64, blank=True,
        help_text="Stable key of the current step — survives step reordering.",
    )
    current_step_started_at = models.DateTimeField(null=True, blank=True)

    # ---- Lifecycle state ------------------------------------------
    status = models.CharField(max_length=16, choices=STATUS_CHOICES,
                              default=DRAFT, db_index=True)
    is_draft = models.BooleanField(default=True)
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # ---- Return cycle ---------------------------------------------
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
        help_text="The approver who last returned this memo.",
    )
    returned_from_step_key = models.CharField(max_length=64, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    returned_comment = models.TextField(blank=True)
    resubmitted_count = models.PositiveSmallIntegerField(default=0)

    # ---- Rejection -------------------------------------------------
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_comment = models.TextField(blank=True)

    # ---- Acknowledgment -------------------------------------------
    requires_acknowledgment = models.BooleanField(
        default=False,
        help_text="Recipients must click 'Acknowledge receipt'.",
    )

    class Meta:
        ordering = ["-published_at", "-created_at", "-pk"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["sender", "-created_at"]),
            models.Index(fields=["is_draft", "-published_at"]),
        ]
        permissions = [
            ("manage_memo", "Can edit or delete any memo"),
            ("send_memo_broadcast",
             "Can address memos to All Staff, All Directors, or a whole department"),
            ("approve_memo", "Can act as an approver on memo routing steps"),
            ("send_external_memo",
             "Can send external memos to institutions (the Executive Secretary)"),
        ]

    def __str__(self):
        return f"{self.reference} — {self.subject}"

    # ---- Save -------------------------------------------------------
    def save(self, *args, **kwargs):
        # Keep is_draft in sync with status for backward compatibility.
        self.is_draft = (self.status == self.DRAFT)
        super().save(*args, **kwargs)

    # ---- Derived ----------------------------------------------------
    @property
    def is_published(self):
        return self.status == self.PUBLISHED

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_live(self):
        return self.is_published and not self.is_expired

    @property
    def is_awaiting(self):
        return self.status == self.AWAITING

    @property
    def is_returned(self):
        return self.status == self.RETURNED

    @property
    def is_closed(self):
        return self.status in {self.PUBLISHED, self.REJECTED, self.CANCELLED}

    @property
    def recipient_count(self):
        return self.memo_recipients.count()

    @property
    def read_count(self):
        return self.memo_recipients.filter(read_at__isnull=False).count()

    @property
    def acknowledged_count(self):
        return self.memo_recipients.filter(acknowledged_at__isnull=False).count()

    @property
    def is_outbound(self):
        """External or general: it leaves NBTE, so the ES signs it (M2, M3)."""
        return self.kind in (self.EXTERNAL, self.GENERAL)

    @property
    def signed_as(self):
        """'from' when the ES sent it, 'approved' when the ES approved it."""
        if not self.es_signatory_id:
            return ""
        return "from" if self.es_signatory_id == self.sender_id else "approved"

    @property
    def card_summary(self):
        if not self.body:
            return ""
        text = self.body.strip().replace("\n", " ")
        return (text[:160] + "…") if len(text) > 160 else text


class MemoRecipient(models.Model):
    """Materialized recipient row. Created at publish time."""
    memo = models.ForeignKey(Memo, on_delete=models.CASCADE,
                             related_name="memo_recipients")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="memo_receipts")

    delivered_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)
    # "Remove from my list" (Phase 8): the memo stays on record; it only
    # leaves this recipient's inbox and archive.
    hidden_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("memo", "user")
        ordering = ["-delivered_at", "-pk"]
        indexes = [
            models.Index(fields=["user", "is_archived", "-delivered_at"]),
        ]

    def __str__(self):
        return f"{self.user} on {self.memo.reference}"

    @property
    def is_read(self):
        return self.read_at is not None

    @property
    def is_acknowledged(self):
        return self.acknowledged_at is not None


class MemoAttachment(RestrictedFileModel):
    """File attached to a memo. Same 30-day expiry as message attachments."""
    MEDIA_SUBDIR = "messaging/memo_attachments"

    memo = models.ForeignKey(Memo, on_delete=models.CASCADE,
                             related_name="attachments")
    description = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField(default=attachment_expiry, db_index=True)

    class Meta:
        ordering = ["created_at", "id"]

    def can_download(self, user):
        if (
            not user.is_authenticated
            or self.is_deleted
            or self.expires_at <= timezone.now()
        ):
            return False
        if user.is_superuser or self.memo.sender_id == user.pk:
            return True
        if self.memo.is_draft:
            return False
        return self.memo.memo_recipients.filter(user=user).exists()


class MemoInstitutionRecipient(models.Model):
    """An institution an external / general memo was delivered to (v2.1 M4).
    Read and acknowledgement are per institution (any of its portal users)."""
    memo = models.ForeignKey(Memo, on_delete=models.CASCADE, related_name="institution_recipients")
    institution = models.ForeignKey("institutions.Institution", on_delete=models.CASCADE,
                                    related_name="memo_receipts")
    delivered_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)
    read_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="+")
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="+")

    class Meta:
        unique_together = ("memo", "institution")
        ordering = ["-delivered_at", "-pk"]
        indexes = [models.Index(fields=["institution", "-delivered_at"])]

    def __str__(self):
        return f"{self.institution_id} on {self.memo.reference}"

    @property
    def is_read(self):
        return self.read_at is not None

    @property
    def is_acknowledged(self):
        return self.acknowledged_at is not None
