"""X33 / S-28: staff bank details - owner only, encrypted, masked, password to change."""
import ast
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.utils import timezone

from honoraria import services
from honoraria.models import Bank, BankDetailsLog, PayeeBankAccount

pytestmark = pytest.mark.django_db
PW = "Bank-test-2026!"
URL = "/accounts/profile/bank/"


def _user(email, superuser=False):
    from accounts.models import User

    u = User(email=email, first_name="Fatima", last_name="Danladi", user_type="staff",
             is_superuser=superuser, is_staff=superuser, must_change_password=False)
    u.set_password(PW)
    u.save()
    return u


@pytest.fixture
def bank(db):
    return Bank.objects.create(code="demo-bank", name="Demo Bank Plc", cbn_code="999", is_active=True)


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _save(c, bank, number="0123454821", name="Fatima Danladi", password=PW):
    return c.post(URL, {"action": "save", "bank": bank.pk, "account_number": number, "account_name": name,
                        "password": password})


def test_x33_encrypted_and_masked(bank):
    user = _user("fatima.bank@demo.nbte.local")
    c = _client(user)
    r = _save(c, bank)
    assert r.status_code == 302
    acc = PayeeBankAccount.objects.get(user=user)
    assert acc.account_last4 == "4821" and acc.name_match == "exact" and acc.confirmed_at
    # encrypted at rest: the number is nowhere in the row
    with connection.cursor() as cur:
        cur.execute("SELECT * FROM honoraria_payeebankaccount WHERE id = %s", [acc.pk])
        row = " ".join(str(v) for v in cur.fetchone())
    assert "0123454821" not in row
    from core import fieldcrypto

    assert fieldcrypto.decrypt(acc.account_number_enc) == "0123454821"
    # masked on the page, the API and the shared audit trail
    html = c.get(URL).content.decode()
    assert "4821" in html and "0123454821" not in html
    from core.models import AuditLogEntry

    for entry in AuditLogEntry.objects.filter(model_label="honoraria.PayeeBankAccount"):
        assert "0123454821" not in str(entry.changes) and "Fatima Danladi" not in str(entry.changes)
    # owner can reveal the full number with the password; logged
    html = c.post(URL, {"action": "reveal", "password": PW}).content.decode()
    assert "0123454821" in html
    assert BankDetailsLog.objects.filter(owner=user, action=BankDetailsLog.REVEALED).exists()


def test_x33_change_needs_password_and_emails_owner(bank):
    from notifications.models import EmailLog

    user = _user("musa.bank@demo.nbte.local")
    c = _client(user)
    _save(c, bank)
    r = _save(c, bank, number="9876543210", password="wrong-password")
    assert r.status_code == 403
    assert PayeeBankAccount.objects.get(user=user, status="active").account_last4 == "4821"
    _save(c, bank, number="9876543210")
    active = PayeeBankAccount.objects.get(user=user, status="active")
    assert active.account_last4 == "3210"
    assert PayeeBankAccount.objects.filter(user=user, status="superseded").count() == 1
    assert BankDetailsLog.objects.filter(owner=user, action=BankDetailsLog.CHANGED, before__contains="4821",
                                         after__contains="3210").exists()
    assert EmailLog.objects.filter(subject="Your bank details were changed", recipient_text=user.email).exists()
    # invalid NUBAN
    r = _save(c, bank, number="12345")
    assert r.status_code == 400


def test_x33_only_owner_not_superadmin_not_impersonation(bank):
    owner = _user("owner.bank@demo.nbte.local")
    _save(_client(owner), bank)
    admin = _user("root.bank@demo.nbte.local", superuser=True)
    c = _client(admin)
    # the admin's own page shows the admin's (empty) details, never the owner's
    html = c.get(URL).content.decode()
    assert "4821" not in html and "Add your bank details" in html
    # acting as the owner: page blocked, nothing shown, attempt logged
    from core.impersonation import IMPERSONATE_SESSION_KEY

    s = c.session
    s[IMPERSONATE_SESSION_KEY] = owner.pk
    s.save()
    r = c.get(URL)
    assert r.status_code == 403 and "4821" not in r.content.decode()
    assert "Hidden while acting as someone else" in r.content.decode()
    r = c.post(URL, {"action": "save", "bank": bank.pk, "account_number": "1111111111", "account_name": "X",
                     "password": PW})
    assert r.status_code == 403
    assert PayeeBankAccount.objects.get(user=owner, status="active").account_last4 == "4821"
    assert BankDetailsLog.objects.filter(owner=owner, action=BankDetailsLog.BLOCKED).exists()
    # no admin page and no model permissions exist for the accounts
    from django.contrib import admin

    assert PayeeBankAccount not in admin.site._registry and BankDetailsLog not in admin.site._registry
    from django.contrib.auth.models import Permission

    assert not Permission.objects.filter(content_type__app_label="honoraria",
                                         content_type__model="payeebankaccount").exists()


def test_x33_confirm_banner_and_yearly_reconfirm(bank):
    user = _user("banner.bank@demo.nbte.local")
    c = _client(user)
    assert "Add your bank details" in c.get("/accounts/profile/").content.decode()
    _save(c, bank)
    assert "Are your bank details still correct?" not in c.get("/accounts/profile/").content.decode()
    acc = PayeeBankAccount.objects.get(user=user)
    acc.confirmed_at = timezone.now() - timedelta(days=400)
    acc.save()
    assert "Are your bank details still correct?" in c.get("/accounts/profile/").content.decode()
    c.post("/accounts/profile/bank/confirm/", {"next": "/accounts/profile/"})
    acc.refresh_from_db()
    assert acc.confirmed_at > timezone.now() - timedelta(minutes=1)
    assert services.banner_for(user) is None


def test_x33_full_details_only_for_honoraria_release(bank):
    """Only the schedules service may release full numbers, and each release is logged."""
    user = _user("pay.bank@demo.nbte.local")
    _save(_client(user), bank)
    from honoraria import disbursement

    out = disbursement.details_for_schedule([user], schedule_reference="PS-TEST-1", generated_by=user)
    assert out[user.pk]["account_number"] == "0123454821"
    assert BankDetailsLog.objects.filter(owner=user, action=BankDetailsLog.RELEASED, note__contains="PS-TEST-1").exists()
    root = Path(settings.BASE_DIR)
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(("honoraria/", ".venv", "node_modules")) or "/migrations/" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "disbursement" not in text:
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "honoraria" in node.module and \
                    any(a.name == "disbursement" for a in node.names):
                offenders.append(rel)
            if isinstance(node, ast.ImportFrom) and node.module == "honoraria.disbursement":
                offenders.append(rel)
    assert not offenders, offenders
