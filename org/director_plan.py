"""The one-director rule applied to existing data (B1).

Shared by ``manage.py plan_single_director`` (dry run, report) and the data
migration ``org/0005_single_director`` (apply), so both use the same rule:

1. ``Department.director`` already set (and active) -> kept.
2. ``Department.head`` set, active staff, holding the director role -> seated.
3. Otherwise the senior-most active staff account holding the ``director``
   role whose department is this department. Seniority order: designation
   rank (high first), grade level (high first), date of present appointment
   (earlier first), date joined the Board (earlier first), account id.
4. Nobody qualifies -> the seat stays empty.

Other director-role holders keep their role but not the seat; they are listed
as conflicts. People whose designation looks like a director (rank 90-99) but
who lack the role are listed as "designation suggests director" for HR.

The functions take model classes so the migration can pass historical models.
"""
import datetime

_FAR = datetime.date(9999, 12, 31)


def _seniority_key(user):
    staff = getattr(user, "_plan_staff", None)
    d_rank = staff.designation.rank if staff is not None and staff.designation_id else 0
    g_num = staff.grade.number if staff is not None and getattr(staff, "grade_id", None) else 0
    present = (staff.date_present_appointment if staff is not None else None) or _FAR
    joined = (staff.date_joined_board if staff is not None else None) or _FAR
    return (-d_rank, -g_num, present, joined, user.pk)


def _describe(user):
    staff = getattr(user, "_plan_staff", None)
    return {
        "user_id": user.pk,
        "name": f"{user.first_name} {user.last_name}".strip() or user.email,
        "email": user.email,
        "designation": (staff.designation.name if staff is not None and staff.designation_id else ""),
        "designation_rank": (staff.designation.rank if staff is not None and staff.designation_id else 0),
        "grade": (staff.grade.number if staff is not None and getattr(staff, "grade_id", None) else None),
        "present_appointment": (staff.date_present_appointment.isoformat()
                                if staff is not None and staff.date_present_appointment else ""),
    }


def plan(Department, User, Staff, director_code="director"):
    """Return one dict per department: current, chosen, rule, others, hints."""
    staff_by_user = {s.user_id: s for s in Staff.objects.filter(user__isnull=False)
                     .select_related("designation", "grade")}
    holders = list(User.objects.filter(role__code=director_code, is_active=True, user_type="staff"))
    for u in holders:
        u._plan_staff = staff_by_user.get(u.pk)
    rows = []
    seated = set(Department.objects.exclude(director__isnull=True).values_list("director_id", flat=True))
    for dept in Department.objects.order_by("name"):
        in_dept = [u for u in holders
                   if u.department_id == dept.pk
                   or (u._plan_staff is not None and u._plan_staff.department_id == dept.pk)]
        in_dept.sort(key=_seniority_key)
        row = {"department_id": dept.pk, "code": dept.code, "name": dept.name,
               "current": None, "chosen": None, "rule": "", "others": [], "hints": []}
        current = User.objects.filter(pk=dept.director_id, is_active=True).first() if dept.director_id else None
        if current is not None:
            current._plan_staff = staff_by_user.get(current.pk)
            row["current"] = _describe(current)
            row["chosen"] = _describe(current)
            row["rule"] = "already seated"
            chosen = current
        else:
            chosen = None
            head = User.objects.filter(pk=dept.head_id, is_active=True).first() if dept.head_id else None
            if head is not None and head in in_dept and head.pk not in seated:
                chosen, row["rule"] = head, "Department.head holds the director role"
            else:
                for u in in_dept:
                    if u.pk not in seated:
                        chosen, row["rule"] = u, "senior-most director-role holder"
                        break
            if chosen is not None:
                seated.add(chosen.pk)
                row["chosen"] = _describe(chosen)
            else:
                row["rule"] = "no director-role holder: seat stays empty"
        row["others"] = [_describe(u) for u in in_dept if chosen is None or u.pk != chosen.pk]
        # Hints: designation says director, role does not.
        for s in staff_by_user.values():
            if s.department_id == dept.pk and s.designation_id and 90 <= (s.designation.rank or 0) <= 99:
                if not any(u.pk == s.user_id for u in in_dept):
                    u = User.objects.filter(pk=s.user_id).first()
                    if u is not None:
                        u._plan_staff = s
                        row["hints"].append(_describe(u))
        rows.append(row)
    return rows


def apply(Department, User, Staff, director_code="director"):
    """Seat the chosen director of every department with an empty seat.
    Returns the plan rows (with ``applied`` set)."""
    rows = plan(Department, User, Staff, director_code)
    for row in rows:
        row["applied"] = False
        if row["chosen"] and row["rule"] != "already seated":
            Department.objects.filter(pk=row["department_id"], director__isnull=True) \
                .update(director_id=row["chosen"]["user_id"])
            row["applied"] = True
    from qa_core.departments import clear_cache

    clear_cache()   # director-seat memo (bulk update bypasses signals)
    return rows


def to_markdown(rows, *, title="Single-director plan (B1)", applied=False):
    lines = [f"# {title}", "",
             "Rule: keep a seated director; else Department.head if it holds the director role; else the "
             "senior-most active director-role holder of the department (designation rank, grade level, "
             "date of present appointment, date joined the Board, account id). Others keep their role but "
             "not the seat.", "",
             f"Mode: {'APPLIED' if applied else 'dry run (nothing changed)'}", "",
             "| Department | Code | Director (seat) | Rule | Conflicts: keep role, no seat | Designation suggests director |",
             "|---|---|---|---|---|---|"]
    conflicts = 0
    for r in rows:
        chosen = (f"{r['chosen']['name']} ({r['chosen']['email']})" if r["chosen"] else "—")
        others = "; ".join(f"{o['name']} ({o['email']})" for o in r["others"]) or "—"
        hints = "; ".join(f"{h['name']} — {h['designation']}" for h in r["hints"]) or "—"
        conflicts += len(r["others"])
        lines.append(f"| {r['name']} | {r['code']} | {chosen} | {r['rule']} | {others} | {hints} |")
    seated = sum(1 for r in rows if r["chosen"])
    lines += ["", f"Departments: {len(rows)} · with a director: {seated} · empty seats: {len(rows) - seated} · "
                  f"director-role holders without a seat: {conflicts}"]
    return "\n".join(lines) + "\n"
