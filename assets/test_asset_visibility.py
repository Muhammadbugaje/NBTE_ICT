"""Asset visibility, labels and barcode (Issues 1 and 3).

Run:  pytest assets/test_asset_visibility.py -q
"""
import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from staff.models import Department

pytestmark = pytest.mark.django_db


@pytest.fixture
def ict():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def fin():
    return Department.objects.get_or_create(code="fin", defaults={"name": "Finance"})[0]


@pytest.fixture
def roles():
    from django.core.management import call_command
    call_command("seed_roles", "--sync", verbosity=0)
    return {r.code: r for r in Role.objects.all()}


def mk(email, dept=None, role=None, **kw):
    u = User.objects.create_user(email=email, password="Xx-123456789", first_name="F",
                                 last_name="L", department=dept, role=role, **kw)
    u.must_change_password = False
    u.save()
    return u


def drop_perm(role, codename):
    role.permissions.remove(Permission.objects.get(codename=codename))

from assets.access import can_view_asset, visible_assets_queryset
from assets.models import Asset, AssetCategory


@pytest.fixture
def cat():
    return AssetCategory.objects.create(name="LapX", code="lapx")


@pytest.fixture
def world(ict, fin, roles, cat):
    """staff1 holds A1; A2 is another ICT asset; F1 belongs to Finance."""
    staff1 = mk("s1@x.org", ict, roles["staff"])
    a1 = Asset.objects.create(asset_tag="ICT-LAP-00001", category=cat, owner_department=ict,
                              state="in_service", current_custodian=staff1)
    a2 = Asset.objects.create(asset_tag="ICT-LAP-00002", category=cat, owner_department=ict,
                              state="in_service")
    f1 = Asset.objects.create(asset_tag="FIN-LAP-00001", category=cat, owner_department=fin,
                              state="in_service")
    return {"staff1": staff1, "a1": a1, "a2": a2, "f1": f1}


def tags(user):
    return set(visible_assets_queryset(user).values_list("asset_tag", flat=True))


def test_staff_sees_only_own_assets(world):
    assert tags(world["staff1"]) == {"ICT-LAP-00001"}


def test_director_sees_own_department_not_others(world, ict, roles):
    d = mk("d@x.org", ict, roles["director"])
    assert tags(d) == {"ICT-LAP-00001", "ICT-LAP-00002"}
    assert "FIN-LAP-00001" not in tags(d)


def test_es_with_permission_sees_all_without_does_not(world, ict, roles):
    es = mk("es@x.org", ict, roles["executive-secretary"])
    assert tags(es) == {"ICT-LAP-00001", "ICT-LAP-00002", "FIN-LAP-00001"}
    drop_perm(roles["executive-secretary"], "view_all_assets")
    es2 = User.objects.get(pk=es.pk)
    assert tags(es2) == set()          # holds nothing personally, no all-permission


def test_super_admin_sees_all(world, ict):
    su = User.objects.create_superuser(email="su@x.org", password="Xx-123456789")
    assert len(tags(su)) == 3


def test_explicit_grant_gives_view_all(world, ict, roles):
    u = mk("hr@x.org", ict, roles["staff"])
    u.user_permissions.add(Permission.objects.get(codename="view_all_assets"))
    assert len(tags(User.objects.get(pk=u.pk))) == 3


def test_url_guessing_detail_is_404(client, world):
    client.force_login(world["staff1"])
    assert client.get(reverse("assets:detail", args=["ICT-LAP-00001"])).status_code == 200
    assert client.get(reverse("assets:detail", args=["ICT-LAP-00002"])).status_code == 404
    assert client.get(reverse("assets:detail", args=["FIN-LAP-00001"])).status_code == 404


def test_list_only_shows_visible(client, world):
    client.force_login(world["staff1"])
    html = client.get(reverse("assets:list")).content.decode()
    assert "ICT-LAP-00001" in html
    assert "ICT-LAP-00002" not in html and "FIN-LAP-00001" not in html


def test_can_view_asset_object_level(world):
    assert can_view_asset(world["staff1"], world["a1"])
    assert not can_view_asset(world["staff1"], world["f1"])


def test_see_does_not_mean_edit(client, world):
    # staff1 can see a1 but holds no change_asset permission.
    client.force_login(world["staff1"])
    r = client.post(reverse("assets:set_state", args=["ICT-LAP-00001"]),
                    {"state": "retired", "reason": "x"})
    assert r.status_code in (403, 404)
    world["a1"].refresh_from_db()
    assert world["a1"].state == "in_service"


# ---------------------------- labels ----------------------------
def test_label_refuses_asset_user_may_not_see(client, world):
    client.force_login(world["staff1"])
    url = reverse("assets:print_labels")
    assert client.get(url, {"tags": "FIN-LAP-00001"}).status_code == 404


def test_label_mixed_batch_prints_only_visible(client, world):
    client.force_login(world["staff1"])
    r = client.get(reverse("assets:print_labels"),
                   {"tags": "ICT-LAP-00001,FIN-LAP-00001", "style": "small"})
    html = r.content.decode()
    assert r.status_code == 200
    assert "ICT-LAP-00001" in html and "FIN-LAP-00001" not in html
    assert "skipped" in html


@pytest.mark.parametrize("style,css", [
    ("small", "lbl-small"), ("standard", "lbl-standard"), ("large", "lbl-large"),
    ("barcode", "lbl-barcode"), ("sheet", "lbl-cell"),
])
def test_every_label_style_renders(client, world, style, css):
    client.force_login(world["staff1"])
    r = client.get(reverse("assets:print_labels"), {"tags": "ICT-LAP-00001", "style": style})
    html = r.content.decode()
    assert r.status_code == 200 and css in html and "ICT-LAP-00001" in html


def test_copies_and_unknown_style_fall_back(client, world):
    client.force_login(world["staff1"])
    r = client.get(reverse("assets:print_labels"),
                   {"tags": "ICT-LAP-00001", "style": "nonsense", "copies": "3"})
    html = r.content.decode()
    assert r.status_code == 200
    assert html.count('class="lbl-page"') == 3             # default style, 3 copies
    assert "lbl-standard" in html


def test_sheet_paginates_24_per_page(client, world):
    client.force_login(world["staff1"])
    r = client.get(reverse("assets:print_labels"),
                   {"tags": "ICT-LAP-00001", "style": "sheet", "copies": "30"})
    assert r.content.decode().count('class="lbl-sheet"') == 2


# ---------------------------- barcode ----------------------------
def test_barcode_decodes_back_with_valid_checksum():
    from assets.barcode import PATTERNS, STOP, code128_modules
    text = "ICT-COMPUT-00001"
    bits = code128_modules(text)
    assert bits.endswith(STOP + "11")
    body = bits[:-(len(STOP) + 2)]
    inverse = {p: i for i, p in enumerate(PATTERNS)}
    values = [inverse[body[i:i + 11]] for i in range(0, len(body), 11)]
    assert values[0] == 104                                   # start B
    checksum = (104 + sum(i * v for i, v in enumerate(values[1:-1], start=1))) % 103
    assert values[-1] == checksum
    assert "".join(chr(v + 32) for v in values[1:-1]) == text


def test_registry_has_five_styles_and_templates_exist():
    from django.template.loader import get_template
    from assets.label_styles import LABEL_STYLES
    assert len(LABEL_STYLES) >= 5
    for s in LABEL_STYLES:
        get_template(s.template)
