"""Light fixtures for the Work endpoint tests: a few departments and ranked
roles (no full qa_seed, which takes about a minute per test)."""
import pytest

ROLES = [("executive-secretary", 999), ("director", 100), ("deputy-director", 80), ("officer", 20),
         ("admin-staff", 15), ("staff", 10)]
DEPTS = [("ict", "ICT"), ("finance", "Finance"), ("hr", "Human Resources")]


@pytest.fixture
def work_seed(db):
    from accounts.models import Role
    from staff.models import Department

    for code, rank in ROLES:
        Role.objects.get_or_create(code=code, defaults={"name": code.replace("-", " ").title(), "rank": rank,
                                                        "max_open_tasks": 10})
    for code, name in DEPTS:
        Department.objects.get_or_create(code=code, defaults={"name": name})


@pytest.fixture
def boss(work_seed, make_user):
    return make_user("boss.dir@t.local", role="director", dept="ict")


@pytest.fixture
def worker(work_seed, make_user):
    return make_user("worker.off@t.local", role="officer", dept="ict")


@pytest.fixture
def outsider(work_seed, make_user):
    return make_user("out.off@t.local", role="officer", dept="finance")


def grant(user, *perms):
    """Give ``user`` model permissions ("app.codename") and return a fresh copy."""
    from django.contrib.auth.models import Permission

    from accounts.models import User

    for perm in perms:
        app, code = perm.split(".")
        user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=code))
    return User.objects.get(pk=user.pk)


def api_as(user, host="localhost"):
    """A fresh staff-door client signed in as ``user`` (bearer() mutates the
    client it is given, so tests acting as two users need two clients)."""
    from rest_framework.test import APIClient

    from .conftest import bearer

    return bearer(APIClient(HTTP_HOST=host), user)


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
