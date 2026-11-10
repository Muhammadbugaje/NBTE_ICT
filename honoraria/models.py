"""Honoraria (v2.1). Phase B: staff bank details (X33, S-28).

Owner decisions (28 Oct 2026, PROGRESS.md):
* Bank details live on each staff member's own profile and are used for SSQ,
  visit and other payments.
* ONLY the owner can view or change them - not super admin, not anyone using
  "act as", not HR. There is no admin page, no staff-profile field and no API
  for other people. A change needs the owner's password (no two-person
  approval); every change is logged and the owner is e-mailed.
* Account numbers are encrypted at rest (core.fieldcrypto, FIELD_ENCRYPTION_KEY)
  and masked everywhere. Phase H's payment schedule reads full numbers only
  through honoraria.disbursement (logged).
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import TimeStampedModel


class Bank(TimeStampedModel):
    """Nigerian banks with CBN codes (seed_nbte set `payments.banks`, data file
    core/seed_data/finance/banks.json - unverified list; Finance to confirm)."""

    AUDITED = True

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    cbn_code = models.CharField(max_length=10, blank=True, help_text="CBN / NIBSS bank code.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class PayeeBankAccount(TimeStampedModel):
    ACTIVE, SUPERSEDED = "active", "superseded"
    STATUS_CHOICES = [(ACTIVE, "In use"), (SUPERSEDED, "Replaced")]
    MATCH_CHOICES = [("exact", "Matches your name"), ("partial", "Partly matches your name"),
                     ("mismatch", "Does not match your name"), ("unchecked", "Not checked")]

    AUDITED = True
    # Never write account data into the shared audit trail; BankDetailsLog
    # records what changed (masked) for the owner.
    AUDIT_EXCLUDE_FIELDS = ("account_number_enc", "account_hash", "account_last4", "account_name")

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE,
                             related_name="bank_accounts")
    resource_person = models.ForeignKey("inspection.ResourcePerson", null=True, blank=True,
                                        on_delete=models.CASCADE, related_name="bank_accounts")
    bank = models.ForeignKey(Bank, on_delete=models.PROTECT, related_name="+")
    account_number_enc = models.TextField(help_text="Fernet token; never shown, never exported.")
    account_hash = models.CharField(max_length=64, blank=True, db_index=True)
    account_last4 = models.CharField(max_length=4)
    account_name = models.CharField(max_length=150)
    name_match = models.CharField(max_length=12, choices=MATCH_CHOICES, default="unchecked")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=ACTIVE, db_index=True)
    confirmed_at = models.DateTimeField(null=True, blank=True,
                                        help_text="When the owner last confirmed the details are correct.")
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(user__isnull=False, resource_person__isnull=True)
                           | Q(user__isnull=True, resource_person__isnull=False)),
                name="honoraria_payee_exactly_one"),
            models.UniqueConstraint(fields=["user"], condition=Q(status="active", user__isnull=False),
                                    name="honoraria_one_active_account_per_user"),
            models.UniqueConstraint(fields=["resource_person"],
                                    condition=Q(status="active", resource_person__isnull=False),
                                    name="honoraria_one_active_account_per_rp"),
        ]
        default_permissions = ()  # no add/change/view/delete permission can expose these rows

    def __str__(self):
        return f"Bank account ••{self.account_last4} ({self.get_status_display()})"

    @property
    def masked(self):
        from core.fieldcrypto import mask

        return mask(self.account_last4)


class BankDetailsLog(models.Model):
    """Append-only history of a person's bank details (masked values only)."""

    CREATED, CHANGED, CONFIRMED, REVEALED, RELEASED, BLOCKED = (
        "created", "changed", "confirmed", "revealed", "released_for_payment", "blocked")
    ACTION_CHOICES = [(CREATED, "Added"), (CHANGED, "Changed"), (CONFIRMED, "Confirmed correct"),
                      (REVEALED, "Full number shown to owner"), (RELEASED, "Released to a payment schedule"),
                      (BLOCKED, "Blocked attempt")]

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    account = models.ForeignKey(PayeeBankAccount, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="log")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="+")
    action = models.CharField(max_length=24, choices=ACTION_CHOICES, db_index=True)
    before = models.CharField(max_length=200, blank=True, help_text="Masked: bank and last four digits.")
    after = models.CharField(max_length=200, blank=True)
    note = models.CharField(max_length=255, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-at", "-id"]
        default_permissions = ()

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.action}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("BankDetailsLog is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("BankDetailsLog is append-only.")


# ===========================================================================
# v2.1 X32/X34 (Phase H): honoraria payment schedules
# ===========================================================================
class PaymentSchedule(TimeStampedModel):
    """One payment run: the internal items and academic resource persons whose
    work was completed in the period. Built from settings rates, approved by
    Finance, then paid."""

    AUDITED = True
    STATUS_CHOICES = [("draft", "Draft"), ("approved", "Approved"), ("paid", "Paid"),
                      ("cancelled", "Cancelled")]

    reference = models.CharField(max_length=60, unique=True)
    title = models.CharField(max_length=200, blank=True)
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="draft", db_index=True)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    funding_account = models.ForeignKey("payments.BankAccount", null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name="honoraria_schedules")
    funding_bank_name = models.CharField(max_length=120, blank=True)
    funding_account_last4 = models.CharField(max_length=4, blank=True)
    payment_reference = models.CharField(max_length=100, blank=True)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="+")
    # v2.3: a batch prepared from an institution's honorarium file (pay officer
    # prepares, the Director of Finance approves with one click).
    honorarium_file = models.ForeignKey("HonorariumFile", null=True, blank=True, on_delete=models.PROTECT,
                                        related_name="batches")

    class Meta:
        ordering = ["-period_end", "-id"]
        permissions = [("approve_payment_schedule", "QA: can approve a honoraria payment schedule"),
                       ("pay_payment_schedule", "QA: can mark a honoraria payment schedule paid"),
                       ("view_all_payment_schedules", "QA: can see every honoraria payment schedule")]

    def __str__(self):
        return self.reference


class PaymentScheduleLine(TimeStampedModel):
    """One person's line on a schedule. Rates are frozen when the line is
    written; the person confirms receipt, Finance marks it paid."""

    AUDITED = True
    INTERNAL, ACADEMIC, OTHER = "internal_item", "academic_resource_person", "other"
    SOURCE_CHOICES = [(INTERNAL, "Internal SSQ / document item"), (ACADEMIC, "Academic resource person"),
                      (OTHER, "Other")]

    schedule = models.ForeignKey(PaymentSchedule, on_delete=models.CASCADE, related_name="lines")
    entries = models.ManyToManyField("HonorariumEntry", blank=True, related_name="schedule_lines")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.PROTECT, related_name="honorarium_lines")
    resource_person = models.ForeignKey("inspection.ResourcePerson", null=True, blank=True,
                                        on_delete=models.PROTECT, related_name="honorarium_lines")
    name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=150, blank=True)
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, default=OTHER)
    quantity = models.PositiveIntegerField(default=1)
    rate = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_override = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True,
                                        help_text="The payee confirmed receipt.")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    paid_at = models.DateTimeField(null=True, blank=True)
    funding_account = models.ForeignKey(
        "payments.BankAccount", null=True, blank=True, on_delete=models.SET_NULL, related_name="honoraria_lines")
    funding_bank_name = models.CharField(max_length=120, blank=True)
    funding_account_last4 = models.CharField(max_length=4, blank=True)
    payment_reference = models.CharField(max_length=100, blank=True)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["schedule", "name", "pk"]

    def __str__(self):
        return f"{self.name or self.user or self.resource_person}: {self.amount}"

    @property
    def person(self):
        return self.resource_person or self.user

    def save(self, *args, **kwargs):
        if self.rate and self.quantity and not self.amount_override:
            self.amount = self.rate * self.quantity
        super().save(*args, **kwargs)


class HonorariumServiceRecordClaim(TimeStampedModel):
    """Reserve each append-only service record for at most one live payout."""

    RESERVED, PAID, RELEASED = "reserved", "paid", "released"
    STATUS_CHOICES = [(RESERVED, "Reserved"), (PAID, "Paid"), (RELEASED, "Released")]

    service_record = models.ForeignKey(
        "inspection.ServiceRecord", on_delete=models.PROTECT, related_name="honorarium_claims")
    line = models.ForeignKey(
        PaymentScheduleLine, on_delete=models.CASCADE, related_name="service_record_claims")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=RESERVED, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["service_record"], condition=Q(status__in=("reserved", "paid")),
                name="honoraria_live_service_record_once"),
        ]


class HonorariumEntry(TimeStampedModel):
    """A single payable work item, snapshotted when its assignment completes."""

    AUDITED = True
    ACCRUED, SCHEDULED, PAID, RECEIVED, VOIDED = (
        "accrued", "scheduled", "paid", "received", "voided")
    STATUS_CHOICES = [(ACCRUED, "Accrued"), (SCHEDULED, "Scheduled"), (PAID, "Paid"),
                      (RECEIVED, "Receipt confirmed"), (VOIDED, "Voided")]
    DOCUMENT_ANALYSIS, SSQ_REVIEW = "document_analysis_internal", "ssq_review_internal"
    SSQ_REVIEW_EXTERNAL = "ssq_review_external"  # v2.2: resource person (external) SSQ review
    KIND_CHOICES = [(DOCUMENT_ANALYSIS, "Document analysis"), (SSQ_REVIEW, "SSQ review"),
                    (SSQ_REVIEW_EXTERNAL, "External SSQ review")]

    payee_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="honorarium_earnings")
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    source_ct = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    source_id = models.CharField(max_length=64)
    source = GenericForeignKey("source_ct", "source_id")
    institution = models.ForeignKey("institutions.Institution", null=True, blank=True,
                                    on_delete=models.PROTECT, related_name="+")
    department = models.ForeignKey("staff.Department", null=True, blank=True,
                                   on_delete=models.PROTECT, related_name="+")
    rate_key = models.CharField(max_length=80)
    quantity = models.PositiveIntegerField(default=1)
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=ACCRUED, db_index=True)
    schedule = models.ForeignKey(PaymentSchedule, null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="entries")
    accrued_at = models.DateTimeField(default=timezone.now, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    paid_reference = models.CharField(max_length=100, blank=True)
    void_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-accrued_at", "-pk"]
        indexes = [models.Index(fields=["status", "accrued_at"], name="hon_entry_status_date"),  # v2.2 roller
                   models.Index(fields=["payee_user", "status"], name="hon_entry_payee_status")]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "source_ct", "source_id", "payee_user"],
                condition=~Q(status="voided"),
                name="honoraria_unique_live_work_entry"),
        ]
        permissions = [
            ("view_department_earnings", "QA: can view own department's honorarium earnings"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.payee_user} · ₦{self.amount}"


# ===========================================================================
# v2.3: one honorarium file per institution (SOP-01 -> SOP-03)
# ===========================================================================
class HonorariumFile(TimeStampedModel):
    """Everything NBTE owes people for work on one institution, from the
    SOP-01 document reviews through SOP-02 SSQ reviews to the SOP-03 visit.
    Lines are added automatically as each stage completes; unpaid lines from a
    later stage join the same file and paid lines stay as history."""

    AUDITED = True

    institution = models.OneToOneField("institutions.Institution", on_delete=models.PROTECT,
                                       related_name="honorarium_file")
    reference = models.CharField(max_length=60, unique=True)
    pay_officer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", help_text="Finance officer the DoF asked to prepare payments.")
    pay_officer_assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                                on_delete=models.SET_NULL, related_name="+")
    pay_officer_assigned_at = models.DateTimeField(null=True, blank=True)
    last_stage = models.CharField(max_length=8, blank=True)
    last_announced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at", "-pk"]

    def __str__(self):
        return f"{self.reference} — {self.institution}"


class HonorariumFileLine(TimeStampedModel):
    """One payable work item of one person on an institution's file."""

    AUDITED = True
    SOP01, SOP02, SOP03 = "sop01", "sop02", "sop03"
    STAGE_CHOICES = [(SOP01, "SOP-01 · Documents"), (SOP02, "SOP-02 · SSQ review"), (SOP03, "SOP-03 · Visit")]
    PENDING, PREPARED, APPROVED, PAID, CONFIRMED, SETTLED, VOIDED = (
        "pending", "prepared", "approved", "paid", "confirmed", "settled", "voided")
    STATUS_CHOICES = [(PENDING, "Ready to pay"), (PREPARED, "In a prepared batch"), (APPROVED, "Approved"),
                      (PAID, "Paid — awaiting confirmation"), (CONFIRMED, "Receipt confirmed"),
                      (SETTLED, "Settled by case fund release"), (VOIDED, "Voided")]
    OUTSTANDING = (PENDING, PREPARED, APPROVED)

    file = models.ForeignKey(HonorariumFile, on_delete=models.CASCADE, related_name="lines")
    stage = models.CharField(max_length=8, choices=STAGE_CHOICES, db_index=True)
    source_key = models.CharField(max_length=80, unique=True,
                                  help_text="The work item (entry:<id>, cost:<id>, svc:<id>): added once only.")
    entry = models.ForeignKey(HonorariumEntry, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    service_record = models.ForeignKey("inspection.ServiceRecord", null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="+")
    cost_line = models.ForeignKey("accreditation.CostLine", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    case = models.ForeignKey("accreditation.QACase", null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="+")
    payee_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="honorarium_file_lines")
    resource_person = models.ForeignKey("inspection.ResourcePerson", null=True, blank=True,
                                        on_delete=models.PROTECT, related_name="+")
    name = models.CharField(max_length=150)
    role = models.CharField(max_length=150, blank=True)
    work_item = models.CharField(max_length=255, blank=True)
    rate_key = models.CharField(max_length=80, blank=True)
    quantity = models.DecimalField(max_digits=8, decimal_places=1, default=1)
    rate = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    schedule_line = models.ForeignKey(PaymentScheduleLine, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="file_lines")
    note = models.CharField(max_length=255, blank=True)
    accrued_at = models.DateTimeField(default=timezone.now)
    announced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["stage", "accrued_at", "pk"]

    def __str__(self):
        return f"{self.name} · {self.get_stage_display()} · ₦{self.amount}"

    @property
    def payee_key(self):
        return f"rp:{self.resource_person_id}" if self.resource_person_id else f"user:{self.payee_user_id}"


class PayOfficerDelegation(TimeStampedModel):
    """The standing pay officer the Director of Finance chose once: new
    honorarium files go to them for preparation unless a file names another."""

    AUDITED = True

    officer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    is_active = models.BooleanField(default=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["is_active"], condition=Q(is_active=True),
                                               name="honoraria_one_active_pay_officer")]

    def __str__(self):
        return f"Pay officer {self.officer}"
