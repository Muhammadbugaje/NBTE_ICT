"""Remind and escalate official letters still waiting for their signed copy.

Also run by ``manage.py qa_stage_reminders``. Safe to run hourly: each
reminder (Director / assignee) and the ES escalation is sent once per letter.

    python manage.py qa_official_letters
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Remind directors and escalate to the ES when an official letter copy is overdue."

    def handle(self, *args, **opts):
        from documents.official_letters import send_reminders

        sent = send_reminders()
        kinds = {}
        for _pk, kind in sent:
            kinds[kind] = kinds.get(kind, 0) + 1
        self.stdout.write("Official letters: " + (", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
                                                  or "nothing overdue") + ".")
