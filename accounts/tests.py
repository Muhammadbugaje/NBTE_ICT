from unittest import mock

import pytest
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.management import call_command
from django.test import Client, RequestFactory

from accounts.models import Role, User, UserPermissionOverride
from accounts.services import provision_user

pytestmark = pytest.mark.django_db


def _perm(label):
    app, code = label.split(".")
    return Permission.objects.get(content_type__app_label=app, codename=code)


def test_provision_generates_password_emails_and_forces_change(django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        u = provision_user("new@x.org", "New", "User")
    assert u.must_change_password is True
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    pw = u._temporary_password
    assert pw in body and "new@x.org" in body
    assert u.check_password(pw) and len(pw) == 12


def test_admin_set_password_is_used(django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        u = provision_user("n2@x.org", "N", "Two", temporary_password="Chosen-By-Admin-9")
    assert u.check_password("Chosen-By-Admin-9")
    assert "Chosen-By-Admin-9" in mail.outbox[0].body


def test_admin_password_reset_reports_queue_failure():
    from accounts.user_views import _send_reset_email

    user = User.objects.create_user("reset@x.org", "Pass-word-123")
    request = RequestFactory().post("/", HTTP_HOST="testserver")
    with mock.patch("core.email_utils.send_templated_email", return_value=None) as queue, \
            mock.patch("django.core.mail.send_mail") as fallback:
        assert _send_reset_email(request, user, "Temporary-Pass-42", True) is False
    assert queue.call_args.kwargs["category"] == "credentials"
    fallback.assert_not_called()


def test_first_login_forces_password_change_then_releases():
    u = provision_user("f@x.org", "F", "L", temporary_password="Temp-Passw0rd-1", send_email=False)
    c = Client()
    assert c.login(username="f@x.org", password="Temp-Passw0rd-1")
    r = c.get("/")
    assert r.status_code == 302 and r["Location"].endswith("/accounts/change-password/")
    # other pages are blocked too, but the change page and logout are reachable
    assert c.get("/accounts/change-password/").status_code == 200
    bad = c.post("/accounts/change-password/", {"new_password": "Temp-Passw0rd-1", "confirm_password": "Temp-Passw0rd-1"})
    assert bad.status_code == 200  # same as temporary -> rejected
    weak = c.post("/accounts/change-password/", {"new_password": "12345678", "confirm_password": "12345678"})
    assert weak.status_code == 200
    ok = c.post("/accounts/change-password/", {"new_password": "Brand-New-Pass-42", "confirm_password": "Brand-New-Pass-42"})
    assert ok.status_code == 302
    u.refresh_from_db()
    assert u.must_change_password is False
    assert c.get("/").status_code == 200  # session survived the hash change


def test_login_and_logout_via_views():
    User.objects.create_user("l@x.org", "Pass-word-123", must_change_password=False)
    c = Client()
    r = c.post("/accounts/login/", {"username": "L@X.org".lower(), "password": "Pass-word-123"})
    assert r.status_code == 302
    assert c.get("/").status_code == 200
    assert c.post("/accounts/logout/").status_code == 302
    assert c.get("/").status_code == 302  # back to login


def test_role_baseline_and_user_overrides():
    role = Role.objects.create(code="r", name="R")
    role.permissions.add(_perm("staff.view_staff"))
    u = User.objects.create_user("p@x.org", "x-pass-1234", role=role)
    assert u.has_perm("staff.view_staff")
    assert not u.has_perm("staff.add_staff")

    def fresh():
        return User.objects.get(pk=u.pk)  # permission cache is per-instance

    UserPermissionOverride.objects.create(user=u, permission=_perm("staff.add_staff"), effect="grant")
    assert fresh().has_perm("staff.add_staff")
    UserPermissionOverride.objects.create(user=u, permission=_perm("staff.view_staff"), effect="revoke")
    assert not fresh().has_perm("staff.view_staff")  # revoke beats role baseline


def test_django_groups_are_ignored():
    from django.contrib.auth.models import Group
    g = Group.objects.create(name="g")
    g.permissions.add(_perm("staff.add_staff"))
    u = User.objects.create_user("g@x.org", "x-pass-1234")
    u.groups.add(g)
    assert not User.objects.get(pk=u.pk).has_perm("staff.add_staff")


def test_superuser_has_everything_and_role_changes_are_audited():
    from core.models import AuditLogEntry
    su = User.objects.create_superuser("su@x.org", "x-pass-1234")
    assert su.has_perm("staff.change_staff_state")
    role = Role.objects.create(code="a", name="A")
    role.permissions.add(_perm("staff.view_staff"))
    assert AuditLogEntry.objects.filter(model_label="accounts.Role", changes__has_key="permissions_added").exists() or \
        any("permissions_added" in e.changes for e in AuditLogEntry.objects.filter(model_label="accounts.Role"))


def test_seed_roles_idempotent_and_no_missing_permissions(capsys):
    call_command("seed_roles")
    call_command("seed_roles")
    out = capsys.readouterr()
    assert "missing permission" not in out.err
    assert Role.objects.count() == 10
    assert Role.objects.get(code="director").requires_2fa
