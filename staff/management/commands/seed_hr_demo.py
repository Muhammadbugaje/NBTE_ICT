"""python manage.py seed_hr_demo

Demo data for the Phase B HR pages (promotions, user management, act as user,
staff ID, the Access tab). Development only: refuses to run unless DEBUG is on
(or --i-know-this-is-demo is passed). Idempotent: a second run changes nothing.

Creates (all e-mails end in @demo.nbte.local; password NBTE-Demo#2026):
  * hr.admin@demo.nbte.local       HR officer (group "HR officers (demo)")
  * demo.superadmin@demo.nbte.local superuser, for "Act as user"
  * qa.es@demo.nbte.local          joins the HR demo group if the account exists
  * 16 fictional staff records DEMO-HR-001 ... DEMO-HR-016 on the programme,
    HR and ICT ladders, with a spread of present-appointment dates so the
    Eligible / Overdue tabs have rows.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

PASSWORD = "NBTE-Demo#2026"
GROUP = "HR officers (demo)"
HR_PERMS = [
    "staff.view_staff", "staff.add_staff", "staff.change_staff", "staff.promote_staff",
    "staff.print_staff_id", "staff.export_nominal_roll",
    "accounts.view_user", "accounts.change_user", "accounts.access_all_departments",
]

# (no, first, last, gender, designation name, years in present post, department name)
STAFF = [
    (1, "Amina", "Sule (Demo)", "F", "PROG. OFF. II", 4.2, "ASPD"),
    (2, "Bashir", "Lawan (Demo)", "M", "PROG. OFF. II", 1.5, "ASPD"),
    (3, "Chiamaka", "Obi (Demo)", "F", "PROG. OFF. I", 3.1, "ASPD"),
    (4, "Dauda", "Yaro (Demo)", "M", "PROG. OFF. I", 6.0, "DPAP"),
    (5, "Esther", "Bako (Demo)", "F", "SNR. PROG. OFFR.", 2.0, "DPAP"),
    (6, "Fatai", "Ade (Demo)", "M", "SNR. PROG. OFFR.", 3.6, "ASPD"),
    (7, "Grace", "Udo (Demo)", "F", "PRIN. PROG. OFFR.", 5.4, "DPAP"),
    (8, "Hassan", "Musa (Demo)", "M", "HUMAN RES. OFFR. II", 3.0, "Human Resources"),
    (9, "Ifeoma", "Eze (Demo)", "F", "HUMAN RES.OFF. I", 0.8, "Human Resources"),
    (10, "Jibrin", "Tanko (Demo)", "M", "SNR. HUMAN RES. OFFR.", 4.5, "Human Resources"),
    (11, "Khadija", "Isa (Demo)", "F", "ICT OFF. II", 3.3, "ICT"),
    (12, "Lukman", "Ali (Demo)", "M", "ICT OFF. II", 2.4, "ICT"),
    (13, "Maryam", "Idris (Demo)", "F", "ICT OFF. I", 7.1, "ICT"),
    (14, "Nuhu", "Bala (Demo)", "M", "PROG. OFF. II", 3.9, "DPAP"),
    (15, "Oluchi", "Nwosu (Demo)", "F", "PROG. OFF. I", 0.4, "DPAP"),
    (16, "Peter", "Agbo (Demo)", "", "HUMAN RES. OFFR. II", 5.0, "Human Resources"),
]


class Command(BaseCommand):
    help = "Demo HR accounts and staff for the Phase B pages (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--i-know-this-is-demo", action="store_true",
                            help="Run even when DEBUG is off (never on production).")

    @transaction.atomic
    def handle(self, *args, **opts):
        if not (settings.DEBUG or opts["i_know_this_is_demo"]):
            raise CommandError("seed_hr_demo only runs with DEBUG on.")
        from accounts.models import Role, User
        from staff.models import Department, Designation, Staff

        changes = []
        group, created = Group.objects.get_or_create(name=GROUP)
        if created:
            changes.append(f"group {GROUP}")
        for label in HR_PERMS:
            app, code = label.split(".")
            perm = Permission.objects.filter(content_type__app_label=app, codename=code).first()
            if perm and not group.permissions.filter(pk=perm.pk).exists():
                group.permissions.add(perm)
                changes.append(f"{GROUP} += {label}")

        hr_dept = Department.objects.filter(name="Human Resources").first()
        officer = Role.objects.filter(code="officer").first()

        def account(email, first, last, **extra):
            user = User.objects.filter(email=email).first()
            if user is None:
                user = User(email=email, first_name=first, last_name=last, must_change_password=False,
                            **extra)
                user.set_password(PASSWORD)
                user.save()
                changes.append(f"user {email}")
            return user

        hr = account("hr.admin@demo.nbte.local", "Halima", "HR Admin (Demo)",
                     role=officer, department=hr_dept)
        if not hr.groups.filter(pk=group.pk).exists():
            hr.groups.add(group)
            changes.append("hr.admin joins the HR group")
        account("demo.superadmin@demo.nbte.local", "Sani", "Superadmin (Demo)",
                is_superuser=True, is_staff=True)
        es = User.objects.filter(email="qa.es@demo.nbte.local").first()
        if es and not es.groups.filter(pk=group.pk).exists():
            es.groups.add(group)
            changes.append("qa.es joins the HR group")

        today = date.today()
        for no, first, last, gender, desig, years, dept in STAFF:
            emp = f"DEMO-HR-{no:03d}"
            if Staff.all_objects.filter(employee_no=emp).exists():
                continue
            d = Designation.objects.filter(name=desig).first()
            Staff.objects.create(
                employee_no=emp, first_name=first, last_name=last, gender=gender,
                email=f"{first.lower()}.demo{no}@demo.nbte.local",
                designation=d, grade_level=(d.contediss_min if d else ""),
                department=Department.objects.filter(name=dept).first(),
                date_present_appointment=today - timedelta(days=int(years * 365.25)),
                date_first_appointment=today - timedelta(days=int((years + 6) * 365.25)),
            )
            changes.append(f"staff {emp}")
        if changes:
            self.stdout.write(f"seed_hr_demo: {len(changes)} change(s): " + "; ".join(changes[:12])
                              + (" …" if len(changes) > 12 else ""))
        else:
            self.stdout.write("seed_hr_demo: no changes.")
