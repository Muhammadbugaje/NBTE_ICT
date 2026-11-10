"""Send SLA reminders for open QA stage tasks (Phase 4, §8).

Run it every hour (cron / Task Scheduler / Celery beat). Each holder is
reminded once at every percentage in ``qa.stage.reminder_at_percent``
(default 50 and 90) of the stage's SLA, and the holder and the department
head are told once when the stage is overdue.

    python manage.py qa_stage_reminders
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send SLA reminders and breach notices for open QA stage tasks."

    def handle(self, *args, **opts):
        from qa_core.task_bridge import send_stage_reminders

        sent = send_stage_reminders()
        reminders = sum(1 for _pk, kind in sent if kind != "breach")
        breaches = sum(1 for _pk, kind in sent if kind == "breach")
        self.stdout.write(f"QA stage reminders: {reminders} reminder(s), {breaches} breach notice(s) sent.")
        # Official letters waiting for their signed copy (qa.documents.official_copy_sla_days).
        from documents.official_letters import send_reminders as official_letter_reminders

        letters = official_letter_reminders()
        self.stdout.write(f"Official letters: {len(letters)} reminder(s)/escalation(s) sent.")
        # SOP-03 visit invitations: reminders before the visit (qa.visit.invite_reminder_days), staff alerts and
        # amended letters when the visit dates changed.
        from accreditation.visit_invitations import send_reminders as visit_invite_reminders

        invites = visit_invite_reminders()
        self.stdout.write(f"Visit invitations: {sum(1 for _pk, k in invites if k == 'reminder')} reminder(s), "
                          f"{sum(1 for _pk, k in invites if k == 'alert')} staff alert(s), "
                          f"{sum(1 for _pk, k in invites if k == 'amended')} amended case(s).")
