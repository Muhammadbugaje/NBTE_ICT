"""python manage.py privacy_mode --show
python manage.py privacy_mode --set assignment --dry-run
python manage.py privacy_mode --set assignment --by director.ict@example   (starts dual control)

P.11: ``qa.privacy.mode`` is a dual-control setting. ``--set`` never writes
the value: it starts the same PendingAction the Settings screen does
(``qa_core.settings_service.save_setting``); a different officer holding
``qa_core.edit_qa_dual_control_settings`` confirms it on Settings → QA →
Pending changes. On confirmation ``qa_core.view_all_institutions`` is narrowed
to the ES, super admin and Director of ICT (``assignments.view_all``).
``--dry-run`` (and ``--show``) print the impact per role.
"""
from django.core.management.base import BaseCommand, CommandError

KEY = "qa.privacy.mode"


class Command(BaseCommand):
    help = "Show or (dual-control) change the QA privacy mode."

    def add_arguments(self, parser):
        parser.add_argument("--show", action="store_true")
        parser.add_argument("--set", choices=["assignment", "department"])
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--by", help="E-mail of the officer starting the change (needs QA settings rights).")
        parser.add_argument("--reason", default="Need-to-know privacy (X1) after the access report review.")

    def handle(self, *args, **o):
        from config.models import ConfigEntry

        from assignments import access, report, view_all
        entry = ConfigEntry.objects.filter(key=KEY, scope="global").first()
        if entry is None:
            raise CommandError(f"{KEY} is not seeded; run seed_startup first.")
        self.stdout.write(f"{KEY} = {access.privacy_mode()}  (dual control: {'yes' if entry.dual_control else 'no'})")
        try:
            from qa_core.models import PendingAction
            pend = PendingAction.objects.filter(policy_id="config.update_setting", status="pending",
                                                object_id=str(entry.pk)).count()
            if pend:
                self.stdout.write(f"Pending change(s) waiting for confirmation: {pend}")
        except Exception:
            pass
        if o["show"] or o["dry_run"] or not o["set"]:
            self.stdout.write("")
            self.stdout.write(report.impact_text())
            self.stdout.write("")
            self.stdout.write(view_all.plan_text())
        if not o["set"] or o["dry_run"]:
            if o["set"]:
                self.stdout.write(self.style.WARNING("Dry run: nothing changed."))
            return
        if o["set"] == access.privacy_mode():
            self.stdout.write("Already in that mode.")
            return
        if not o["by"]:
            raise CommandError("--by EMAIL is required: the change is started by a named officer (dual control).")
        from accounts.models import User
        from qa_core.settings_service import save_setting
        user = User.objects.filter(email__iexact=o["by"], is_active=True).first()
        if user is None:
            raise CommandError("No active account with that e-mail.")
        from django.core.exceptions import PermissionDenied, ValidationError
        try:
            outcome, obj = save_setting(user, entry, o["set"], reason=o["reason"])
        except (PermissionDenied, ValidationError) as exc:
            raise CommandError(f"Not started: {exc}")
        if outcome == "pending":
            self.stdout.write(self.style.SUCCESS(
                f"Change started (pending action #{obj.pk}). A second officer confirms it on Settings → QA."))
        else:
            self.stdout.write(self.style.SUCCESS("Saved."))
