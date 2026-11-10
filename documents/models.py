"""Documents (v2.1 X23 first; X22/X24-X26 issued documents arrive in Phase F).

`StaffSignature` is THE signature store for NBTE staff: one active row per
person, older rows kept (retired) so a document generated yesterday keeps the
signature that was in force when it was signed (`signature.png_for(user, at)`).

Images live in restricted storage and have NO URL route anywhere (spec S-13):
they are only embedded into generated documents, or shown to their owner as
an inline data: URI on My profile -> Signature.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import TimeStampedModel
from core.storage import restricted_storage, uuid_upload_to
from qa_core.files import QAFileMixin
from qa_core.scoping import InstitutionScopedManager, InstitutionScopedModelMixin, UnscopedInstitutionManager


class StaffSignature(TimeStampedModel):
    AUDITED = True
    # The image bytes are personal: never written to the audit trail.
    AUDIT_EXCLUDE_FIELDS = ("image", "source")
    MEDIA_SUBDIR = "documents/signatures"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="signatures")
    image = models.FileField(upload_to=uuid_upload_to, storage=restricted_storage)
    source = models.FileField(upload_to=uuid_upload_to, storage=restricted_storage, blank=True,
                              help_text="The uploaded photo, kept only until the signature is accepted.")
    source_name = models.CharField(max_length=255, blank=True)
    sensitivity = models.PositiveSmallIntegerField(default=50)
    ink = models.CharField(max_length=10, default="navy")
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    ink_ratio = models.FloatField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_active = models.BooleanField(default=False)
    retired_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user"], condition=Q(is_active=True),
                                    name="documents_one_active_signature_per_user"),
        ]
        indexes = [models.Index(fields=["user", "accepted_at"])]
        permissions = [
            ("view_signature_report", "Can see which directors have no signature yet (names only)"),
        ]

    def __str__(self):
        state = "active" if self.is_active else ("pending" if not self.accepted_at else "retired")
        return f"Signature of user #{self.user_id} ({state})"


# ===========================================================================
# v2.1 Phase F (X22, X24-X26): generated documents and the verification registry
# ===========================================================================

KIND_CHOICES = [
    ("invoice", "Invoice"), ("payment_slip", "Payment slip"), ("receipt", "Receipt"),
    ("payment_advice", "Payment advice"), ("letter", "Letter"), ("memo", "Memo"),
    ("schedule", "Schedule"), ("notice", "Notice"), ("staff_id", "Staff ID"),
]
KIND_LABELS = dict(KIND_CHOICES)


def _is_staff(user):
    return bool(user and user.is_authenticated and getattr(user, "user_type", "staff") == "staff")


class DocumentTemplate(TimeStampedModel):
    """What a generated document says. Published rows are never edited: a
    change makes a new version (``version`` + 1) and the old row stays for the
    documents already issued from it."""

    AUDITED = True
    AUDIENCE_CHOICES = [("internal", "Internal (NBTE staff)"), ("institution", "Institution"),
                        ("professional_body", "Professional body")]
    LETTERHEAD_CHOICES = [("internal_memo", "Internal memo"), ("external_letter", "External letter"),
                          ("receipt", "Receipt / payment document")]
    SIGNATORY_CHOICES = [("actor", "The person who approved"), ("es", "The Executive Secretary"),
                         ("actor_and_director", "The approver and their director"), ("none", "No signature (system)")]

    code = models.SlugField(max_length=60)
    version = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=40, choices=KIND_CHOICES, default="letter")
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default="institution")
    letterhead = models.CharField(max_length=20, choices=LETTERHEAD_CHOICES, default="external_letter")
    subject_line = models.CharField(max_length=200, blank=True,
                                    help_text="Heading of the letter; may use {placeholders} from the context.")
    body = models.TextField(blank=True, help_text="Paragraphs separated by a blank line; {placeholders} allowed.")
    signatory_rule = models.CharField(max_length=40, choices=SIGNATORY_CHOICES, default="actor")
    provisional_default = models.BooleanField(default=False)
    requires_qr = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code", "-version"]
        constraints = [models.UniqueConstraint(fields=["code", "version"], name="documents_template_code_version")]

    def __str__(self):
        return f"{self.title} (v{self.version})"

    @classmethod
    def current(cls, code):
        return cls.objects.filter(code=code, is_active=True).order_by("-version").first()


class DocumentRule(TimeStampedModel):
    """Event -> document. Every approval that emits ``event_code`` issues one
    document from ``template_code`` (the template's current version)."""

    AUDITED = True

    event_code = models.CharField(max_length=80, unique=True)
    template_code = models.SlugField(max_length=60)
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    provisional = models.BooleanField(null=True, blank=True, help_text="Empty = the template's default.")
    send_to_institution = models.BooleanField(default=True)

    class Meta:
        ordering = ["event_code"]

    def __str__(self):
        return f"{self.event_code} -> {self.template_code}"

    @property
    def template(self):
        return DocumentTemplate.current(self.template_code)


class IssuedDocument(InstitutionScopedModelMixin, QAFileMixin, TimeStampedModel):
    """The verification registry: one row per generated document. The QR on
    the document carries only ``/v/<token>/``; the short code is what people
    type on the Scan page. ``file`` is exactly the PDF that was sent."""

    AUDITED = True
    INSTITUTION_PATH = "institution"
    MEDIA_SUBDIR = "documents/issued"
    INSTITUTION_MEDIA_KIND = "documents/issued"

    VALID, SUPERSEDED, REVOKED = "valid", "superseded", "revoked"
    STATUS_CHOICES = [(VALID, "Valid"), (SUPERSEDED, "Superseded"), (REVOKED, "Revoked")]

    kind = models.CharField(max_length=40, choices=KIND_CHOICES, db_index=True)
    template = models.ForeignKey(DocumentTemplate, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    rule = models.ForeignKey(DocumentRule, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    event_code = models.CharField(max_length=80, blank=True, db_index=True)
    reference = models.CharField(max_length=80, unique=True)
    short_code = models.CharField(max_length=14, unique=True)
    token = models.CharField(max_length=40, unique=True)
    legacy_token = models.CharField(max_length=500, blank=True, db_index=True,
                                    help_text="Signed token printed on slips/advices before v2.1 (still verifies).")
    title = models.CharField(max_length=200)
    audience = models.CharField(max_length=20, default="institution")
    institution = models.ForeignKey("institutions.Institution", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="issued_documents")
    subject_ct = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    subject_id = models.PositiveBigIntegerField(null=True, blank=True)
    subject = GenericForeignKey("subject_ct", "subject_id")
    amount = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    signatories = models.JSONField(default=list, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    digest = models.CharField(max_length=64, blank=True, help_text="SHA-256 of the canonical payload.")
    public_fields = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=VALID, db_index=True)
    provisional = models.BooleanField(default=False)
    superseded_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    revoke_reason = models.CharField(max_length=500, blank=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    issued_at = models.DateTimeField(default=timezone.now, db_index=True)
    verify_count = models.PositiveIntegerField(default=0)
    last_verified_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-issued_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        indexes = [models.Index(fields=["institution", "kind"]), models.Index(fields=["subject_ct", "subject_id"])]
        permissions = [
            ("view_issued_documents", "Documents: can see the issued-documents register"),
            ("issue_document", "Documents: can issue a document by hand"),
            ("upload_official_copy", "Documents: can upload the signed official copy"),
            ("revoke_document", "Documents: can revoke or supersede an issued document"),
            ("view_verification_log", "Documents: can see the verification log"),
        ]

    def __str__(self):
        return f"{self.reference} ({self.get_kind_display()})"

    @property
    def kind_label(self):
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def official_copy(self):
        return OfficialCopy.unscoped.filter(document=self).order_by("-uploaded_at").first()

    def can_download(self, user):
        if (self.institution_id and self.audience != "internal" and getattr(user, "user_type", "") == "institution"
                and user.is_authenticated and user.institution_id == self.institution_id):
            return True
        return _is_staff(user) and (user.is_superuser or user.has_perm("documents.view_issued_documents")
                                    or self.issued_by_id == user.pk
                                    or any(s.get("user_id") == user.pk for s in self.signatories or []))


class OfficialCopy(InstitutionScopedModelMixin, QAFileMixin, TimeStampedModel):
    """X25: the signed official document NBTE uploads after a provisional
    copy. Uploading it supersedes the provisional status on the verify page."""

    AUDITED = True
    INSTITUTION_PATH = "document__institution"
    MEDIA_SUBDIR = "documents/official"
    INSTITUTION_MEDIA_KIND = "documents/official"

    document = models.ForeignKey(IssuedDocument, on_delete=models.CASCADE, related_name="official_copies")
    note = models.CharField(max_length=300, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    uploaded_at = models.DateTimeField(default=timezone.now)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-uploaded_at"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"Official copy of {self.document.reference}"

    def can_download(self, user):
        return self.document.can_download(user)


class DocumentOpen(models.Model):
    """X25: the provisional pop-up shows once per user per document."""

    document = models.ForeignKey(IssuedDocument, on_delete=models.CASCADE, related_name="opens")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    first_opened_at = models.DateTimeField(default=timezone.now)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["document", "user"], name="documents_open_once")]


class VerificationLog(models.Model):
    """Append-only record of every Scan / verify lookup (S-19)."""

    VIA_CHOICES = [("qr_camera", "Camera"), ("image_upload", "Uploaded image"), ("code", "Typed code"),
                   ("link", "Link")]
    RESULT_CHOICES = [("valid", "Valid"), ("superseded", "Superseded"), ("revoked", "Revoked"),
                      ("not_found", "Not found"), ("rate_limited", "Rate limited"), ("legacy", "Legacy slip/advice")]

    document = models.ForeignKey(IssuedDocument, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="verifications")
    token_or_code = models.CharField(max_length=80, blank=True, help_text="Hashed when nothing matched.")
    via = models.CharField(max_length=14, choices=VIA_CHOICES, default="link")
    host = models.CharField(max_length=10, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="+")
    result = models.CharField(max_length=14, choices=RESULT_CHOICES)
    at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-at"]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.result}"


# ===========================================================================
# Official letters pipeline (provisional -> signed official copy)
# ===========================================================================
class OfficialLetterJob(TimeStampedModel):
    """Follows one provisional document until its signed official copy is
    uploaded. Created lazily (first action, first reminder) - the queue reads
    the pending IssuedDocuments themselves, so nothing is ever missed.

    The handling department's Director (or the ES / a super admin) either
    uploads the official copy, or assigns a staff member to prepare it; the
    staff member sends a draft back, and the Director uploads the signed copy."""

    AUDITED = True

    WAITING, ASSIGNED, DRAFT_READY, RETURNED, DONE, CANCELLED = (
        "waiting", "assigned", "draft_ready", "returned", "done", "cancelled")
    STATE_CHOICES = [
        (WAITING, "Waiting for the official copy"),
        (ASSIGNED, "With staff to prepare"),
        (DRAFT_READY, "Draft ready – review & upload"),
        (RETURNED, "Sent back to staff"),
        (DONE, "Official copy uploaded"),
        (CANCELLED, "Closed (document revoked)"),
    ]
    OPEN_STATES = (WAITING, ASSIGNED, DRAFT_READY, RETURNED)
    WITH_STAFF = (ASSIGNED, RETURNED)

    document = models.OneToOneField(IssuedDocument, on_delete=models.CASCADE, related_name="official_job")
    department = models.ForeignKey("staff.Department", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", help_text="Handling department when the job started.")
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default=WAITING, db_index=True)
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="official_letter_jobs")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    assigned_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    assign_note = models.CharField(max_length=500, blank=True)
    task = models.ForeignKey("tasks.Task", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    review_comment = models.CharField(max_length=1000, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    reminded_at = models.DateTimeField(null=True, blank=True, help_text="Overdue reminder sent (once).")
    escalated_at = models.DateTimeField(null=True, blank=True, help_text="Escalated to the ES (once).")
    assignee_reminded_at = models.DateTimeField(null=True, blank=True)
    history = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Official letter for {self.document.reference} ({self.state})"

    @property
    def is_open(self):
        return self.state in self.OPEN_STATES

    def log(self, action, user=None, note=""):
        entry = {"at": timezone.now().isoformat(), "action": action, "note": (note or "")[:500]}
        if user is not None and getattr(user, "pk", None):
            entry["user_id"] = user.pk
            entry["user"] = user.get_full_name() or user.email
        self.history = list(self.history or []) + [entry]

    @property
    def latest_draft(self):
        return self.drafts.order_by("-uploaded_at", "-id").first()


class OfficialLetterDraft(QAFileMixin, TimeStampedModel):
    """A draft of the official letter prepared by the assigned staff member."""

    AUDITED = True
    MEDIA_SUBDIR = "documents/official_drafts"

    job = models.ForeignKey(OfficialLetterJob, on_delete=models.CASCADE, related_name="drafts")
    note = models.CharField(max_length=1000, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-uploaded_at", "-id"]

    def __str__(self):
        return f"Draft for {self.job.document.reference}"

    def can_download(self, user):
        from . import official_letters

        return official_letters.can_open(user, self.job.document, job=self.job)
