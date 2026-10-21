"""python manage.py seed_memo_demo [--routes-only] [--remove] [--dry-run]

Sets memo routing up properly and fills the system with demo memos you can
open and act on.

1. Repairs the routes that shipped with the database:
     * "Line Head" steps that were set to the Deputy Director kind now use
       the line-head kind (office head / division head), still optional;
     * a "Director Approval" step set to the Deputy kind now uses Director;
     * the Director step of "Staff to ES" becomes required, so a memo to the
       ES always passes the sender's director (it waits — and admins are
       told — when a department has no director);
     * the two "staff" rules get target roles, so "Staff to Director" is
       actually used for memos to directors (before, both rules matched
       every memo and "Staff to ES" always won on priority).
2. Adds the other routes and rules a directorate needs, with priorities that
   put the specific rules above the general ones (see PRIORITIES below).
3. Grants the memo permissions to the roles that should have them.
4. Unless --routes-only: adds three demo accounts (…@demo.nbte.test,
   password Demo-Memo-2026!) and a set of "[Demo]" memos in every state —
   waiting, waiting on nobody, returned, rejected, published, draft.

Re-running is safe. --remove deletes the demo memos, demo accounts and the
routes/rules this command added (the repairs in step 1 are kept).
No email is sent: memo alerts are in-app notifications only.
"""
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

DEMO_PREFIX = "[Demo] "
DEMO_DOMAIN = "demo.nbte.test"
DEMO_PASSWORD = "Demo-Memo-2026!"
MARK = "Added by seed_memo_demo."

ES, DIR, DEP = "executive-secretary", "director", "deputy-director"

# code, name, description, same_rank, steps [(name, kind, optional, hierarchical, instructions)]
ROUTES = [
    # Reuses the "Executive Secretary to Staff" route if it already exists.
    ("es-to-staff", "Executive Secretary to Staff",
     "Memos from the Executive Secretary go straight out.", "skip", []),
    ("director-to-es", "Director to ES",
     "Directors and deputies write to the ES directly.", "skip", []),
    ("staff-to-deputy", "Staff to Deputy Director",
     "Through the sender's line head, if they have one.", "skip",
     [("Line Head", "division_head", True, True, "Check the request before it goes to the deputy.")]),
    ("general-memo", "General memo",
     "Any other memo to one person or a list of people: the line head sees it first, if there is one.",
     "skip",
     [("Line Head", "division_head", True, True, "")]),
    ("department-circular", "Department circular",
     "A memo to a whole department needs the sender's director.", "skip",
     [("Director Approval", "director", False, True, "Approve before it reaches the whole department.")]),
    ("all-staff-circular", "Institution-wide circular",
     "All staff / all directors / roles: the sender's director, then the ES.", "skip",
     [("Director Approval", "director", True, True, ""),
      ("ES Approval", "es", False, False, "Final approval before it goes to everyone.")]),
]

# name, route code, target kind, sender roles, target roles, priority
PRIORITIES = [
    ("ES writes to anyone", "es-to-staff", "specific_user", [ES], [], 300),
    ("ES writes to a list", "es-to-staff", "individuals", [ES], [], 300),
    ("Director/Deputy to ES", "director-to-es", "specific_user", [DIR, DEP], [ES], 200),
    # 100 / 90: the two rules that came with the database (re-targeted below)
    ("Staff memo to Deputy Director", "staff-to-deputy", "specific_user", [], [DEP], 80),
    ("Department circular", "department-circular", "department", [], [], 50),
    ("All-staff circular", "all-staff-circular", "all_staff", [], [], 50),
    ("All-directors circular", "all-staff-circular", "all_directors", [], [], 50),
    ("Circular to roles", "all-staff-circular", "role", [], [], 50),
    ("Memo to a list of people", "general-memo", "individuals", [], [], 10),
    ("Any other memo to one person", "general-memo", "specific_user", [], [], 10),
]

PERMS = {
    "send_memo_broadcast": [ES, DIR, DEP, "super-admin"],
    "approve_memo": [ES, DIR, DEP, "division-head", "office-head-ts", "office-head-dev",
                     "office-head-admin", "super-admin"],
    "manage_memo": [ES, "super-admin"],
    "manage_memoroute": ["super-admin"],
}

DEMO_USERS = [
    # local part, first, last, role, department code
    ("ict.linehead", "Aisha", "Line-Head (Demo)", "division-head", "ict"),
    ("ict.deputy", "Usman", "Deputy (Demo)", DEP, "ict"),
    ("hr.officer", "Halima", "HR Officer (Demo)", "officer", "hr"),
]


class Command(BaseCommand):
    help = "Repair memo routes, add useful routes/rules/permissions and demo memos."

    def add_arguments(self, parser):
        parser.add_argument("--routes-only", action="store_true",
                            help="Routes, rules and permissions only — no demo users or memos.")
        parser.add_argument("--remove", action="store_true",
                            help="Delete the demo memos, demo users and the routes/rules added here.")
        parser.add_argument("--dry-run", action="store_true", help="Show what would change, then roll back.")

    def say(self, msg):
        self.stdout.write("  " + msg)

    def handle(self, *args, routes_only=False, remove=False, dry_run=False, **opts):
        try:
            with transaction.atomic():
                if remove:
                    self.remove()
                else:
                    self.stdout.write("Routes")
                    self.repair_existing()
                    self.add_routes()
                    self.stdout.write("Permissions")
                    self.grant_permissions()
                    if not routes_only:
                        self.stdout.write("Demo accounts")
                        users = self.demo_users()
                        self.stdout.write("Demo memos")
                        self.demo_memos(users)
                if dry_run:
                    raise _DryRun()
        except _DryRun:
            self.stdout.write(self.style.WARNING("Dry run — nothing was saved."))
            return
        # Memos parked by earlier edits get a fresh look now the routes are right.
        from messaging import routing
        moved = routing.reconcile_parked() if not remove else 0
        if moved:
            self.say(f"{moved} waiting memo(s) re-checked against the new routes.")
        self.stdout.write(self.style.SUCCESS("Done."))

    # ------------------------------------------------------------------ routes
    def repair_existing(self):
        from messaging.models import MemoRoute, MemoRouteRule, MemoRouteStep
        for step in MemoRouteStep.objects.select_related("route"):
            name = step.name.lower()
            if "line" in name and "head" in name and step.approver_kind != "division_head":
                self.say(f"{step.route.name}: “{step.name}” was {step.get_approver_kind_display()!r} "
                         "→ now the sender's line head (office / division head)")
                step.approver_kind = "division_head"
                step.save(update_fields=["approver_kind"])
            elif name.startswith("director") and step.approver_kind == "deputy":
                self.say(f"{step.route.name}: “{step.name}” was the Deputy kind → now the sender's director")
                step.approver_kind = "director"
                step.save(update_fields=["approver_kind"])
        route = MemoRoute.objects.filter(code="staff-to-es").first()
        if route:
            for step in route.steps.filter(approver_kind="director", is_optional=True):
                self.say(f"{route.name}: “{step.name}” is now required (waits for a director; admins are alerted)")
                step.is_optional = False
                step.save(update_fields=["is_optional"])
            for step in route.steps.filter(approver_kind="es", is_hierarchical=True):
                # The ES outranks everyone, so this changes nothing for staff, but
                # it keeps the ES gate from ever being ranked away.
                step.is_hierarchical = False
                step.save(update_fields=["is_hierarchical"])
        for code, targets in (("staff-to-es", [ES]), ("staff-to-director", [DIR])):
            for rule in MemoRouteRule.objects.filter(route__code=code, target_kind="specific_user"):
                if not rule.target_role_codes:
                    self.say(f"Rule “{rule.name}” now applies only to memos addressed to: {', '.join(targets)}")
                    rule.target_role_codes = targets
                    rule.save(update_fields=["target_role_codes", "updated_at"])
        # Routes without any rule are never used — say so.
        for r in MemoRoute.objects.filter(rules__isnull=True, is_active=True):
            if r.code not in {c for c, *_ in ROUTES}:
                self.say(f"Note: route “{r.name}” has no rule, so no memo uses it.")

    def add_routes(self):
        from messaging.models import MemoRoute, MemoRouteRule, MemoRouteStep
        for code, name, desc, same, steps in ROUTES:
            route, created = MemoRoute.objects.get_or_create(
                code=code, defaults={"name": name, "description": f"{desc} {MARK}",
                                     "same_rank_behaviour": same})
            if created:
                for i, (sname, kind, opt, hier, instr) in enumerate(steps, start=1):
                    MemoRouteStep.objects.create(route=route, position=i, name=sname, approver_kind=kind,
                                                 is_optional=opt, is_hierarchical=hier, instructions=instr)
                self.say(f"+ route “{name}” ({len(steps)} step{'s' if len(steps) != 1 else ''})")
        for name, code, kind, senders, targets, prio in PRIORITIES:
            route = MemoRoute.objects.get(code=code)
            rule, created = MemoRouteRule.objects.get_or_create(
                name=name, defaults={"route": route, "target_kind": kind, "sender_role_codes": senders,
                                     "target_role_codes": targets, "priority": prio})
            if created:
                self.say(f"+ rule “{name}” → {route.name} (priority {prio})")

    def grant_permissions(self):
        from accounts.models import Role
        for codename, role_codes in PERMS.items():
            perm = Permission.objects.get(content_type__app_label="messaging", codename=codename)
            for role in Role.objects.filter(code__in=role_codes):
                if not role.permissions.filter(pk=perm.pk).exists():
                    role.permissions.add(perm)
                    self.say(f"+ {role.name}: {perm.name}")

    # ------------------------------------------------------------------ demo data
    def demo_users(self):
        from accounts.models import Role, User
        from staff.models import Department
        out = {}
        for local, first, last, role_code, dept_code in DEMO_USERS:
            email = f"{local}@{DEMO_DOMAIN}"
            dept = Department.objects.filter(code=dept_code).first()
            user = User.objects.filter(email__iexact=email).first()
            if user is None:
                user = User.objects.create_user(email, DEMO_PASSWORD, first_name=first, last_name=last,
                                                role=Role.objects.filter(code=role_code).first(),
                                                department=dept)
                user.must_change_password = False
                user.save()
                self.say(f"+ {user.get_full_name()} <{email}> — {user.role.name if user.role else ''}, "
                         f"{dept.name if dept else 'no department'}")
            out[local] = user
        return out

    def demo_memos(self, demo):
        from accounts.models import User
        from messaging import memos as svc
        from messaging import routing
        from messaging.models import Memo
        from staff.models import Department

        if Memo.objects.filter(subject__startswith=DEMO_PREFIX).exists():
            self.say("Demo memos already exist — skipped (run with --remove first to recreate them).")
            return

        def u(email):
            return User.objects.filter(email__iexact=email, is_active=True).first()

        es = routing.find_es()
        fin = Department.objects.filter(code="finance").first()
        fatima = u("finance@nbte.gov.ng")
        nikola = u("tech@nbte.gov.ng")
        tesla = routing.find_director(fin) if fin else None
        ict_dir = routing.find_director(Department.objects.filter(code="ict").first())
        need = {"ES": es, "Finance staff": fatima, "ICT officer": nikola,
                "Finance director": tesla, "ICT director": ict_dir}
        missing = [k for k, v in need.items() if v is None]
        if missing:
            raise CommandError("Demo memos need these accounts, which were not found: " + ", ".join(missing))
        linehead, deputy, hr = demo["ict.linehead"], demo["ict.deputy"], demo["hr.officer"]

        def memo(sender, subject, body, *, to=None, kind=Memo.SPECIFIC_USER, publish=True, **kw):
            m = svc.create_memo(sender=sender, subject=DEMO_PREFIX + subject, body=body, visibility=kind,
                                target_user=to, **kw)
            if publish:
                svc.publish_memo(m, sender)
            m.refresh_from_db()
            return m

        def act(m, how, who, comment=""):
            getattr(routing, how)(m, who, comment=comment)
            m.refresh_from_db()
            return m

        made = []
        m = memo(fatima, "Extension of imprest retirement deadline",
                 "The Finance department requests a two-week extension for retiring Q3 imprest.",
                 to=es, priority="urgent")
        made.append((m, f"urgent, waiting for {tesla.get_full_name()} (line head skipped: none in Finance)"))

        m = memo(nikola, "Replacement of server-room UPS batteries",
                 "The UPS in the server room failed its load test. Quotation attached in the next memo.",
                 to=es, priority="high")
        m = act(m, "approve_step", linehead, "Confirmed — the batteries are 6 years old.")
        made.append((m, f"line head approved, waiting for {ict_dir.get_full_name()}"))

        m = memo(fatima, "Staff training budget for 2027",
                 "Proposed allocation for staff training in the 2027 budget, for your approval.", to=es)
        m = act(m, "approve_step", tesla, "Recommended.")
        m = act(m, "approve_step", es, "Approved.")
        made.append((m, "published — director and ES approved"))

        m = memo(nikola, "Request for five new laptops",
                 "Five officers are sharing two laptops. Request to procure five units.", to=es)
        m = act(m, "approve_step", linehead, "Genuine need.")
        m = act(m, "return_to_sender", ict_dir, "Please attach the quotation and the asset register extract.")
        made.append((m, f"returned to the sender by {ict_dir.get_full_name()}"))

        m = memo(fatima, "Overtime claim for August",
                 "Claim for 40 hours of overtime during the August payroll run.", to=es)
        m = act(m, "reject_memo", tesla, "Overtime was not pre-approved; resubmit through the HR form.")
        made.append((m, "rejected by the director"))

        m = memo(hr, "Digitisation of personnel files",
                 "Proposal to scan and index all personnel files in the HR registry.", to=es)
        made.append((m, "waiting on nobody: Human Resources has no director — open it to see the "
                        "banner and the Re-check button"))

        m = memo(fatima, "Reconciliation of ICT asset purchases",
                 "Please confirm the asset tags for the purchases listed below.", to=ict_dir)
        made.append((m, f"Staff to Director route — waiting for {tesla.get_full_name()} (sender's director)"))

        m = memo(nikola, "Leave roster for the ICT department",
                 "Draft leave roster for December, for your review.", to=ict_dir)
        made.append((m, f"Staff to Director route — waiting for {deputy.get_full_name()} (deputy)"))

        m = memo(nikola, "Network outage report", "Root cause and actions from Tuesday's outage.",
                 to=deputy)
        made.append((m, "Staff to Deputy route — line head is waiting"))

        m = memo(tesla, "Closing of accounts for the year",
                 "All Finance units should submit their returns by 15 December.",
                 kind=Memo.DEPARTMENT, department=fin, requires_acknowledgment=True)
        made.append((m, "department circular — published straight away (the sender is the director)"))

        m = memo(es, "End-of-year staff audit",
                 "Every member of staff should confirm their records in the staff file by 30 November.",
                 kind=Memo.ALL_STAFF, requires_acknowledgment=True)
        made.append((m, "ES circular to all staff — published, needs acknowledgement"))

        m = memo(deputy, "ICT help-desk hours during the audit",
                 "The help desk will open at 7:30 for the two weeks of the audit.", kind=Memo.ALL_STAFF)
        made.append((m, f"all-staff circular — waiting for {ict_dir.get_full_name()}, then the ES"))

        m = memo(ict_dir, "Quarterly ICT report", "The ICT quarterly report is attached.", to=es)
        made.append((m, "Director to ES — no approval steps, delivered"))

        m = memo(nikola, "Request for a printer cartridge", "Two black cartridges for the ICT office.",
                 to=es, publish=False)
        made.append((m, "draft — open it to see the expected approval chain"))

        for m, note in made:
            self.say(f"{m.reference}  {m.get_status_display():20} {m.subject[len(DEMO_PREFIX):][:44]:44}  {note}")
        self.say(f"Demo sign-in password for …@{DEMO_DOMAIN}: {DEMO_PASSWORD}")

    # ------------------------------------------------------------------ remove
    def remove(self):
        from accounts.models import User
        from messaging.models import Memo, MemoRoute, MemoRouteRule
        from notifications.models import Notification
        memos = Memo.objects.filter(subject__startswith=DEMO_PREFIX)
        urls = [f"/messages/memos/{pk}/" for pk in memos.values_list("pk", flat=True)]
        n_notes = Notification.objects.filter(url__in=urls).delete()[0] if urls else 0
        n = memos.count()
        memos.delete()
        self.say(f"- {n} demo memo(s), {n_notes} notification(s)")
        rules = MemoRouteRule.objects.filter(name__in=[p[0] for p in PRIORITIES])
        self.say(f"- {rules.count()} rule(s)")
        rules.delete()
        for code, *_ in ROUTES:
            route = MemoRoute.objects.filter(code=code, description__contains=MARK).first()
            if route is None:
                continue
            if route.rules.exists() or route.memos.exists():
                self.say(f"  kept route “{route.name}” (still used)")
                continue
            route.delete()
            self.say(f"- route “{route.name}”")
        users = User.objects.filter(email__iendswith="@" + DEMO_DOMAIN)
        for user in users:
            if user.memos_sent.exists():
                self.say(f"  kept {user.email} (has sent memos)")
                continue
            self.say(f"- {user.email}")
            from staff.models import Staff
            Staff._base_manager.filter(user=user).hard_delete()   # the staff record made with the account
            user.delete()


class _DryRun(Exception):
    pass
