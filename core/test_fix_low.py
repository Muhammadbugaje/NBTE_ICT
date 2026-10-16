"""Regression tests for the low-severity review findings L1-L8."""
import logging
from pathlib import Path

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.models import Role, User
from staff.models import Department

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def roles():
    from django.core.management import call_command
    call_command("seed_roles", "--sync", verbosity=0)
    return {r.code: r for r in Role.objects.all()}


def mk(email, dept=None, role=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F",
                                 last_name="L", department=dept, role=role, **kw)
    if "must_change_password" not in kw:
        u.must_change_password = False
        u.save()
    return u


def _role_with(code, *perms):
    role = Role.objects.create(code=code, name=code)
    for label in perms:
        app, codename = label.split(".")
        role.permissions.add(Permission.objects.get(content_type__app_label=app, codename=codename))
    return role


# ----------------------------------------------------------------- L1
def test_chairman_dashboard_opens_with_its_permission(client):
    u = mk("chair@x.org", role=_role_with("chair-l1", "accounts.view_chairman_dashboard"))
    client.force_login(u)
    assert client.get(reverse("dashboards:chairman")).status_code == 200


def test_chairman_dashboard_still_closed_without_it(client):
    client.force_login(mk("nochair@x.org"))
    assert client.get(reverse("dashboards:chairman")).status_code == 403


def test_holiday_manager_can_delete_holiday(client):
    from datetime import date
    from portal.models import Holiday
    h = Holiday.objects.create(name="H", date_start=date(2026, 12, 25), date_end=date(2026, 12, 25))
    client.force_login(mk("hol@x.org", role=_role_with("hol-l1", "portal.manage_holiday")))
    resp = client.post(reverse("portal:holiday_delete", args=[h.pk]))
    assert resp.status_code == 302
    assert not Holiday.objects.filter(pk=h.pk).exists() or Holiday.objects.get(pk=h.pk).is_deleted


# ----------------------------------------------------------------- L2
def test_flagged_user_cannot_use_admin_before_changing_password(client):
    u = mk("adm@x.org", is_staff=True, must_change_password=True)
    client.force_login(u)
    resp = client.get("/admin/")
    assert resp.status_code == 302
    assert resp["Location"].endswith(reverse("accounts:force_password_change"))


def test_admin_login_page_still_reachable(client):
    u = mk("adm2@x.org", is_staff=True, must_change_password=True)
    client.force_login(u)
    resp = client.get("/admin/login/")
    # Not bounced to the password-change page (the admin may redirect a
    # signed-in user onwards itself, which is fine).
    assert "change-password" not in (resp.get("Location") or "")


# ----------------------------------------------------------------- L3
@pytest.fixture
def chain_setup(roles):
    from workflow.models import ApprovalChain, ApprovalStep, RequestCategory, RequestType
    ict = Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]
    cat = RequestCategory.objects.create(name="CL3", code="cl3")
    chain = ApprovalChain.objects.create(name="chl3", code="chl3")
    rt = RequestType.objects.create(name="TL3", code="tl3", category=cat,
                                    handling_department=ict, approval_chain=chain)
    return ict, chain, rt


def _step(chain, pos, kind, approver_kind, **kw):
    from workflow.models import ApprovalStep
    return ApprovalStep.objects.create(chain=chain, position=pos, name=f"S{pos}", kind=kind,
                                       approver_kind=approver_kind, **kw)


def test_approving_an_assignment_step_is_refused(roles, chain_setup):
    from workflow import services
    from workflow.models import ApprovalStep, RequestApproval
    ict, chain, rt = chain_setup
    director = mk("dir-l3@x.org", ict, roles["director"])
    _step(chain, 1, ApprovalStep.KIND_ASSIGNMENT, ApprovalStep.APPROVER_RECEIVING_DIRECTOR)
    req = services.raise_request(requester=mk("rq-l3@x.org", ict, roles["staff"]),
                                 request_type=rt, subject="s", body="b")
    with pytest.raises(ValidationError):
        services.approve_step(req, director)
    assert not RequestApproval.objects.filter(request=req).exists()


def test_return_does_not_bounce_back_to_same_person(roles, chain_setup):
    from workflow import services
    from workflow.models import ApprovalStep, Request
    ict, chain, rt = chain_setup
    requester = mk("rq2-l3@x.org", ict, roles["director"])  # step 1 resolves to them -> skipped
    _step(chain, 1, ApprovalStep.KIND_APPROVAL, ApprovalStep.APPROVER_REQUESTER_DIRECTOR)
    es = mk("es-l3@x.org", Department.objects.create(name="ES", code="es-l3"),
            roles["executive-secretary"])
    _step(chain, 2, ApprovalStep.KIND_APPROVAL, ApprovalStep.APPROVER_ES)
    req = services.raise_request(requester=requester, request_type=rt, subject="s", body="b")
    assert services.resolve_approver(services._resolve_current_step(req), req) == es

    services.return_step(req, es, comment="please fix")
    req.refresh_from_db()
    assert req.status == Request.RETURNED  # nobody acted before -> requester


def test_return_goes_to_previous_actor(roles, chain_setup):
    from workflow import services
    from workflow.models import ApprovalStep
    ict, chain, rt = chain_setup
    requester = mk("rq3-l3@x.org", ict, roles["staff"])
    director = mk("dir3-l3@x.org", ict, roles["director"])
    es = mk("es3-l3@x.org", Department.objects.create(name="ES3", code="es3-l3"),
            roles["executive-secretary"])
    s1 = _step(chain, 1, ApprovalStep.KIND_APPROVAL, ApprovalStep.APPROVER_REQUESTER_DIRECTOR,
               is_hierarchical=False)
    _step(chain, 2, ApprovalStep.KIND_APPROVAL, ApprovalStep.APPROVER_ES)
    req = services.raise_request(requester=requester, request_type=rt, subject="s", body="b")
    services.approve_step(req, director)
    services.return_step(req, es, comment="back to director")
    req.refresh_from_db()
    assert services._resolve_current_step(req).pk == s1.pk


def test_resubmit_status_follows_the_step(roles, chain_setup):
    from workflow import services
    from workflow.models import ApprovalStep, Request
    ict, chain, rt = chain_setup
    requester = mk("rq4-l3@x.org", ict, roles["staff"])
    fulfil = _step(chain, 1, ApprovalStep.KIND_FULFILLMENT, ApprovalStep.APPROVER_RECEIVING_DIRECTOR)
    req = Request.objects.create(reference="REQ-L3-4", request_type=rt, requester=requester,
                                 subject="s", body="b", receiving_department=ict,
                                 status=Request.RETURNED,
                                 metadata={"returned_from_step_key": f"db:{fulfil.pk}"})
    services.resubmit(req, requester)
    req.refresh_from_db()
    assert req.status == Request.IN_PROGRESS


# ----------------------------------------------------------------- L4 / L5
def test_import_error_message_is_escaped(client, monkeypatch):
    from core import importexport_views as ie

    class Boom:
        @staticmethod
        def run_from_file(fh, dry_run):
            raise ValueError("<script>alert(1)</script>")

    monkeypatch.setattr(ie, "_spec_or_404", lambda kind, name: Boom)
    client.force_login(mk("su-l4@x.org", is_superuser=True, is_staff=True))
    resp = client.post(reverse("core:ie_preview", args=["staff"]),
                       {"file": SimpleUploadedFile("x.xlsx", b"not really xlsx")})
    body = resp.content.decode()
    assert "<script>" not in body and "&lt;script&gt;" in body


def test_import_scratch_files_are_not_in_public_media(settings):
    from core import importexport_views as ie
    token, rel = ie._save_upload(SimpleUploadedFile("x.xlsx", b"data"))
    assert (Path(settings.PROTECTED_MEDIA_ROOT) / rel).exists()
    assert not (Path(settings.MEDIA_ROOT) / rel).exists()
    ie._tmp_storage().delete(rel)


def test_cleanup_sweeps_protected_import_tmp(settings, tmp_path):
    import os
    import time
    from core.tasks import cleanup_import_tmp
    settings.PROTECTED_MEDIA_ROOT = tmp_path / "prot"
    settings.MEDIA_ROOT = tmp_path / "pub"
    for root in (settings.PROTECTED_MEDIA_ROOT, settings.MEDIA_ROOT):
        d = Path(root) / "import-tmp"
        d.mkdir(parents=True)
        old = d / "old.xlsx"
        old.write_bytes(b"x")
        past = time.time() - 48 * 3600
        os.utime(old, (past, past))
    assert cleanup_import_tmp()["removed"] == 2


# ----------------------------------------------------------------- L6
def test_dead_code_removed():
    import core.views
    assert not hasattr(core.views, "user_scope_tickets")
    assert not hasattr(core.views, "_count_or_none")
    assert not (ROOT / "templates" / "workflow" / "awaiting_action.html").exists()
    assert not (ROOT / "recover_media_move.py").exists()


# ----------------------------------------------------------------- L7
def test_routine_file_cleanup_logs_are_not_warnings(caplog, settings, tmp_path):
    from core.files import _delete_safely
    from django.core.files.storage import FileSystemStorage
    storage = FileSystemStorage(location=str(tmp_path))
    storage.save("a.txt", SimpleUploadedFile("a.txt", b"x"))
    with caplog.at_level(logging.INFO, logger="core.files"):
        _delete_safely(storage, "a.txt", reason="test")
        _delete_safely(storage, "a.txt", reason="test")
    assert caplog.records and all(r.levelno < logging.WARNING for r in caplog.records)


# ----------------------------------------------------------------- L8
def test_no_cdn_scripts_and_chart_loaded_once():
    templates = ROOT / "templates"
    for path in templates.rglob("*.html"):
        assert "cdn.jsdelivr.net" not in path.read_text(encoding="utf-8"), path
    assert "chart" not in (templates / "base.html").read_text(encoding="utf-8").lower().split(
        "{% block extra_js %}")[0].split("htmx.min.js")[1]
    for name in ("es", "superadmin", "director", "chairman", "deputy"):
        html = (templates / "dashboards" / f"{name}.html").read_text(encoding="utf-8")
        assert html.count("vendor/chart-4.4.1.umd.js") == 1
    assert (ROOT / "static" / "vendor" / "chart-4.4.1.umd.js").exists()
    assert (ROOT / "static" / "vendor" / "html5-qrcode-2.3.8.min.js").exists()
