"""Shared fixtures for the Phase 1 QA tests."""
import io

import pytest
from django.conf import settings
from django.core.management import call_command
from django.test import Client

PASSWORD = "Test-pass-2026!"


@pytest.fixture
def qa_seed(db):
    """Roles, departments, QA settings, formats, policies and flows (no demo)."""
    call_command("seed_roles", "--sync", stdout=io.StringIO())
    call_command("seed_startup", "--skip-base-seeders", stdout=io.StringIO())


@pytest.fixture
def portal_host():
    return settings.QA_PORTAL_HOST.split(":")[0]


@pytest.fixture
def make_user(db):
    from django.contrib.auth.models import Group

    from accounts.models import Role, User
    from staff.models import Department

    def _make(email, *, role=None, dept=None, user_type="staff", institution=None, groups=(),
              superuser=False, must_change=False):
        user = User(email=email, first_name=email.split("@")[0].split(".")[0].title(), last_name="Test",
                    user_type=user_type, institution=institution, is_superuser=superuser,
                    is_staff=superuser, must_change_password=must_change,
                    role=Role.objects.filter(code=role).first() if role else None,
                    department=Department.objects.filter(code=dept).first() if dept else None)
        user.set_password(PASSWORD)
        user.save()
        for name in groups:
            user.groups.add(Group.objects.get(name=name))
        user = User.objects.get(pk=user.pk)  # fresh: no stale permission cache
        # v2.1 X23: Directors and above sign generated documents; fixtures give
        # them a (generated) signature like the demo seed does. Tests of the
        # "no signature" block retire it (documents/tests/test_x23_x24_*).
        from documents.testing import give_if_required

        give_if_required(user)
        return user

    return _make


@pytest.fixture
def make_institution(db):
    from institutions.models import Institution
    from staff.models import Department

    def _make(code, dept_code="dpap", name=None):
        return Institution.unscoped.create(code=code, name=name or f"{code} Polytechnic",
                                           handling_department=Department.objects.get(code=dept_code),
                                           status="approved")

    return _make


@pytest.fixture
def officer(qa_seed, make_user):
    return make_user("officer@t.local", role="officer", dept="dpap", groups=["QA Settings Admin"])


@pytest.fixture
def director(qa_seed, make_user):
    return make_user("director@t.local", role="director", dept="dpap")


@pytest.fixture
def client_for():
    def _client(user, host="localhost"):
        c = Client(HTTP_HOST=host)
        c.force_login(user)
        return c
    return _client


HX = {"HTTP_HX_REQUEST": "true"}


def hx(target):
    return {**HX, "HTTP_HX_TARGET": target}
