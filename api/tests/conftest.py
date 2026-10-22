"""API test fixtures. Users and institutions come from the shared QA
fixtures (qa_core/tests/conftest.py, phase2/5 fixtures)."""
import pytest
from rest_framework.test import APIClient

from qa_core.tests.conftest import *  # noqa: F401,F403
from qa_core.tests.phase2_fixtures import *  # noqa: F401,F403
from qa_core.tests.phase3_fixtures import *  # noqa: F401,F403
from qa_core.tests.phase4_fixtures import *  # noqa: F401,F403
from qa_core.tests.phase5_fixtures import *  # noqa: F401,F403

PASSWORD = "Test-pass-2026!"  # qa_core.tests.conftest.PASSWORD
STAFF_HOST = "localhost"
BASE = "/api/v1/"


@pytest.fixture
def staff_api():
    return APIClient(HTTP_HOST=STAFF_HOST)


@pytest.fixture
def portal_api(portal_host):
    return APIClient(HTTP_HOST=portal_host)


def bearer(client, user):
    """Authenticate ``client`` with a freshly minted access token."""
    from api.tokens import refresh_for

    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_for(user).access_token}")
    return client


def login(client, email, password=PASSWORD, **extra):
    return client.post(BASE + "auth/token/", {"email": email, "password": password, **extra}, format="json")


def err(response):
    return response.json()["error"]


@pytest.fixture
def staff_user(make_user):
    return make_user("fatima.staff@t.local")


@pytest.fixture
def institution(qa_seed, make_institution):
    return make_institution("APX")


@pytest.fixture
def institution_user(make_user, institution):
    return make_user("admin@apx.local", user_type="institution", institution=institution)


@pytest.fixture
def rp_user(make_user):
    return make_user("rp.person@t.local", user_type="resource_person")
