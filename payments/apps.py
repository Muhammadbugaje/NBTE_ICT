"""Payments (QA) — Phase 2 (Master Plan §5.3, §10): fee schedules, bills,
slips, providers, the Finance verification queue and Payment Advices."""
from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "payments"
    verbose_name = "Payments (QA)"

    def ready(self):
        from . import checks, effects, handlers  # noqa: F401  (system check T-SEC-8, flow effect, two-step handlers)
        from django.db.models.signals import post_save

        from . import ledger
        from .models import PaymentObligation

        # v2.2: every committed bill change is mirrored in the double-entry ledger.
        post_save.connect(ledger.on_obligation_saved, sender=PaymentObligation,
                          dispatch_uid="payments.ledger.obligation")
        # v2.5: money confirmed for an already-cancelled request is added to its refund.
        from . import cancellations

        cancellations.connect()
