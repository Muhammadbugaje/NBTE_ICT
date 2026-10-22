"""Phase 9 fixtures for the accounts tests (staff and portal users)."""
import pytest
from django.conf import settings

P9_PASSWORD = "Phase9-Test-pass!"


@pytest.fixture
def portal_host():
    return settings.QA_PORTAL_HOST.split(":")[0]


@pytest.fixture
def p9_make_user(db):
    from accounts.models import User

    def _make(email, *, user_type="staff", institution=None, superuser=False, **extra):
        user = User(email=email, first_name=email.split("@")[0].title(), last_name="Tester",
                    user_type=user_type, institution=institution, is_superuser=superuser, is_staff=superuser,
                    must_change_password=False, **extra)
        user.set_password(P9_PASSWORD)
        user.save()
        return User.objects.get(pk=user.pk)

    return _make


@pytest.fixture
def staff_user(p9_make_user):
    return p9_make_user("ada@nbte.test")


@pytest.fixture
def other_staff(p9_make_user):
    return p9_make_user("bola@nbte.test")


@pytest.fixture
def institution(db):
    from institutions.models import Institution
    from staff.models import Department

    dept, _ = Department.objects.get_or_create(code="dpap", defaults={"name": "DPAP"})
    return Institution.unscoped.create(code="P9POLY", name="Phase Nine Polytechnic", handling_department=dept,
                                       status="approved")


@pytest.fixture
def institution_user(p9_make_user, institution):
    return p9_make_user("registrar@p9poly.test", user_type="institution", institution=institution)
