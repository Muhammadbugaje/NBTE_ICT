"""Phase C fixtures: an ICT department with a director seat, a deputy, a
division head and officers, plus an HR department and the ES."""
import io

import pytest
from django.core.management import call_command

PW = "Tasks-test-pass-2026!"
_n = {"i": 0}


@pytest.fixture
def roles(db):
    from accounts.models import Role
    call_command("seed_roles", "--sync", stdout=io.StringIO())
    return {r.code: r for r in Role.objects.all()}


@pytest.fixture
def depts(db):
    from staff.models import Department
    return {
        "ict": Department.objects.create(name="ICT (t)", code="ict"),
        "hr": Department.objects.create(name="HR (t)", code="hr"),
    }


def mk(email, role=None, dept=None):
    from accounts.models import Role, User
    from staff.models import Staff

    user = User.objects.create_user(email, PW, first_name=email.split("@")[0].split(".")[0].title(),
                                    last_name="T", role=Role.objects.filter(code=role).first() if role else None,
                                    department=dept, must_change_password=False)
    _n["i"] += 1
    staff = Staff.objects.filter(user=user).first()
    if staff is None:
        staff = Staff(user=user, employee_no=f"C{_n['i']:05d}", first_name=user.first_name, last_name="T")
    staff.department = dept
    staff.save()
    return User.objects.get(pk=user.pk)


def fresh(u):
    from accounts.models import User
    return User.objects.get(pk=u.pk)


@pytest.fixture
def people(roles, depts):
    """es, director (seat), deputy, head (division head), officer, officer2,
    staff (lowest), hr_dir (seat), hr_officer."""
    import org
    from staff.models import Department

    ict, hr = depts["ict"], depts["hr"]
    p = {
        "es": mk("es@t.local", "executive-secretary", hr),
        "director": mk("director@t.local", "director", ict),
        "deputy": mk("deputy@t.local", "deputy-director", ict),
        "head": mk("head@t.local", "division-head", ict),
        "officer": mk("officer@t.local", "officer", ict),
        "officer2": mk("officer2@t.local", "officer", ict),
        "staff": mk("staff@t.local", "staff", ict),
        "hr_dir": mk("hrdir@t.local", "director", hr),
        "hr_officer": mk("hrofficer@t.local", "officer", hr),
    }
    Department.objects.filter(pk=ict.pk).update(director=p["director"])
    Department.objects.filter(pk=hr.pk).update(director=p["hr_dir"])
    div = org.create_division(ict, "Technical Support", None, max_open_tasks=3)
    org.set_division_head(div, p["head"], None)
    for key in ("officer", "officer2"):
        org.move_to_division(fresh(p[key]).staff_profile, div, None)
    p = {k: fresh(v) for k, v in p.items()}
    p["division"] = div
    return p
