"""Inspection (QA) — Phase 5 (Master Plan §5.5, §9): resource persons, QA
personnel and mapping, the availability finder, inspections, post-visit
reports with their endorsement chain, and the service history."""
from django.apps import AppConfig


class InspectionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "inspection"
    verbose_name = "Inspection (QA)"

    def ready(self):
        from accounts.scoping import register_engagement_resolver

        from . import effects  # noqa: F401  (flow effects)
        from . import handlers  # noqa: F401  (Edit QA Personnel / Mapping, Override Post-Visit Personnel)
        from . import services

        # A resource person on the portal sees the institutions of their engagements (§4).
        register_engagement_resolver(services.engaged_institution_ids)
        from qa_core.task_bridge import register_reconcile_check

        # The nightly reconciliation lists people who are no longer eligible (§9).
        register_reconcile_check(services.lapsed_people_report)
