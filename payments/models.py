"""Payments (QA) — Master Plan §5.3 and §10, Phase 2.

The rule this app exists to enforce (v1.1, Amendment 1; locked setting
``qa.payment.provider_confirmation_is_evidence_only``):

    A provider report, an uploaded proof or a statement match is EVIDENCE.
    An obligation reaches ``confirmed`` only through a PaymentVerification
    whose ``final_decision == "confirmed"``, decided by the holder of the role
    in ``qa.payment.final_approver_role`` (the Director of Finance), who is
    not the first-pass officer. There is no other path in code.

``payments.services`` is the only module that changes an obligation's state,
and it does so only through the seeded ``payment`` flow.

Added beyond §5.3 (PHASE2_CHANGELOG.md): ``PaymentCredit`` (overpayment held
as credit, §10.6) and ``WebhookLog`` (the ICT monitor's webhook log, §13).
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel
from qa_core.files import QAFileMixin
from qa_core.scoping import (
    InstitutionScopedManager,
    InstitutionScopedModelMixin,
    UnscopedInstitutionManager,
)

USER = settings.AUTH_USER_MODEL
ZERO = Decimal("0.00")
MONEY = {"max_digits": 16, "decimal_places": 2}

nuban_validator = RegexValidator(r"^\d{10}$", "A NUBAN account number is exactly 10 digits.")


def _is_staff(user):
    return bool(user and user.is_authenticated and getattr(user, "user_type", "staff") == "staff")


def _is_institution_member(user, institution_id):
    return bool(user and user.is_authenticated and getattr(user, "user_type", "") == "institution"
                and user.institution_id == institution_id)


# ===========================================================================
# Configuration tables (dual control)
# ===========================================================================
class BankAccount(TimeStampedModel):
    """NBTE revenue account printed on slips. Changes need a second officer."""

    AUDITED = True

    bank_name = models.CharField(max_length=120)
    account_name = models.CharField(max_length=150)
    account_number = models.CharField(max_length=10, validators=[nuban_validator])
    sort_code = models.CharField(max_length=20, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=False)
    is_placeholder = models.BooleanField(default=False,
                                         help_text="Seeded placeholder: slips cannot be issued against it.")

    class Meta:
        ordering = ["-is_default", "bank_name"]

    def __str__(self):
        return f"{self.bank_name} · {self.account_number}"

    @property
    def usable(self):
        return self.is_active and not self.is_placeholder and self.account_number != "0000000000"


class FeeSchedule(TimeStampedModel):
    AUDITED = True

    code = models.SlugField(max_length=60, unique=True)
    name = models.CharField(max_length=150)
    description = models.CharField(max_length=255, blank=True,
                                   help_text="Printed on the slip, e.g. 'Institutional processing fee'.")
    applies_to = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    formula = models.CharField(max_length=150, help_text="Key of a formula setting, e.g. qa.fee.programme_processing.")
    confirmed_setting = models.CharField(
        max_length=150, blank=True,
        help_text="Optional bool setting that must be true before bills can be issued (D15).")
    currency = models.CharField(max_length=3, default="NGN")
    due_days = models.PositiveIntegerField(null=True, blank=True, help_text="Blank = qa.payment.due_days.")
    remita_service_type_id = models.CharField(max_length=40, blank=True)
    revenue_account = models.ForeignKey(BankAccount, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="fee_schedules")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# ===========================================================================
# The bill
# ===========================================================================
class PaymentObligation(InstitutionScopedModelMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "institution"
    FLOW_CODE = "payment"
    FLOW_STATUS_FIELD = "status"

    OPEN_FOR_PAYMENT = ("issued", "awaiting_payment", "rejected", "partially_paid")
    CLAIMED = ("provider_processing", "provider_reported_paid", "proof_submitted",
               "awaiting_finance_confirmation", "finance_confirmation_overdue")
    SETTLED = ("confirmed", "overpaid", "waived", "refund_requested", "refunded")

    reference = models.CharField(max_length=60, unique=True)
    institution = models.ForeignKey("institutions.Institution", on_delete=models.PROTECT,
                                    related_name="payment_obligations")
    source_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    source_id = models.CharField(max_length=64, blank=True)
    source = GenericForeignKey("source_type", "source_id")
    source_reference = models.CharField(max_length=60, blank=True,
                                        help_text="Application or QA request reference printed on the slip.")
    fee_schedule = models.ForeignKey(FeeSchedule, on_delete=models.PROTECT, related_name="obligations")
    description = models.CharField(max_length=255)
    currency = models.CharField(max_length=3, default="NGN")
    amount = models.DecimalField(**MONEY)
    amount_in_words = models.CharField(max_length=400)
    formula_snapshot = models.JSONField(default=dict)
    due_at = models.DateTimeField()
    status = models.CharField(max_length=40, default="issued", db_index=True)
    amount_paid = models.DecimalField(default=ZERO, **MONEY)
    balance = models.DecimalField(default=ZERO, **MONEY)
    overpaid_amount = models.DecimalField(default=ZERO, **MONEY)
    credit_applied = models.DecimalField(default=ZERO, **MONEY)
    remita_rrr = models.CharField(max_length=20, blank=True)
    waived_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    waiver_reason = models.TextField(blank=True)
    waived_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.TextField(blank=True)
    flags = models.JSONField(default=list, blank=True, help_text="duplicate, paid_after_cancellation …")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        # Master Plan §12: every payments.* codename (v1.1 adds final_confirm_payment).
        permissions = [
            ("view_obligation", "QA: can see QA payment obligations"),
            ("view_proof", "QA: can open proofs of payment"),
            ("verify_payment", "QA: Finance first pass on payments"),
            ("second_verify_payment", "QA: second first-pass check on large payments"),
            ("final_confirm_payment", "QA: final confirm/reject of payments (Director of Finance)"),
            ("import_bank_statement", "QA: can import bank statements"),
            ("manual_match_payment", "QA: can match statement lines by hand"),
            ("initiate_waiver", "QA: can propose a fee waiver"),
            ("confirm_waiver", "QA: can confirm a fee waiver"),
            ("initiate_refund", "QA: can request a refund"),
            ("confirm_refund", "QA: can confirm a refund"),
            ("initiate_cancel_obligation", "QA: can propose cancelling a bill"),
            ("confirm_cancel_obligation", "QA: can confirm cancelling a bill"),
            ("manage_fee_schedule", "QA: can change fee schedules (dual control)"),
            ("manage_bank_accounts", "QA: can change bank accounts (dual control)"),
            ("view_payment_reports", "QA: can see payment reports"),
        ]

    def __str__(self):
        return self.reference

    @property
    def is_open_for_payment(self):
        return self.status in self.OPEN_FOR_PAYMENT

    @property
    def is_settled(self):
        return self.status in self.SETTLED


class PaymentSlip(InstitutionScopedModelMixin, QAFileMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "obligation__institution"
    MEDIA_SUBDIR = "qa/slips"
    INSTITUTION_MEDIA_KIND = "payments/slips"

    obligation = models.ForeignKey(PaymentObligation, on_delete=models.CASCADE, related_name="slips")
    version = models.PositiveIntegerField(default=1)
    amount = models.DecimalField(**MONEY)
    amount_in_words = models.CharField(max_length=400)
    qr_payload = models.CharField(max_length=500)
    barcode_value = models.CharField(max_length=60)
    remita_rrr = models.CharField(max_length=20, blank=True)
    bank_snapshot = models.JSONField(default=dict)
    generated_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    is_current = models.BooleanField(default=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-version"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.obligation.reference} slip v{self.version}"

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    def can_download(self, user):
        # Institution-facing document: the institution's own users only (and
        # NBTE staff only through a read-only impersonation session, where
        # request.user *is* an institution user).
        return _is_institution_member(user, self.obligation.institution_id)


class PaymentAttempt(InstitutionScopedModelMixin, TimeStampedModel):
    """One try to pay through a provider. Evidence, never truth."""

    AUDITED = True
    INSTITUTION_PATH = "obligation__institution"
    STATUS_CHOICES = [("initiated", "Initiated"), ("pending", "Pending"),
                      ("provider_reported_paid", "Provider reported paid (evidence)"),
                      ("failed", "Failed"), ("abandoned", "Abandoned")]
    PROVIDER_CHOICES = [("remita", "Remita"), ("paystack", "Paystack"),
                        ("bank_transfer", "Bank transfer"), ("offline", "Offline / waived")]

    obligation = models.ForeignKey(PaymentObligation, on_delete=models.CASCADE, related_name="attempts")
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    provider_reference = models.CharField(max_length=100, blank=True, db_index=True)
    amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="initiated", db_index=True)
    checkout_url = models.URLField(max_length=500, blank=True)
    raw_request = models.JSONField(default=dict, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)
    verify_result = models.JSONField(default=dict, blank=True)
    webhook_verified = models.BooleanField(default=False)
    failure_reason = models.CharField(max_length=255, blank=True)
    flags = models.JSONField(default=list, blank=True)
    initiated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"{self.get_provider_display()} {self.provider_reference or '#' + str(self.pk)}"


class ProofOfPayment(InstitutionScopedModelMixin, QAFileMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "obligation__institution"
    MEDIA_SUBDIR = "qa/proofs"
    INSTITUTION_MEDIA_KIND = "payments/proofs"
    KIND_CHOICES = [("teller", "Bank teller / transfer receipt"), ("provider_receipt", "Online payment receipt")]

    obligation = models.ForeignKey(PaymentObligation, on_delete=models.CASCADE, related_name="proofs")
    attempt = models.ForeignKey(PaymentAttempt, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="receipts")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default="teller")
    payer_name = models.CharField(max_length=150, blank=True)
    paid_on = models.DateField(null=True, blank=True)
    amount_claimed = models.DecimalField(default=ZERO, **MONEY)
    bank_name = models.CharField(max_length=120, blank=True)
    teller_number = models.CharField(max_length=60, blank=True)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    fraud_flag = models.BooleanField(default=False)
    duplicate_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        verbose_name_plural = "proofs of payment"

    def __str__(self):
        return f"Proof for {self.obligation.reference} ({self.teller_number or self.display_name})"

    def can_download(self, user):
        """§10.4: the owning institution's users, the Finance group, the
        Director of Finance, the ES and holders of payments.view_proof."""
        if _is_institution_member(user, self.obligation.institution_id):
            return True
        if not _is_staff(user):
            return False
        role = getattr(getattr(user, "role", None), "code", "")
        return bool(user.is_superuser or user.has_perm("payments.view_proof")
                    or user.has_perm("payments.final_confirm_payment") or role == "executive-secretary")


class BankStatementImport(QAFileMixin, TimeStampedModel):
    AUDITED = True
    MEDIA_SUBDIR = "qa/statements"

    bank_account = models.ForeignKey(BankAccount, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="statements")
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    column_map = models.JSONField(default=dict, blank=True)
    parsed_rows = models.PositiveIntegerField(default=0)
    matched = models.PositiveIntegerField(default=0)
    ambiguous = models.PositiveIntegerField(default=0)
    unmatched = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=12, default="parsed")
    error = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Statement #{self.pk} ({self.original_name})"

    def can_download(self, user):
        return _is_staff(user) and (user.is_superuser or user.has_perm("payments.import_bank_statement")
                                    or user.has_perm("payments.final_confirm_payment"))


class BankStatementLine(TimeStampedModel):
    AUDITED = True
    MATCHED, AMBIGUOUS, UNMATCHED, IGNORED = "matched", "ambiguous", "unmatched", "ignored"
    MATCH_CHOICES = [(MATCHED, "Matched"), (AMBIGUOUS, "Ambiguous"), (UNMATCHED, "Unmatched"),
                     (IGNORED, "Ignored")]

    statement = models.ForeignKey(BankStatementImport, on_delete=models.CASCADE, related_name="lines")
    row_number = models.PositiveIntegerField()
    value_date = models.DateField(null=True, blank=True)
    narration = models.TextField(blank=True)
    amount = models.DecimalField(default=ZERO, **MONEY)
    extracted_reference = models.CharField(max_length=60, blank=True)
    reference_check_failed = models.BooleanField(default=False, help_text="Mod-97 typo detected.")
    candidates = models.JSONField(default=list, blank=True)
    match_status = models.CharField(max_length=10, choices=MATCH_CHOICES, default=UNMATCHED, db_index=True)
    matched_obligation = models.ForeignKey(PaymentObligation, null=True, blank=True, on_delete=models.SET_NULL,
                                           related_name="statement_lines")
    matched_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    matched_at = models.DateTimeField(null=True, blank=True)
    match_remark = models.TextField(blank=True)
    flags = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["statement", "row_number"]

    def __str__(self):
        return f"Line {self.row_number} of statement {self.statement_id}"


class PaymentVerification(InstitutionScopedModelMixin, TimeStampedModel):
    """The Finance queue item: first pass by an officer, final decision by the
    Director of Finance (who may not be the first-pass officer)."""

    AUDITED = True
    INSTITUTION_PATH = "obligation__institution"
    SOURCE_CHOICES = [("proof", "Uploaded proof"), ("provider", "Provider report"),
                      ("statement", "Bank statement match"), ("manual", "Manual entry")]
    DECISION_CHOICES = [("approved", "Recommend confirm"), ("rejected", "Recommend reject"),
                        ("needs_info", "Needs information")]
    FINAL_CHOICES = [("confirmed", "Confirmed"), ("rejected", "Rejected")]

    obligation = models.ForeignKey(PaymentObligation, on_delete=models.CASCADE, related_name="verifications")
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES)
    attempt = models.ForeignKey(PaymentAttempt, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    proof = models.ForeignKey(ProofOfPayment, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    statement_line = models.ForeignKey(BankStatementLine, null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="+")
    is_open = models.BooleanField(default=True, db_index=True)
    opened_at = models.DateTimeField(default=timezone.now)
    sla_due_at = models.DateTimeField(null=True, blank=True)
    reminders_sent = models.JSONField(default=list, blank=True)
    # First pass
    decision = models.CharField(max_length=12, choices=DECISION_CHOICES, blank=True)
    amount_confirmed = models.DecimalField(null=True, blank=True, **MONEY)
    reason = models.TextField(blank=True)
    verified_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    # Second first-pass check (above qa.payment.dual_verify_threshold)
    second_decision = models.CharField(max_length=12, choices=DECISION_CHOICES, blank=True)
    second_reason = models.TextField(blank=True)
    second_verifier = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    second_verified_at = models.DateTimeField(null=True, blank=True)
    # Final decision (Director of Finance)
    final_approver = models.ForeignKey(USER, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    final_decision = models.CharField(max_length=10, choices=FINAL_CHOICES, blank=True)
    final_reason = models.TextField(blank=True)
    final_decided_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["opened_at", "id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"Verification #{self.pk} · {self.obligation.reference}"


class Refund(InstitutionScopedModelMixin, TimeStampedModel):
    AUDITED = True
    INSTITUTION_PATH = "obligation__institution"
    FLOW_STATUS_FIELD = "status"
    SOURCE_CHOICES = [("overpayment", "Overpayment"), ("duplicate", "Duplicate payment"),
                      ("after_cancellation", "Paid after cancellation"), ("other", "Other")]

    obligation = models.ForeignKey(PaymentObligation, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(**MONEY)
    reason = models.TextField()
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="other")
    status = models.CharField(max_length=40, default="requested", db_index=True)
    requested_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"Refund #{self.pk} · {self.obligation.reference}"


class PaymentAdvice(InstitutionScopedModelMixin, QAFileMixin, TimeStampedModel):
    """A4/R-26: generated automatically when the Director of Finance confirms."""

    AUDITED = True
    INSTITUTION_PATH = "obligation__institution"
    MEDIA_SUBDIR = "qa/advices"
    INSTITUTION_MEDIA_KIND = "payments/advices"

    obligation = models.ForeignKey(PaymentObligation, on_delete=models.PROTECT, related_name="advices")
    verification = models.ForeignKey(PaymentVerification, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="+")
    reference = models.CharField(max_length=60, unique=True)
    amount = models.DecimalField(**MONEY)
    qr_payload = models.CharField(max_length=500)
    generated_at = models.DateTimeField(default=timezone.now)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-generated_at"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return self.reference

    def can_download(self, user):
        if _is_institution_member(user, self.obligation.institution_id):
            return True
        return _is_staff(user) and (user.is_superuser or user.has_perm("payments.view_obligation")
                                    or user.has_perm("payments.final_confirm_payment"))


class PaymentCredit(InstitutionScopedModelMixin, TimeStampedModel):
    """An overpayment Finance chose to hold as credit (§10.6)."""

    AUDITED = True
    INSTITUTION_PATH = "institution"
    STATUS_CHOICES = [("held", "Held"), ("applied", "Applied"), ("refunded", "Refunded")]

    institution = models.ForeignKey("institutions.Institution", on_delete=models.PROTECT,
                                    related_name="payment_credits")
    amount = models.DecimalField(**MONEY)
    source_obligation = models.ForeignKey(PaymentObligation, on_delete=models.PROTECT, related_name="credits_out")
    applied_to = models.ForeignKey(PaymentObligation, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="credits_in")
    applied_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="held")
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.TextField(blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"

    def __str__(self):
        return f"Credit {self.amount} from {self.source_obligation.reference}"


class WebhookLog(models.Model):
    """Every provider callback, valid or not (ICT monitor: webhook log)."""

    provider = models.CharField(max_length=20)
    received_at = models.DateTimeField(default=timezone.now, db_index=True)
    remote_ip = models.CharField(max_length=60, blank=True)
    signature_valid = models.BooleanField(default=False)
    reference = models.CharField(max_length=100, blank=True, db_index=True)
    body = models.TextField(blank=True)
    outcome = models.CharField(max_length=255, blank=True)
    attempt = models.ForeignKey(PaymentAttempt, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-received_at", "-id"]

    def __str__(self):
        return f"{self.provider} webhook @ {self.received_at:%Y-%m-%d %H:%M:%S} ({self.outcome})"
