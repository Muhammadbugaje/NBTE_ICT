"""Django admin for payments — READ-ONLY for money records.

Nothing here may move an obligation's state: confirmation is only ever the
Director of Finance's final decision in the Finance queue (Master Plan §10.2).
Admin shows the records for support and audit.
"""
from django.contrib import admin

from core.admin_base import ChoicesDropdownFilter, RangeDateTimeFilter, ReadOnlyModelAdmin as _ThemedReadOnly, badge
from core.admin_base import ModelAdmin, StackedInline, TabularInline  # Phase 8 admin theme

from .models import (
    BankAccount, BankStatementImport, BankStatementLine, FeeSchedule, PaymentAdvice, PaymentAttempt, PaymentCredit,
    PaymentObligation, PaymentSlip, PaymentVerification, ProofOfPayment, Refund, WebhookLog,
)


class ReadOnlyAdmin(_ThemedReadOnly):

    def get_queryset(self, request):
        manager = getattr(self.model, "unscoped", None) or self.model._default_manager
        return manager.all()


@admin.register(PaymentObligation)
class PaymentObligationAdmin(ReadOnlyAdmin):
    list_display = ("reference", "institution", "fee_schedule", "amount", "balance", "status_badge", "created_at")
    list_filter = ("status", "fee_schedule", ("created_at", RangeDateTimeFilter))
    date_hierarchy = "created_at"
    status_badge = badge("status", "Status", {"issued": "info", "part_paid": "warning", "overpaid": "warning",
                                              "refund_requested": "warning"})
    search_fields = ("reference", "institution__code", "institution__name")


@admin.register(PaymentVerification)
class PaymentVerificationAdmin(ReadOnlyAdmin):
    list_display = ("obligation", "source", "decision", "verified_by", "final_decision", "final_approver", "is_open")
    list_filter = ("is_open", "source", "decision", "final_decision", ("created_at", RangeDateTimeFilter))


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(ReadOnlyAdmin):
    list_display = ("obligation", "provider", "provider_reference", "amount", "status_badge", "webhook_verified")
    list_filter = ("provider", ("status", ChoicesDropdownFilter), ("created_at", RangeDateTimeFilter))
    status_badge = badge("status", "Status", {"initiated": "neutral", "successful": "success"})


@admin.register(WebhookLog)
class WebhookLogAdmin(ReadOnlyAdmin):
    list_display = ("provider", "received_at", "signature_valid", "reference", "outcome")
    list_filter = ("provider", "signature_valid")


for model, cols in ((PaymentSlip, ("obligation", "version", "amount", "is_current", "expires_at")),
                    (ProofOfPayment, ("obligation", "kind", "amount_claimed", "teller_number", "fraud_flag")),
                    (PaymentAdvice, ("reference", "obligation", "amount", "generated_at")),
                    (Refund, ("obligation", "amount", "status", "requested_by", "approved_by")),
                    (PaymentCredit, ("institution", "amount", "status", "applied_to")),
                    (BankStatementImport, ("pk", "original_name", "parsed_rows", "matched", "unmatched")),
                    (BankStatementLine, ("statement", "row_number", "amount", "extracted_reference", "match_status"))):
    admin.site.register(model, type(f"{model.__name__}Admin", (ReadOnlyAdmin,), {"list_display": cols}))

# Fee schedules and bank accounts change only through dual control (Settings → QA).
for model, cols in ((FeeSchedule, ("code", "name", "formula", "is_active")),
                    (BankAccount, ("bank_name", "account_number", "is_default", "is_active", "is_placeholder"))):
    admin.site.register(model, type(f"{model.__name__}Admin", (ReadOnlyAdmin,), {"list_display": cols}))
