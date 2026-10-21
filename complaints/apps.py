"""Complaints (QA) — Phase 7 (Master Plan §5.6, §11).

External complaints: layers, routing, SLAs and escalation. The internal ICT
`tickets` app is untouched.
"""
from django.apps import AppConfig


class ComplaintsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "complaints"
    verbose_name = "Complaints (QA)"

    def ready(self):
        # Registers the task-bridge resolver for Complaint and the flow
        # listener that keeps each stage's SLA clock (after the bridge's own).
        from . import services  # noqa: F401
