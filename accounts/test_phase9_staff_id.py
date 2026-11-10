"""Phase 9: digital staff ID with a rotating QR code and the public verify page."""
import time
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse

from accounts import staff_id

pytestmark = pytest.mark.django_db


@pytest.fixture
def card_user(staff_user):
    from staff.models import Department, Designation

    dept, _ = Department.objects.get_or_create(code="ict", defaults={"name": "ICT Directorate"})
    staff = staff_user.staff_profile
    staff.department = dept
    staff.designation = Designation.objects.create(name="Principal Programmer")
    staff.employee_no = "NBTE-1413"
    staff.phone = "08011112222"
    staff.save()
    return staff_user


def test_token_round_trip(card_user):
    token = staff_id.make_staff_token(card_user)
    result = staff_id.verify_staff_token(token)
    assert result.is_valid and result.user_id == card_user.pk
    assert result.name == card_user.get_full_name()
    assert result.designation == "Principal Programmer" and result.department == "ICT Directorate"


def test_token_expires_after_two_minutes(card_user):
    token = staff_id.make_staff_token(card_user)
    with mock.patch("time.time", return_value=time.time() + 121):
        assert staff_id.verify_staff_token(token).status == staff_id.EXPIRED
    with mock.patch("time.time", return_value=time.time() + 100):
        assert staff_id.verify_staff_token(token).status == staff_id.VALID


def test_tampered_and_garbage_tokens_are_invalid(card_user):
    token = staff_id.make_staff_token(card_user)
    assert staff_id.verify_staff_token(token[:-2] + "xx").status == staff_id.INVALID
    assert staff_id.verify_staff_token("hello").status == staff_id.INVALID
    from django.core import signing
    other_salt = signing.TimestampSigner(salt="something-else").sign_object({"u": card_user.pk, "w": 1})
    assert staff_id.verify_staff_token(other_salt).status == staff_id.INVALID


def test_inactive_and_non_staff_are_invalid(card_user, institution_user):
    token = staff_id.make_staff_token(card_user)
    card_user.is_active = False
    card_user.save()
    assert staff_id.verify_staff_token(token).status == staff_id.INVALID
    assert staff_id.verify_staff_token(staff_id.make_staff_token(institution_user)).status == staff_id.INVALID


def test_my_staff_id_page(card_user):
    c = Client()
    c.force_login(card_user)
    r = c.get(reverse("accounts:staff_id"))
    html = r.content.decode()
    assert r.status_code == 200
    assert "img/brand/" in html and "NBTE-1413" in html and "Principal Programmer" in html
    # Phase B v2.1 (E1/E3): design 12 card; printing goes through the print-format page.
    assert "<svg" in html and reverse("staff:id_print") in html
    assert "08011112222" not in html and card_user.email not in html.split('class="sid-stage"')[1]


def test_qr_endpoint_returns_fresh_svg(card_user):
    c = Client()
    c.force_login(card_user)
    r = c.get(reverse("accounts:staff_id_qr"))
    data = r.json()
    assert r.status_code == 200 and data["svg"].startswith("<svg") and data["refresh_seconds"] == 60
    assert data["url"].startswith("http://testserver/verify/staff/")
    assert "no-store" in r["Cache-Control"] or "max-age=0" in r["Cache-Control"]


def test_staff_id_not_for_portal_users(institution_user):
    c = Client()
    c.force_login(institution_user)
    # institution users are refused on the internal host before the view
    assert c.get(reverse("accounts:staff_id")).status_code in (403, 404)
    assert staff_id.is_eligible(institution_user) is False


def test_verify_page_valid_shows_no_contact_details(card_user):
    token = staff_id.make_staff_token(card_user)
    r = Client().get(reverse("staff_verify", args=[token]))
    html = r.content.decode()
    assert r.status_code == 200 and "Valid NBTE staff" in html
    assert card_user.get_full_name() in html and "ICT Directorate" in html and "Principal Programmer" in html
    assert card_user.email not in html and "08011112222" not in html
    assert r["X-Robots-Tag"].startswith("noindex")


def test_verify_page_expired_and_invalid(card_user):
    token = staff_id.make_staff_token(card_user)
    with mock.patch("time.time", return_value=time.time() + 500):
        assert "Code expired" in Client().get(reverse("staff_verify", args=[token])).content.decode()
    assert "Not a valid NBTE ID" in Client().get(reverse("staff_verify", args=["forged"])).content.decode()


def test_verify_page_is_rate_limited(card_user):
    c = Client()
    url = reverse("staff_verify", args=["x"])
    for _ in range(30):
        assert c.get(url).status_code == 200
    assert c.get(url).status_code == 429


def test_verify_is_not_on_portal_host(card_user, portal_host):
    token = staff_id.make_staff_token(card_user)
    assert Client(HTTP_HOST=portal_host).get(f"/verify/staff/{token}/").status_code == 404


def test_profile_and_menu_link_to_staff_id(card_user):
    c = Client()
    c.force_login(card_user)
    assert reverse("accounts:staff_id") in c.get(reverse("accounts:profile")).content.decode()
