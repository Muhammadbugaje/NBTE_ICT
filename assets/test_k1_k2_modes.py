"""v2.1 K1/K2: department and shared asset assignment."""
import pytest
from django.core.exceptions import ValidationError

from tickets.conftest import org_c  # noqa: F401
from tasks.conftest import depts, people, roles  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def kit(org_c):
    from assets import services as asvc
    from assets.models import AssetCategory
    acat = AssetCategory.objects.create(name="Kitchen (t)", code="kitchen-t")
    a = asvc.create_asset(category=acat, owner_department=org_c["ict"], asset_tag="T-KTL-1", state="in_service")
    return {**org_c, "a": a}


def test_k1_department_assignment(kit):
    from assets import services as asvc
    from assets.access import can_view_asset
    a, d = kit["a"], kit["director"]
    asvc.set_assignment_mode(asset=a, mode="department", by_user=d)
    with pytest.raises(ValidationError):
        asvc.assign_asset(asset=a, by_user=d)                    # needs a department
    row = asvc.assign_asset(asset=a, department=kit["ict"], by_user=d)
    a.refresh_from_db()
    assert row.assigned_to is None and row.department == kit["ict"] and a.current_custodian is None
    assert can_view_asset(kit["officer"], a)                     # every member sees it
    assert not can_view_asset(kit["hr_officer"], a)
    with pytest.raises(ValidationError):
        asvc.assign_asset(asset=a, department=kit["ict"], by_user=d)
    with pytest.raises(ValidationError):
        asvc.set_assignment_mode(asset=a, mode="shared", by_user=d)   # held: cannot switch


def test_k2_shared_multiple_holders(kit):
    from assets import services as asvc
    from assets.access import can_view_asset
    a, d = kit["a"], kit["director"]
    asvc.set_assignment_mode(asset=a, mode="shared", by_user=d)
    r1 = asvc.assign_asset(asset=a, to_user=kit["officer"], by_user=d)
    asvc.assign_asset(asset=a, to_user=kit["officer2"], by_user=d)
    with pytest.raises(ValidationError):
        asvc.assign_asset(asset=a, to_user=kit["officer"], by_user=d)
    assert a.open_assignments().count() == 2
    asvc.return_asset(asset=a, by_user=d, assignment=r1)        # one share back
    assert a.open_assignments().count() == 1
    assert can_view_asset(kit["officer2"], a)
    a.refresh_from_db()
    assert a.current_custodian is None


def test_k_individual_mode_unchanged(kit):
    from assets import services as asvc
    a, d = kit["a"], kit["director"]
    asvc.assign_asset(asset=a, to_user=kit["officer"], by_user=d)
    a.refresh_from_db()
    assert a.current_custodian == kit["officer"]
    with pytest.raises(ValidationError):
        asvc.assign_asset(asset=a, to_user=kit["officer2"], by_user=d)


def test_k2_detail_page_shared(kit, client):
    from assets import services as asvc
    a, d = kit["a"], kit["director"]
    asvc.set_assignment_mode(asset=a, mode="shared", by_user=d)
    asvc.assign_asset(asset=a, to_user=kit["officer"], by_user=d)
    client.force_login(d)
    html = client.get(f"/assets/{a.asset_tag}/").content.decode()
    assert 'data-asset-mode="shared"' in html and "Return share" in html and "Add a person" in html
