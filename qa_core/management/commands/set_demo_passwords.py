"""set_demo_passwords — one known password for every demo account (handover).

    python manage.py set_demo_passwords                       # "NBTE-Demo#2026"
    python manage.py set_demo_passwords --password "S3cret!x" --csv demo-accounts.csv

Demo accounts are the users tagged in ``qa_core.DemoTag`` (every seed tags
what it creates) plus the users of demo-tagged institutions. Nothing else is
touched. Refused when DEBUG is false unless ``--i-understand-production``:
a shared, published password must never exist on a live server.
"""
import csv

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError

DEFAULT_PASSWORD = "NBTE-Demo#2026"


def demo_users():
    from accounts.models import User
    from institutions.models import Institution
    from qa_core.models import DemoTag

    user_ids = DemoTag.objects.filter(content_type=ContentType.objects.get_for_model(User)).values_list(
        "object_id", flat=True)
    inst_ids = DemoTag.objects.filter(content_type=ContentType.objects.get_for_model(Institution)).values_list(
        "object_id", flat=True)
    qs = User.objects.filter(pk__in=[int(i) for i in user_ids if str(i).isdigit()]) | User.objects.filter(
        institution_id__in=[int(i) for i in inst_ids if str(i).isdigit()])
    return qs.distinct().select_related("role", "department", "institution").order_by("user_type", "email")


class Command(BaseCommand):
    help = "Give every demo account the same known password (development and handover only)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default=DEFAULT_PASSWORD)
        parser.add_argument("--csv", default="", help="Also write the account list to this CSV file.")
        parser.add_argument("--list-only", action="store_true", help="List the accounts; change nothing.")
        parser.add_argument("--i-understand-production", action="store_true")

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["i_understand_production"]:
            raise CommandError("Refused when DEBUG is false (a shared demo password must never exist on a live "
                               "server). Add --i-understand-production if you really mean it.")
        if len(opts["password"]) < 10:
            raise CommandError("Use a password of at least 10 characters.")
        rows = []
        for user in demo_users():
            if not opts["list_only"]:
                user.set_password(opts["password"])
                user.must_change_password = False
                user.is_active = True
                user.save(update_fields=["password", "must_change_password", "is_active"])
            rows.append([user.email, user.get_full_name(), user.get_user_type_display(),
                         getattr(user.role, "name", ""), getattr(user.department, "name", ""),
                         getattr(user.institution, "name", ""),
                         "QA portal" if user.user_type != "staff" else "Internal system"])
        w = self.stdout.write
        w(self.style.SUCCESS(f"{len(rows)} demo account(s)"
                             + ("" if opts["list_only"] else f" now use the password: {opts['password']}")))
        for r in rows:
            w(f"  {r[0]:<42} {r[3]:<32} {r[6]}")
        if opts["csv"]:
            with open(opts["csv"], "w", newline="") as fh:
                out = csv.writer(fh)
                out.writerow(["E-mail", "Name", "Account type", "Role", "Department", "Institution", "Signs in at"])
                out.writerows(rows)
            w(f"Written {opts['csv']}")
