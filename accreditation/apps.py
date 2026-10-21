"""Accreditation (QA): SOP-01 onboarding in Phase 2 (Master Plan §5.4).

QA requests (SOP-02), SSQs and the SOP-03 case arrive in Phases 3–4 and 6.
"""
from django.apps import AppConfig


class AccreditationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accreditation"
    verbose_name = "Accreditation (QA)"

    def ready(self):
        from . import effects  # noqa: F401  (registers flow effects and payment receivers)
