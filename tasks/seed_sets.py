"""seed_nbte data sets owned by tasks (Phase C).

``tasks.demo`` (only with ``--with-demo``): Phase C demo tasks in the DPAP
demo department, made through the services so every rule applies. Demo
accounts only (``@demo.nbte.local``); idempotent (matched by title + creator).
"""
from core.seeding import seed_set

DEMO = "@demo.nbte.local"
TAG = "phase-c-demo"


def _u(local):
    from accounts.models import User
    return User.objects.filter(email=local + DEMO, is_active=True).first()


def _existing(title, creator):
    from tasks.models import Task
    return Task.objects.filter(title=title, created_by=creator).first()


@seed_set("tasks.demo", group="demo", depends=("demo",), order=910, demo_only=True)
def seed_tasks_demo(ctx):
    made = build_demo(dry_run=ctx.dry_run, note=ctx.note)
    if made:
        ctx.res.created += made
    else:
        ctx.res.unchanged += 1


def build_demo(dry_run=False, note=print):
    """Create the Phase C demo; returns the number of tasks created."""
    from django.db import transaction

    import org
    from org.demo import seed_org_demo
    from org.models import Division
    from staff.models import Department
    from tasks import division as div
    from tasks import services
    from tasks.models import Task

    dept = Department.objects.filter(code="dpap").first()
    director, deputy = _u("qa.director"), _u("qa.deputy_director")
    head, officer, busy = _u("qa.division_head"), _u("qa.officer"), _u("qa.busy_officer")
    if dept is None or None in (director, deputy, head, officer, busy) or not org.is_director(director, dept):
        note("tasks demo: DPAP demo accounts missing — skipped.")
        return 0
    if dry_run:
        note("tasks demo: would create the Phase C demo tasks.")
        return 0
    seed_org_demo()
    division = Division.objects.filter(department=dept, head=head, is_active=True).first()
    made = 0

    with transaction.atomic():
        title = "Compile Q3 accreditation statistics for EXCO (Demo)"
        if _existing(title, deputy) is None:
            t = services.create_task(
                created_by=deputy, title=title, owner_department=dept, priority=Task.HIGH, reviewer=director,
                tags=[TAG], assignees=[officer, busy, head],
                description=("Pull programme accreditation results for July to September from the QA portal and "
                             "the zonal returns. Break them down by institution type and outcome (full, interim, "
                             "deferred, denied).\n\nEXCO meets on Thursday. Use the standard EXCO template."))
            services.open_task(t, deputy)
            services.accept_and_start(t, officer)            # timer runs
            services.accept_and_start(t, busy)
            services.mark_assignee_done(t, busy, note="Exported portal results for Q3.")
            services.add_comment(t, deputy, "Leave out the two deferred cases still under appeal; I will brief "
                                            "EXCO on those myself.", internal=True)
            services.add_comment(t, busy, "Kano numbers include one programme counted twice in Q2. Corrected in "
                                          "the export.", internal=True)
            services.add_comment(t, officer, "Zonal returns from Enugu arrive tomorrow.")
            kids = services.break_into_subtasks(t, deputy, [
                {"title": "Export portal results for Q3", "assignee": busy},
                {"title": "Collect zonal returns from Kano and Enugu", "assignee": officer},
            ])
            services.accept_and_start(kids[0], busy)
            services.mark_assignee_done(kids[0], busy)
            services.accept_and_start(kids[1], officer)
            made += 1

        title = "Review monotechnic resubmissions (Demo)"
        if division is not None and _existing(title, director) is None:
            t = services.create_task(created_by=director, title=title, owner_department=dept, tags=[TAG],
                                     description="Group task waiting for the division head to accept.")
            if org.division_has_capacity(division):
                div.route_to_division(t, director, division, note="Please take this on this week.")
            made += 1

        title = "Prepare the polytechnic visit schedule (Demo)"
        if division is not None and _existing(title, director) is None:
            t = services.create_task(created_by=director, title=title, owner_department=dept, tags=[TAG],
                                     description="Group task accepted by the division; members were added.")
            if org.division_has_capacity(division):
                div.route_to_division(t, director, division)
                div.accept_division_task(t, head)
            made += 1

        title = "Archive the 2025 accreditation letters (Demo)"
        if _existing(title, deputy) is None:
            t = services.create_task(created_by=deputy, title=title, owner_department=dept, tags=[TAG],
                                     requires_review=True, assignees=[officer])
            services.open_task(t, deputy)
            services.accept_and_start(t, officer)
            services.mark_assignee_done(t, officer)
            services.confirm_all_parts(t, deputy)            # completed this month
            made += 1

        title = "Update the division contact sheet (Demo)"
        if division is not None and _existing(title, director) is None:
            t = services.create_task(created_by=director, title=title, owner_department=dept, tags=[TAG])
            if org.division_has_capacity(division):
                div.route_to_division(t, director, division)
                div.accept_division_task(t, head)
                div.complete_division_task(t, head, note="Sheet updated and shared.")
            made += 1
    note(f"tasks demo: {made} task(s) created. Try qa.deputy_director / qa.officer / qa.division_head{DEMO}.")
    return made
