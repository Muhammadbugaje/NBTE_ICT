"""Phase B v2.1 — B.10 user management and Act as user (C13-C18, T-PERF-4)."""
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from accounts.models import Role, User
from config.models import ConfigEntry
from staff.models import Department
from staff.testing import user_with_perms

pytestmark = pytest.mark.django_db


@pytest.fixture
def crowd():
    ict = Department.objects.create(name="ICT unit", code="ict-u")
    fin = Department.objects.create(name="Finance unit", code="fin-u")
    officer = Role.objects.create(code="c-officer", name="C Officer", rank=20)
    head = Role.objects.create(code="c-head", name="C Head", rank=50)
    for i in range(30):
        User.objects.create_user(f"u{i:02d}@x.org", "pw-12345-x", first_name=f"Person{i:02d}", last_name="Test",
                                 department=ict if i % 2 else fin, role=officer if i % 3 else head)
    return {"ict": ict, "fin": fin, "officer": officer, "head": head}


@pytest.fixture
def admin():
    return user_with_perms("boss@x.org", superuser=True)


def test_c13_show_all_toggle(client, admin, crowd):
    client.force_login(admin)
    first = client.get(reverse("accounts:user_list")).content.decode()
    assert first.count('class="hr-cell-primary"') == 25
    assert "Show all" in first
    every = client.get(reverse("accounts:user_list") + "?all=1").content.decode()
    assert every.count('class="hr-cell-primary"') == User.objects.count()
    ConfigEntry.objects.update_or_create(key="qa.ui.max_show_all_rows", scope="global",
                                         defaults={"value": "25", "value_type": "int"})
    capped = client.get(reverse("accounts:user_list") + "?all=1").content.decode()
    assert capped.count('class="hr-cell-primary"') == 25 and "limited to 25" in capped


def test_c14_filters_combine(client, admin, crowd):
    client.force_login(admin)
    url = reverse("accounts:user_list") + f"?department={crowd['ict'].pk}&role={crowd['head'].pk}&all=1"
    body = client.get(url).content.decode()
    expected = User.objects.filter(department=crowd["ict"], role=crowd["head"]).count()
    assert body.count('class="hr-cell-primary"') == expected > 0
    body = client.get(reverse("accounts:user_list") + "?q=Person07&active=1").content.decode()
    assert body.count('class="hr-cell-primary"') == 1
    hx = client.get(url, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="users-list").content.decode()
    assert hx.lstrip().startswith('<div id="users-list"') and "<html" not in hx


def test_c15_manage_column_sticky_and_scrollable(client, admin, crowd):
    client.force_login(admin)
    body = client.get(reverse("accounts:user_list")).content.decode()
    assert 'class="hr-table-wrap"' in body and 'class="hr-sticky"' in body
    css = (Path(settings.BASE_DIR) / "static/css/hr.css").read_text()
    assert ".hr-sticky" in css and "position: sticky" in css and "overflow-x: auto" in css
    users_css = (Path(settings.BASE_DIR) / "static/css/users.css").read_text()
    wrap = users_css.split(".users-table-wrap {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" not in wrap


def test_c16_impersonate_filtered_and_paginated(client, admin, crowd):
    client.force_login(admin)
    page1 = client.get(reverse("core:impersonate_pick")).content.decode()
    assert page1.count("Act as</button>") == 25                      # paginated (T-PERF-4)
    url = reverse("core:impersonate_pick") + f"?q=Person1&department={crowd['ict'].pk}"
    body = client.get(url).content.decode()
    expected = User.objects.filter(first_name__startswith="Person1", department=crowd["ict"]).count()
    assert body.count("Act as</button>") == expected > 0
    by_role = client.get(reverse("core:impersonate_pick") + f"?role={crowd['head'].pk}&all=1").content.decode()
    assert by_role.count("Act as</button>") == User.objects.filter(role=crowd["head"], is_active=True).count()
    nobody = user_with_perms("plain@x.org", "accounts.view_user")
    client.force_login(nobody)
    assert client.get(reverse("core:impersonate_pick")).status_code == 403


def test_c18_toggle_active_htmx(client, admin, crowd):
    target = User.objects.get(email="u01@x.org")
    client.force_login(admin)
    resp = client.post(reverse("accounts:user_toggle_active", args=[target.pk]), HTTP_HX_REQUEST="true")
    assert resp.status_code == 200 and 'id="user-status"' in resp.content.decode()
    assert "qa:toast" in resp["HX-Trigger"]
    target.refresh_from_db()
    assert not target.is_active


def test_c18_edit_role_htmx_through_role_assign(client, crowd):
    hr = user_with_perms("hr@x.org", "accounts.change_user", "accounts.view_user", "accounts.view_all_users")
    Role.objects.filter(pk=hr.role_id).update(rank=60)
    target = User.objects.get(email="u01@x.org")
    client.force_login(hr)
    resp = client.post(reverse("accounts:user_edit_roles", args=[target.pk]), {"role": crowd["head"].pk},
                       HTTP_HX_REQUEST="true")
    assert resp.status_code == 200 and "Role set to C Head" in resp["HX-Trigger"]
    target.refresh_from_db()
    assert target.role == crowd["head"]
