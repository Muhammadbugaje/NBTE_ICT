"""Send the periodic profile-completeness reminder to every active staff
user below ``profile.completion_threshold`` whose last reminder is older than
``profile.reminder_days`` (accounts/profile_nudges.py). Safe to run daily
from cron; it never sends twice inside the interval."""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts import profile_nudges


class Command(BaseCommand):
    help = "Remind staff with an incomplete profile (respects profile.reminder_days)."

    def handle(self, *args, **options):
        if profile_nudges.reminder_days() <= 0:
            self.stdout.write("Reminders are off (profile.reminder_days = 0).")
            return
        sent = 0
        users = get_user_model().objects.filter(is_active=True, user_type="staff")
        for user in users.iterator():
            if profile_nudges.maybe_send_reminder(user):
                sent += 1
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} profile reminder(s)."))
