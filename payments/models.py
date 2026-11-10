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


class FeeComponent(TimeStampedModel):
    """v2.1 F.4 (P1): one line of a bill — the base fee, the processing fee
    added to every NBTE bill, or any other charge. A schedule with no active
    component bills its formula as one line (the v2 behaviour).

    ``amount_setting`` names a ConfigEntry (decimal or formula); blank on the
    base component means the schedule's own formula. ``mode=percent`` reads
    the setting as a percentage of the base line. ``institution_type`` blank
    = every type; a typed row replaces the untyped row with the same ``kind``
    for that type. Amounts NBTE has not confirmed carry ``to_confirm``."""

    AUDITED = True
    KIND_CHOICES = [("base", "Base fee"), ("processing", "Processing fee"), ("other", "Other charge")]
    MODE_CHOICES = [("fixed", "Fixed amount"), ("percent", "Percent of the base fee")]

    schedule = models.ForeignKey(FeeSchedule, on_delete=models.CASCADE, related_name="components")
    code = models.SlugField(max_length=60)
    label = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True, help_text="Small print under the line on the bill.")
    kind = models.CharField(max_length=12, choices=KIND_CHOICES, default="other")
    amount_setting = models.CharField(max_length=150, blank=True,
                                      help_text="Setting key giving the amount (blank on the base line = the "
                                                "schedule's formula).")
    mode = models.CharField(max_length=8, choices=MODE_CHOICES, default="fixed")
    institution_type = models.ForeignKey("institutions.InstitutionType", null=True, blank=True,
                                         on_delete=models.SET_NULL, related_name="+")
    order = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)
    to_confirm = models.BooleanField(default=False, help_text="Amount not yet confirmed by NBTE ([FEE AMOUNT]).")

    class Meta:
        ordering = ["schedule", "order", "id"]
        constraints = [models.UniqueConstraint(fields=["schedule", "code"], name="payments_feecomponent_code")]

    def __str__(self):
        return f"{self.schedule.code}: {self.label}"


class InstitutionTypeFee(TimeStampedModel):
    """v2.2: one row per institution category (Polytechnic, College of Nursing,
    College of Health …) holding every fee NBTE charges that category. Finance
    edits the whole table on one Settings screen; billing reads it first and
    only falls back to the older formula/component settings when a category has
    no row. A zero amount means "no such line"; a row with nothing above zero
    blocks billing for that category until Finance fills it in."""

    AUDITED = True

    institution_type = models.OneToOneField("institutions.InstitutionType", on_delete=models.CASCADE,
                                            related_name="fee_row")
    # SOP-01 — new (proposed) institution
    registration_fee = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Institutional registration fee (SOP-01, paid once).")
    registration_processing_fee = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Processing fee added to the registration bill.")
    # Programmes added to NBTE (SOP-01 step 7 / new programme)
    programme_fee = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, help_text="Fee per programme.")
    programme_processing_fee = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Processing fee on a programme bill.")
    programme_processing_per_programme = models.BooleanField(
        default=False, help_text="On: the processing fee is charged for each programme. Off: once per bill.")
    extra_stream_fee = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Fee for each stream asked for beyond one per programme.")
    # SOP-02 — existing institution QA requests (accreditation, re-accreditation …)
    qa_request_base_fee = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, help_text="Fixed part of a QA request bill.")
    qa_request_fee_per_programme = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, help_text="QA request fee per programme.")
    qa_request_processing_fee = models.DecimalField(
        max_digits=14, decimal_places=2, default=0, help_text="Processing fee on a QA request bill.")
    is_confirmed = models.BooleanField(
        default=False, help_text="Finance has confirmed these figures (unconfirmed rows show a warning).")
    is_active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        ordering = ["institution_type__sort_order", "institution_type__name"]
        verbose_name = "fee by institution category"
        verbose_name_plural = "fees by institution category"

    def __str__(self):
        return f"Fees · {self.institution_type}"

    @classmethod
    def for_institution(cls, institution):
        type_id = getattr(institution, "institution_type_id", None)
        if not type_id and getattr(institution, "category_id", None):
            type_id = getattr(institution.category, "institution_type_id", None)
        if not type_id:
            return None
        return cls.objects.filter(institution_type_id=type_id, is_active=True).select_related(
            "institution_type").first()


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
    # v2.1 F.4 (P9): the method the institution chose; the others are hidden.
    METHOD_CHOICES = [("remita", "Remita"), ("paystack", "Paystack"), ("bank_transfer", "Bank transfer")]
    chosen_method = models.CharField(max_length=20, choices=METHOD_CHOICES, blank=True)
    chosen_at = models.DateTimeField(null=True, blank=True)
    # v2.1 F.4 (P10): the institution was taken to its dashboard welcome once.
    welcomed_at = models.DateTimeField(null=True, blank=True)

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["institution", "status"], name="pay_ob_inst_status")]  # v2.2
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

    @property
    def breakdown(self):
        """[{code, label, description, kind, amount}] frozen at issue (F.4)."""
        lines = (self.formula_snapshot or {}).get("components") or []
        if lines:
            return [{**line, "amount": Decimal(str(line.get("amount") or 0))} for line in lines]
        return [{"code": "fee", "label": self.fee_schedule.name if self.fee_schedule_id else self.description,
                 "description": "", "kind": "base", "amount": self.amount}]


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
        indexes = [models.Index(fields=["obligation", "status"], name="pay_attempt_ob_status"),  # v2.2
                   models.Index(fields=["provider", "provider_reference"], name="pay_attempt_provider_ref")]
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
        import org
        return bool(user.is_superuser or user.has_perm("payments.view_proof")
                    or user.has_perm("payments.final_confirm_payment") or org.is_es(user))


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
    # v2.1 X21 (G4.c): how the first pass was given; gateway = the system,
    # after a server-side verify whose amount and currency match.
    VIA_CHOICES = [("officer", "Finance officer"), ("gateway", "Gateway verified"), ("statement", "Statement match")]
    verified_via = models.CharField(max_length=12, choices=VIA_CHOICES, default="officer")
    auto_first_pass = models.BooleanField(default=False)
    provider_amount = models.DecimalField(null=True, blank=True, **MONEY)
    amount_matches = models.BooleanField(null=True, blank=True)
    # v2.1 F.4 (P8): after a first pass the item is locked until someone
    # clicks "Change first pass" (reason + audit).
    first_pass_unlocked = models.BooleanField(default=False)
    first_pass_changes = models.JSONField(default=list, blank=True)
    # v2.1 F.4 (P7): approvals beyond the second check when more approvers
    # are configured above the limit ([{user, at, decision, reason}]).
    extra_approvals = models.JSONField(default=list, blank=True)
    # v2.1 F.4 (P4): ICT "Send reminder to the Director of Finance".
    last_dof_reminder_at = models.DateTimeField(null=True, blank=True)
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
        indexes = [models.Index(fields=["is_open", "opened_at"], name="pay_verif_open")]  # v2.2
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


# ===========================================================================
# v2.2 — double-entry ledger and nightly reconciliation
# ===========================================================================
class LedgerAccount(models.Model):
    """Chart of accounts for QA money (seeded; see payments.ledger.ACCOUNTS)."""

    KIND_CHOICES = [("asset", "Asset"), ("liability", "Liability"), ("income", "Income"), ("expense", "Expense")]
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.name}"


class LedgerJournal(models.Model):
    """One balanced posting. ``key`` is unique, so the same event can never
    be posted twice (idempotency); journals are never edited — a correction
    is a new journal."""

    key = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=255)
    obligation = models.ForeignKey(PaymentObligation, null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="journals")
    institution = models.ForeignKey("institutions.Institution", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="+")
    source = models.CharField(max_length=30, blank=True, help_text="bill, payment, waiver, cancel, honorarium, fix …")
    posted_at = models.DateTimeField(default=timezone.now, db_index=True)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")

    class Meta:
        ordering = ["-posted_at", "-id"]
        indexes = [models.Index(fields=["obligation", "source"], name="payments_journal_ob_src")]

    def __str__(self):
        return self.key

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.pop("_allow_update", False):
            raise ValueError("Ledger journals are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Ledger journals are append-only.")


class LedgerLine(models.Model):
    journal = models.ForeignKey(LedgerJournal, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(default=ZERO, **MONEY)
    credit = models.DecimalField(default=ZERO, **MONEY)

    class Meta:
        indexes = [models.Index(fields=["account"], name="payments_ledgerline_acct")]


class ReconciliationRun(models.Model):
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    trigger = models.CharField(max_length=20, default="nightly")
    checked_obligations = models.PositiveIntegerField(default=0)
    checked_attempts = models.PositiveIntegerField(default=0)
    issues_found = models.PositiveIntegerField(default=0)
    auto_fixed = models.PositiveIntegerField(default=0)
    summary = models.JSONField(default=dict, blank=True)
    run_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="+")

    class Meta:
        ordering = ["-started_at"]


class ReconciliationIssue(models.Model):
    KIND_CHOICES = [
        ("ledger_drift", "Ledger did not match the bill (posted a correction)"),
        ("provider_paid_not_recorded", "Provider says paid; NBTE had not recorded it"),
        ("recorded_not_paid", "NBTE recorded a payment the provider does not confirm"),
        ("amount_mismatch", "Provider amount differs from what NBTE recorded"),
        ("double_payment", "More than one successful online payment for one bill"),
        ("stale_pending", "Online payment pending too long"),
        ("unconfirmed_too_long", "Provider-paid, waiting for Finance too long"),
        ("unbalanced_journal", "A ledger journal does not balance"),
    ]
    SEVERITY_CHOICES = [("info", "Info"), ("warning", "Warning"), ("critical", "Critical")]
    run = models.ForeignKey(ReconciliationRun, on_delete=models.CASCADE, related_name="issues")
    kind = models.CharField(max_length=40, choices=KIND_CHOICES)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default="warning")
    obligation = models.ForeignKey(PaymentObligation, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    attempt = models.ForeignKey(PaymentAttempt, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    message = models.CharField(max_length=500)
    auto_fixed = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    resolution = models.TextField(blank=True)

    class Meta:
        ordering = ["resolved_at", "-id"]


# ===========================================================================
# v2.4 — a bill made by Finance from the institution's programmes, approved by
# the Executive Secretary before the institution ever sees it
# ===========================================================================
class BillRequest(InstitutionScopedModelMixin, TimeStampedModel):
    """A bill Finance builds by picking items (programmes the institution
    already runs, programmes it can now offer, other fee schedules). The
    price comes from the fee rules (``payments.bill_requests.price``) and is
    frozen in ``lines``. Nothing reaches the institution until the Executive
    Secretary approves; approval issues a normal ``PaymentObligation``.

    draft → awaiting_es → approved (bill issued and sent) | rejected (reason)
    draft / awaiting_es → withdrawn
    """

    AUDITED = True
    INSTITUTION_PATH = "institution"

    DRAFT, AWAITING, APPROVED, REJECTED, WITHDRAWN = "draft", "awaiting_es", "approved", "rejected", "withdrawn"
    STATUS_CHOICES = [(DRAFT, "Draft"), (AWAITING, "Awaiting ES approval"), (APPROVED, "Approved & sent"),
                      (REJECTED, "Rejected"), (WITHDRAWN, "Withdrawn")]
    OPEN = (DRAFT, AWAITING)

    reference = models.CharField(max_length=40, unique=True)
    institution = models.ForeignKey("institutions.Institution", on_delete=models.PROTECT,
                                    related_name="bill_requests")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT, db_index=True)
    items = models.JSONField(default=list, blank=True,
                             help_text="What Finance picked: [{kind, master_id, programme_id, label, streams, fee_code}]")
    lines = models.JSONField(default=list, blank=True, help_text="Priced bill lines, frozen when sent to the ES.")
    total = models.DecimalField(default=ZERO, **MONEY)
    fee_schedule = models.ForeignKey(FeeSchedule, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True)
    reason = models.TextField(blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_remark = models.TextField(blank=True)
    obligation = models.OneToOneField(PaymentObligation, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="bill_request")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        indexes = [models.Index(fields=["status", "-created_at"], name="pay_billreq_status")]

    def __str__(self):
        return f"{self.reference} — {self.get_status_display()}"

    @property
    def is_open(self):
        return self.status in self.OPEN

    @property
    def tone(self):
        return "qa-tone-" + {"draft": "neutral", "awaiting_es": "warn", "approved": "success",
                             "rejected": "danger", "withdrawn": "neutral"}.get(self.status, "neutral")

    @property
    def priced_lines(self):
        return [{**line, "amount": Decimal(str(line.get("amount") or 0))} for line in self.lines or []]


# ===========================================================================
# v2.5 — cancellation and refund policy
# ===========================================================================
class Cancellation(InstitutionScopedModelMixin, TimeStampedModel):
    """One record per cancelled QA request or onboarding application: who
    cancelled it, why, at which stage and the refund quote frozen at that
    moment (``payments.refund_policy.refund_quote``). Unpaid bills on the
    subject were voided; a RefundRequest follows when money was paid."""

    AUDITED = True
    INSTITUTION_PATH = "institution"
    KIND_CHOICES = [("qa_request", "QA request"), ("application", "Onboarding application")]

    institution = models.ForeignKey("institutions.Institution", on_delete=models.PROTECT,
                                    related_name="cancellations")
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    subject_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    subject_id = models.CharField(max_length=64)
    subject = GenericForeignKey("subject_type", "subject_id")
    subject_reference = models.CharField(max_length=60)
    by_nbte = models.BooleanField(default=False, help_text="Withdrawn or rejected by NBTE (full refund band).")
    reason = models.TextField()
    declaration_accepted = models.BooleanField(default=False)
    cancelled_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    stage_code = models.CharField(max_length=80, blank=True)
    stage_label = models.CharField(max_length=200, blank=True)
    quote = models.JSONField(default=dict, blank=True, help_text="refund_quote() at the moment of cancellation.")
    voided_obligations = models.JSONField(default=list, blank=True, help_text="References of unpaid bills voided.")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        constraints = [models.UniqueConstraint(fields=["subject_type", "subject_id"], name="pay_cancel_one_per_subject")]
        permissions = [
            ("withdraw_for_nbte", "QA: can withdraw a request or application on NBTE's side (full refund)"),
        ]

    def __str__(self):
        return f"Cancellation of {self.subject_reference}"


class RefundRequest(InstitutionScopedModelMixin, TimeStampedModel):
    """Money back after a cancellation, under the refund policy.

    awaiting_details (institution gives bank details) → awaiting_finance
    (Finance verifies the payment and the bank details) → awaiting_approval
    (the Executive Secretary — or, when ``qa.refund.requires_es_approval`` is
    off, the Director of Finance — approves or rejects with a reason; never
    the verifier) → approved → refunded (Finance records the payment
    reference). Finance may send it back to awaiting_details; it lapses when
    no bank details arrive within ``qa.refund.request_window_days``."""

    AUDITED = True
    INSTITUTION_PATH = "institution"

    DETAILS, FINANCE, APPROVAL = "awaiting_details", "awaiting_finance", "awaiting_approval"
    APPROVED, REFUNDED, REJECTED, LAPSED = "approved", "refunded", "rejected", "lapsed"
    STATUS_CHOICES = [(DETAILS, "Waiting for bank details"), (FINANCE, "Finance is verifying"),
                      (APPROVAL, "Awaiting approval"), (APPROVED, "Approved — payment being made"),
                      (REFUNDED, "Refunded"), (REJECTED, "Not approved"), (LAPSED, "Lapsed")]
    OPEN = (DETAILS, FINANCE, APPROVAL, APPROVED)

    reference = models.CharField(max_length=40, unique=True)
    institution = models.ForeignKey("institutions.Institution", on_delete=models.PROTECT,
                                    related_name="refund_requests")
    cancellation = models.OneToOneField(Cancellation, on_delete=models.PROTECT, related_name="refund_request")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DETAILS, db_index=True)
    band = models.CharField(max_length=40)
    rule_label = models.CharField(max_length=255, blank=True)
    percent = models.DecimalField(max_digits=5, decimal_places=2)
    paid_amount = models.DecimalField(**MONEY)
    admin_fee = models.DecimalField(default=ZERO, **MONEY)
    refund_amount = models.DecimalField(**MONEY)
    obligations = models.JSONField(default=list, blank=True,
                                   help_text="[{id, reference, paid}] — the paid bills this refund covers.")
    # Bank details (from the institution)
    bank_name = models.CharField(max_length=120, blank=True)
    account_name = models.CharField(max_length=200, blank=True)
    account_number = models.CharField(max_length=10, blank=True, validators=[nuban_validator])
    details_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    details_at = models.DateTimeField(null=True, blank=True)
    # Finance verification
    verified_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    finance_note = models.TextField(blank=True)
    # Approval
    approver_kind = models.CharField(max_length=10, blank=True, help_text="es | finance (from the setting).")
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_remark = models.TextField(blank=True)
    # Payment
    paid_reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Bank details must arrive before this.")

    objects = InstitutionScopedManager()
    unscoped = UnscopedInstitutionManager()

    class Meta:
        ordering = ["-created_at", "-id"]
        base_manager_name = "unscoped"
        default_manager_name = "objects"
        indexes = [models.Index(fields=["status", "-created_at"], name="pay_refreq_status")]

    def __str__(self):
        return f"{self.reference} — {self.get_status_display()}"

    @property
    def is_open(self):
        return self.status in self.OPEN

    @property
    def tone(self):
        return "qa-tone-" + {self.DETAILS: "warn", self.FINANCE: "info", self.APPROVAL: "warn",
                             self.APPROVED: "info", self.REFUNDED: "success", self.REJECTED: "danger",
                             self.LAPSED: "neutral"}.get(self.status, "neutral")

    @property
    def masked_account(self):
        return f"••••••{self.account_number[-4:]}" if self.account_number else ""
