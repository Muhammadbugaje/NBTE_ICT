"""Payments (QA) — Phase 2 (Master Plan §5.3, §10): fee schedules, bills,
slips, providers, the Finance verification queue and Payment Advices."""
from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "payments"
    verbose_name = "Payments (QA)"

    def ready(self):
        from . import effects, handlers  # noqa: F401  (flow effect + two-step handlers)
