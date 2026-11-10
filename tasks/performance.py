"""Department performance counts (Master Plan v2.1 §9.6 step 5: H17, I3).

Counts per person and per division, with group (division) and individual
tasks kept apart. Every number is computed from the viewer's own visible task
list (``work.visibility``) with the same filters the task list uses, so each
count links to a list that shows exactly that many tasks.
"""
from __future__ import annotations

from collections import defaultdict

from django.core.exceptions import PermissionDenied
from django.utils import timezone

import org

from .models import Task, TaskAssignee


def month_start():
    now = timezone.localtime()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def can_view(user) -> bool:
    from work.visibility import can_see_all
    return bool(user.is_authenticated and (can_see_all(user, Task) or org.is_director(user)
                                           or user.headed_divisions.filter(is_active=True).exists()))


def scope(user, dept_id=None):
    """(department or None, divisions, people) the viewer may see counts for."""
    from accounts.selectors import staff_users
    from org.models import Division
    from staff.models import Department
    from work.visibility import can_see_all

    if not can_view(user):
        raise PermissionDenied("The performance page is for directors, division heads and the ES.")
    if can_see_all(user, Task):
        dept = Department.objects.filter(pk=dept_id).first() if dept_id else org.directed_department(user)
        if dept is None:
            dept = Department.objects.filter(pk=user.department_id).first()
    else:
        dept = org.directed_department(user)
    if dept is not None:
        divisions = Division.objects.filter(department=dept, is_active=True)
        people = staff_users().filter(department=dept)
    else:  # a division head who is not a director: their own division(s)
        divisions = Division.objects.filter(head=user, is_active=True)
        people = staff_users().filter(staff_profile__division__in=divisions)
        dept = divisions.first().department if divisions.exists() else None
    if dept is not None and not can_see_all(user, Task) and not org.is_director(user, dept):
        divisions = divisions.filter(head=user)
        people = staff_users().filter(staff_profile__division__in=divisions)
    return dept, divisions.select_related("head").order_by("name"), \
        people.select_related("role", "staff_profile__division").order_by("-role__rank", "first_name", "last_name")


def _days(a, b):
    return max(0.0, (b - a).total_seconds() / 86400.0)


def build(user, dept_id=None):
    from .views import _visible_tasks_qs

    dept, divisions, people = scope(user, dept_id)
    start = month_start()
    visible = _visible_tasks_qs(user).values("pk")
    people = list(people)
    ids = [p.pk for p in people]

    stats = defaultdict(lambda: {"open_individual": 0, "open_group": 0, "done_individual": 0, "done_group": 0,
                                 "days": []})
    seen = set()
    rows = (TaskAssignee.objects.filter(user_id__in=ids, task__in=visible)
            .values("user_id", "task_id", "task__state", "task__division_id", "task__completed_at",
                    "task__created_at"))
    for r in rows:
        key = (r["user_id"], r["task_id"])
        if key in seen:
            continue
        seen.add(key)
        s = stats[r["user_id"]]
        kind = "group" if r["task__division_id"] else "individual"
        if r["task__state"] not in Task.CLOSED_STATES:
            s[f"open_{kind}"] += 1
        elif r["task__state"] == Task.COMPLETED and r["task__completed_at"] and r["task__completed_at"] >= start:
            s[f"done_{kind}"] += 1
            s["days"].append(_days(r["task__created_at"], r["task__completed_at"]))

    person_rows = []
    for p in people:
        s = stats[p.pk]
        person_rows.append({
            "user": p,
            "division": getattr(getattr(p, "staff_profile", None), "division", None),
            "open_individual": s["open_individual"], "open_group": s["open_group"],
            "done_individual": s["done_individual"], "done_group": s["done_group"],
            "done_total": s["done_individual"] + s["done_group"],
            "avg_days": round(sum(s["days"]) / len(s["days"]), 1) if s["days"] else None,
        })

    div_rows = []
    vis_tasks = Task.objects.filter(pk__in=visible)
    for div in divisions:
        t = vis_tasks.filter(division=div)
        done = list(t.filter(state=Task.COMPLETED, completed_at__gte=start).values_list("created_at", "completed_at"))
        div_rows.append({
            "division": div,
            "open": t.exclude(state__in=Task.CLOSED_STATES).count(),
            "capacity_used": org.division_open_task_count(div),
            "max": div.max_open_tasks,
            "pending": t.filter(division_state=Task.DIV_PENDING).exclude(state__in=Task.CLOSED_STATES).count(),
            "done_month": len(done),
            "avg_days": round(sum(_days(a, b) for a, b in done) / len(done), 1) if done else None,
        })
    totals = {
        # Distinct tasks (as the linked list counts them), not person-task pairs.
        "open_individual": vis_tasks.filter(division__isnull=True, assignee_rows__user_id__in=ids)
        .exclude(state__in=Task.CLOSED_STATES).distinct().count(),
        "open_group": vis_tasks.filter(division__in=divisions).exclude(state__in=Task.CLOSED_STATES).count(),
        "done_individual": vis_tasks.filter(division__isnull=True, state=Task.COMPLETED, completed_at__gte=start,
                                            assignee_rows__user_id__in=ids).distinct().count(),
        "done_group": sum(r["done_month"] for r in div_rows),
    }
    return {"department": dept, "people": person_rows, "divisions": div_rows, "totals": totals,
            "month_start": start}
