"""python manage.py qa_onboarding_reminders

Remind whoever holds an onboarding self-study past ``qa.onboarding.stage_sla_days``
and escalate to the director, then the ES (each level once per stage). Celery
beat runs the same job hourly as ``accreditation.onboarding_stage_reminders``.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send overdue reminders / escalations for onboarding self-study reviews (idempotent)."

    def handle(self, *args, **opts):
        from accreditation.onboarding_linear import send_stage_reminders

        n = send_stage_reminders()
        self.stdout.write(self.style.SUCCESS(f"{n} reminder(s) sent."))
