"""python manage.py seed_memo_scenarios [--walk] [--remove]

A ready-made playground for the directorate memo routing (18 Oct 2026).

Creates four practice departments and their people, installs the routing
policy (`seed_memo_routes`), then sends one memo for every routing case and
prints where each memo is now and the path it will take. Sign in as the
person a memo is waiting on and approve it to watch it move.

    Alpha    director, deputy director, line head, two officers
    Bravo    director, deputy director, line head, two officers
    Charlie  two officers — no director, deputy or line head
             (shows what happens when nobody holds a step)
    Delta    director and one officer — no deputy or line head

Every account's password is printed at the end (they are practice accounts
with an @memo-scenarios.test address; delete them with --remove).

    --walk     approve every memo as whoever it waits on, all the way to
               delivery, and print the path each one actually took
    --remove   delete the practice memos, accounts and departments
               (the routing policy itself stays; use seed_memo_routes --remove)
"""
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Role, User
from staff.models import Department

DOMAIN = "memo-scenarios.test"
PASSWORD = "Memo-Scenario-2026!"
SUBJECT = "[Scenario] "

DEPARTMENTS = [("scn-alpha", "[Scenario] Alpha Directorate"), ("scn-bravo", "[Scenario] Bravo Directorate"),
               ("scn-charlie", "[Scenario] Charlie Directorate"), ("scn-delta", "[Scenario] Delta Directorate")]

# key, first name, role, department code
PEOPLE = [
    ("alpha.director", "Alpha Director", "director", "scn-alpha"),
    ("alpha.deputy", "Alpha Deputy", "deputy-director", "scn-alpha"),
    ("alpha.linehead", "Alpha LineHead", "division-head", "scn-alpha"),
    ("alpha.officer", "Alpha Officer", "officer", "scn-alpha"),
    ("alpha.officer2", "Alpha Officer2", "officer", "scn-alpha"),
    ("bravo.director", "Bravo Director", "director", "scn-bravo"),
    ("bravo.deputy", "Bravo Deputy", "deputy-director", "scn-bravo"),
    ("bravo.linehead", "Bravo LineHead", "division-head", "scn-bravo"),
    ("bravo.officer", "Bravo Officer", "officer", "scn-bravo"),
    ("bravo.officer2", "Bravo Officer2", "officer", "scn-bravo"),
    ("charlie.officer", "Charlie Officer", "officer", "scn-charlie"),
    ("charlie.officer2", "Charlie Officer2", "officer", "scn-charlie"),
    ("delta.director", "Delta Director", "director", "scn-delta"),
    ("delta.officer", "Delta Officer", "officer", "scn-delta"),
]

# title, sender, kind, target, expected path (approvers in order; "" = delivered directly)
SCENARIOS = [
    ("Director → own officer", "alpha.director", "user", "alpha.officer", ""),
    ("Director → whole own department", "alpha.director", "dept", "scn-alpha", ""),
    ("Director → another director", "alpha.director", "user", "bravo.director", ""),
    ("Director → officer in another directorate", "alpha.director", "user", "bravo.officer", "bravo.director"),
    ("Director → circular to another directorate", "alpha.director", "dept", "scn-bravo", "bravo.director"),
    ("Officer → own director", "alpha.officer", "user", "alpha.director", "alpha.linehead → alpha.deputy"),
    ("Line head → own director", "alpha.linehead", "user", "alpha.director", "alpha.deputy"),
    ("Deputy → own director", "alpha.deputy", "user", "alpha.director", ""),
    ("Officer → another director", "alpha.officer", "user", "bravo.director",
     "alpha.linehead → alpha.deputy → alpha.director"),
    ("Deputy → another director", "alpha.deputy", "user", "bravo.director", "alpha.director"),
    ("Officer → officer in another directorate", "alpha.officer", "user", "bravo.officer",
     "alpha.linehead → alpha.deputy → alpha.director → bravo.director"),
    ("Officer → colleague (same department)", "alpha.officer", "user", "alpha.officer2", "alpha.linehead"),
    ("Small department officer → another directorate", "delta.officer", "user", "bravo.officer",
     "delta.director → bravo.director"),
    ("Officer, no line head/deputy → own director", "delta.officer", "user", "delta.director", ""),
    ("Director → department with NO director", "alpha.director", "user", "charlie.officer",
     "waits: Charlie has no director"),
    ("Officer of a department with NO director → elsewhere", "charlie.officer", "user", "alpha.officer",
     "waits: Charlie has no director"),
]


class Command(BaseCommand):
    help = "Create practice departments, people and one memo per routing case (directorate routing)."

    def add_arguments(self, parser):
        parser.add_argument("--walk", action="store_true",
                            help="Approve every memo all the way and print the path each one took.")
        parser.add_argument("--remove", action="store_true", help="Delete the practice data.")

    def handle(self, *args, walk=False, remove=False, **opts):
        if remove:
            return self.remove()
        if not Role.objects.filter(code="director").exists():
            raise CommandError("Run `python manage.py seed_roles --sync` first.")
        call_command("seed_memo_routes", stdout=_Silent())
        with transaction.atomic():
            people, depts, created = self.build()
            memos = self.send(people, depts)
        self.report(memos, people, walk)
        if created:
            self.stdout.write(self.style.WARNING(
                f"\nPractice accounts (password for all: {PASSWORD}) — sign in on the internal system:"))
            for key, first, role, dept in PEOPLE:
                self.stdout.write(f"  {key + '@' + DOMAIN:<40} {role:<16} {dict(DEPARTMENTS)[dept]}")
        self.stdout.write("\nRemove everything with: python manage.py seed_memo_scenarios --remove")

    # ------------------------------------------------------------------
    def build(self):
        roles = {r.code: r for r in Role.objects.all()}
        depts = {}
        for code, name in DEPARTMENTS:
            depts[code], _ = Department.objects.get_or_create(code=code, defaults={"name": name})
        people, created = {}, False
        broadcast = Permission.objects.filter(codename="send_memo_broadcast").first()
        for key, first, role, dept in PEOPLE:
            email = f"{key}@{DOMAIN}"
            user = User.objects.filter(email=email).first()
            if user is None:
                user = User.objects.create_user(email, PASSWORD, first_name=first, last_name="(Scenario)",
                                                role=roles.get(role), department=depts[dept])
                user.must_change_password = False
                user.save()
                created = True
                if broadcast is not None and role in ("director", "deputy-director"):
                    user.user_permissions.add(broadcast)  # department circulars
            people[key] = User.objects.get(pk=user.pk)
        return people, depts, created

    def send(self, people, depts):
        from messaging import memos as memo_service
        from messaging.models import Memo

        memos = []
        for title, sender, kind, target, expected in SCENARIOS:
            subject = SUBJECT + title
            memo = Memo.objects.filter(subject=subject, sender=people[sender]).first()
            if memo is None:
                kwargs = {"visibility": Memo.SPECIFIC_USER, "target_user": people.get(target)}
                if kind == "dept":
                    kwargs = {"visibility": Memo.DEPARTMENT, "department": depts[target]}
                memo = memo_service.create_memo(sender=people[sender], subject=subject,
                                                body=f"Practice memo: {title}.\nExpected path: "
                                                     f"{expected or 'delivered directly'}.", **kwargs)
                memo_service.publish_memo(memo, people[sender])
            memos.append((title, memo, expected))
        return memos

    def report(self, memos, people, walk):
        from messaging import routing
        from messaging.models import Memo

        self.stdout.write(self.style.SUCCESS("Directorate memo routing — practice memos"))
        self.stdout.write(f"  {'Case':<52} {'Ref':<16} {'Now':<34} Expected path")
        failures = 0
        for title, memo, expected in memos:
            memo.refresh_from_db()
            taken = []
            if walk:
                for _ in range(10):
                    if memo.status != Memo.AWAITING:
                        break
                    actor = routing.actor_for_memo(memo)
                    if actor is None:
                        break
                    taken.append(actor.email.split("@")[0])
                    routing.approve_step(memo, actor, "Approved by seed_memo_scenarios --walk")
                    memo.refresh_from_db()
            if memo.status == Memo.PUBLISHED:
                now = "delivered"
            elif routing.is_parked(memo):
                now = "WAITING — nobody holds the step"
            elif memo.status == Memo.AWAITING:
                who = routing.actor_for_memo(memo)
                now = f"waiting on {who.email.split('@')[0]}" if who else "waiting"
            else:
                now = memo.get_status_display()
            line = f"  {title:<52} {memo.reference:<16} {now:<34} {expected or 'delivered directly'}"
            if walk and not expected.startswith("waits"):
                ok = " → ".join(taken) == expected and memo.status == Memo.PUBLISHED
                failures += not ok
                line += "   ✔" if ok else f"   ✘ took: {' → '.join(taken) or 'direct'}"
            self.stdout.write(line)
        if walk:
            msg = "every case followed its expected path" if not failures else f"{failures} case(s) differ"
            (self.stdout.write(self.style.SUCCESS("  Result: " + msg)) if not failures
             else self.stdout.write(self.style.ERROR("  Result: " + msg)))

    def remove(self):
        from messaging.models import Memo

        users = User.objects.filter(email__endswith="@" + DOMAIN)
        n_memos = 0
        for memo in Memo.objects.filter(subject__startswith=SUBJECT, sender__in=users):
            memo.delete()
            n_memos += 1
        n_users = users.count()
        users.delete()
        n_depts, _ = Department.objects.filter(code__in=[c for c, _ in DEPARTMENTS]).delete()
        self.stdout.write(self.style.SUCCESS(
            f"Removed {n_memos} practice memo(s), {n_users} account(s) and the practice departments."))


class _Silent:
    def write(self, *a, **k):
        pass

    def flush(self):
        pass
