"""Demo organisation data (demo accounts only), for `seed_org --with-demo`
and later `seed_nbte --with-demo`. Idempotent; never touches real staff."""
from __future__ import annotations

DEMO_DOMAIN = "@demo.nbte.local"


def seed_org_demo(stdout=None):
    from django.contrib.auth.models import Group

    import org
    from accounts.models import User
    from staff.models import Department, Office

    from .models import Division

    say = stdout or (lambda *_: None)
    dept = Department.objects.filter(code="dpap").first()
    director = User.objects.filter(email="qa.director" + DEMO_DOMAIN).first()
    if dept is None or director is None or not org.is_director(director, dept):
        say("org demo: DPAP or its demo director is missing — skipped.")
        return 0
    made = 0
    for name, cap, head_email in (("Polytechnic Programmes (Demo)", 8, "qa.division_head" + DEMO_DOMAIN),
                                  ("Monotechnics (Demo)", 6, None)):
        div = Division.objects.filter(department=dept, name=name).first()
        if div is None:
            head = User.objects.filter(email=head_email).first() if head_email else None
            div = org.create_division(dept, name, director, max_open_tasks=cap, head=head)
            made += 1
    first = Division.objects.filter(department=dept, name__endswith="(Demo)").order_by("name").last()
    for email in ("qa.officer" + DEMO_DOMAIN, "qa.busy_officer" + DEMO_DOMAIN):
        u = User.objects.filter(email=email).first()
        if u is not None and getattr(u, "staff_profile", None) is not None and not u.staff_profile.division_id \
                and u.staff_profile.department_id == dept.pk:
            org.move_to_division(u.staff_profile, first, director)
    office = Office.objects.filter(name="JAMB Desk (Demo)").first()
    if office is None:
        office = Office.objects.create(name="JAMB Desk (Demo)",
                                       description="Demo office: everyone posted here can see department requests.")
        group = Group.objects.filter(name="QA Desk").first()
        if group is not None:
            org.set_office_groups(office, [group], None)
        made += 1
    officer = User.objects.filter(email="qa.officer" + DEMO_DOMAIN).first()
    if officer is not None and not org.active_grants(user=officer).exists():
        try:
            org.grant_permission(director, officer, "accounts.approve_requests", days=14,
                                 reason="Demo: covering approvals during the director's leave")
            made += 1
        except org.OrgRuleError as exc:
            say(f"org demo grant skipped: {exc}")
    say(f"org demo: {made} item(s) created.")
    return made
