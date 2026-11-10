import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError

from accounts.forms import ProfileForm
from accounts.models import User

pytestmark = pytest.mark.django_db


def test_email_changes_stay_in_sync_and_remain_the_sign_in_name():
    password = "Email-sync-test-2026!"
    user = User.objects.create_user(
        "email-sync-old@example.test",
        password,
        first_name="Email",
        last_name="Sync",
        must_change_password=False,
    )
    staff = user.staff_profile

    profile = ProfileForm(user, data={
        "first_name": "Email",
        "last_name": "Sync",
        "email": "email-sync-profile@example.test",
    })
    assert profile.is_valid(), profile.errors
    profile.save()
    user.refresh_from_db()
    staff.refresh_from_db()
    assert user.email == staff.email == "email-sync-profile@example.test"
    assert authenticate(username=user.email, password=password) == user

    staff.email = "email-sync-directory@example.test"
    staff.save(update_fields=["email"])
    user.refresh_from_db()
    staff.refresh_from_db()
    assert user.email == staff.email == "email-sync-directory@example.test"
    assert authenticate(username=user.email, password=password) == user
    assert authenticate(username="email-sync-profile@example.test", password=password) is None

    other = User.objects.create_user("email-sync-taken@example.test", password)
    staff.email = other.email
    with pytest.raises(ValidationError, match="Another account already uses this email"):
        staff.save(update_fields=["email"])
    user.refresh_from_db()
    staff.refresh_from_db()
    assert user.email == staff.email == "email-sync-directory@example.test"
