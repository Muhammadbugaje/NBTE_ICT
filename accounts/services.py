"""Provisioning: admin sets or auto-generates a password, it is emailed, and the
user is forced to change it on first login (modelled on NAMETS: random
12-char password, must_change_password flag, credentials email)."""
from django.conf import settings
from django.db import transaction
from django.utils.crypto import get_random_string

from core.email_utils import send_templated_email

# Same alphabet as NAMETS (no look-alike characters).
_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#$%"


def generate_temporary_password(length=12):
    return get_random_string(length, _ALPHABET)


def send_credentials_email(user, temporary_password):
    return send_templated_email(
        subject=f"Your {settings.SITE_NAME} account",
        recipients=[user.email],
        template_name="emails/credentials.html", category="credentials",
        context={"user": user, "username": user.email, "temp_password": temporary_password},
    )


def prepare_new_user(user, temporary_password=None):
    """Set password + force-change flag on an unsaved/just-built user.
    Returns the plaintext so the caller can email it after the save commits."""
    temporary_password = temporary_password or generate_temporary_password()
    user.set_password(temporary_password)
    user.must_change_password = True
    user.is_active = True
    user._temporary_password = temporary_password
    return temporary_password


def provision_user(email, first_name, last_name, role=None, department=None, temporary_password=None, send_email=True):
    """Programmatic equivalent of the admin add-user flow."""
    from .models import User

    user = User(email=email, first_name=first_name, last_name=last_name, role=role, department=department)
    password = prepare_new_user(user, temporary_password)
    user.save()
    if send_email:
        transaction.on_commit(lambda: send_credentials_email(user, password))
    return user
