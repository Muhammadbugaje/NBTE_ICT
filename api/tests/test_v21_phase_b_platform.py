"""v2.1 Phase B (pb-platform) API: me/signature/, me/bank-details/ (owner only)."""
import pytest

from honoraria.models import Bank, PayeeBankAccount

from .conftest import BASE, PASSWORD, bearer

pytestmark = pytest.mark.django_db


@pytest.fixture
def bank(db):
    return Bank.objects.create(code="demo-bank", name="Demo Bank Plc", cbn_code="999")


def test_x33_api_bank_details_owner_only(staff_api, staff_user, bank, make_user):
    api = bearer(staff_api, staff_user)
    assert api.get(BASE + "me/bank-details/").json() == {"bank": None}
    body = {"bank_code": "demo-bank", "account_number": "0123454821", "account_name": "Fatima Staff"}
    r = api.put(BASE + "me/bank-details/", {**body, "password": "wrong"}, format="json")
    assert r.status_code == 403 and not PayeeBankAccount.objects.exists()
    r = api.put(BASE + "me/bank-details/", {**body, "password": PASSWORD}, format="json")
    assert r.status_code == 200, r.content
    data = r.json()
    assert data["last4"] == "4821" and "0123454821" not in r.content.decode()
    assert r["Cache-Control"] == "no-store"
    assert api.post(BASE + "me/bank-details/confirm/").status_code == 200
    # another user (even a super admin) only ever sees their own
    root = make_user("root.api@t.local", superuser=True)
    other = bearer(staff_api.__class__(HTTP_HOST="localhost"), root)
    assert other.get(BASE + "me/bank-details/").json() == {"bank": None}


def test_x23_api_signature_status_has_no_image(staff_api, staff_user):
    r = bearer(staff_api, staff_user).get(BASE + "me/signature/")
    assert r.status_code == 200
    data = r.json()
    assert data["has_signature"] is False and "image" not in data and data["manage_url"].endswith("/signature/")


def test_x33_api_portal_user_refused(portal_api, institution_user):
    r = bearer(portal_api, institution_user).get(BASE + "me/bank-details/")
    assert r.status_code in (403, 404)
