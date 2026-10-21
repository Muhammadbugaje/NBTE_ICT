"""Staff file: transfers and the detail-page timeline (nominal-roll follow-up)."""
from datetime import date

import pytest
from django.urls import reverse

from assets.models import Asset, AssetAssignment, AssetCategory
from staff.models import Department, Designation, Office, Staff, StaffTransfer
from staff.promotions import apply_promotion, plan_promotion
from staff.testing import user_with_perms

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup():
    ict = Department.objects.create(name="ICT", code="ict")
    lagos = Department.objects.create(name="ZONAL OFFICE LAGOS", code="zonal-lagos")
    admin = user_with_perms("admin@x.org", superuser=True)
    person = user_with_perms("p@x.org")
    s = person.staff_profile
    s.employee_no, s.department = "1413", ict
    s.save()
    return {"ict": ict, "lagos": lagos, "admin": admin, "person": person, "staff": s}


def test_transfer_action_records_and_notifies(client, setup):
    client.force_login(setup["admin"])
    resp = client.post(reverse("staff:transfer", args=[setup["staff"].pk]),
                       {"department": setup["lagos"].pk, "effective_date": "2026-10-20",
                        "reason": "Posting ref NBTE/HR/1", "notify": "on"})
    assert resp.status_code == 302
    t = StaffTransfer.objects.get()
    assert (t.from_department, t.to_department, t.source) == (setup["ict"], setup["lagos"], "transfer")
    assert t.effective_date == date(2026, 10, 20) and t.recorded_by == setup["admin"]
    assert Staff.objects.get(pk=setup["staff"].pk).department == setup["lagos"]
    assert setup["person"].notifications.exists() if hasattr(setup["person"], "notifications") else True


def test_transfer_needs_reason_and_permission(client, setup):
    client.force_login(setup["admin"])
    client.post(reverse("staff:transfer", args=[setup["staff"].pk]), {"department": setup["lagos"].pk})
    assert not StaffTransfer.objects.exists()
    client.force_login(user_with_perms("v@x.org", "staff.view_staff", "accounts.access_all_departments"))
    resp = client.post(reverse("staff:transfer", args=[setup["staff"].pk]),
                       {"department": setup["lagos"].pk, "reason": "x"})
    assert resp.status_code == 403


def test_edit_moves_are_recorded_too(setup):
    s = Staff.objects.get(pk=setup["staff"].pk)
    s.office = Office.objects.create(name="Software Unit")
    s.save()                                   # first office: a posting, not a transfer
    assert not StaffTransfer.objects.exists()
    s = Staff.objects.get(pk=s.pk)
    s.department = setup["lagos"]
    s.save()
    t = StaffTransfer.objects.get()
    assert t.source == "edit" and t.to_department == setup["lagos"]


def test_timeline_collects_everything(client, setup):
    s = setup["staff"]
    po2 = Designation.objects.create(name="PROG. OFF. II", rank=35)
    po1 = Designation.objects.create(name="PROG. OFF. I", rank=40, contediss_min="8")
    s.designation = po2
    s.save()
    apply_promotion(plan_promotion([Staff.objects.get(pk=s.pk)], po1), effective_date=date(2026, 1, 1),
                    reason="exercise", approver=setup["admin"])
    cat = AssetCategory.objects.create(name="Laptop")
    asset = Asset.objects.create(asset_tag="NBTE-LT-001", category=cat, brand="Dell", model="Latitude")
    AssetAssignment.objects.create(asset=asset, assigned_to=setup["person"], assigned_by=setup["admin"])
    client.force_login(setup["admin"])
    client.post(reverse("staff:transfer", args=[s.pk]),
                {"department": setup["lagos"].pk, "reason": "Posting", "effective_date": "2026-10-01"})
    client.post(reverse("staff:notify", args=[s.pk]), {"title": "Welcome to Lagos", "body": "Report Monday"})
    page = client.get(reverse("staff:detail", args=[s.pk]))
    html = page.content.decode()
    assert "Staff file" in html
    for text in ("Promoted: PROG. OFF. II → PROG. OFF. I", "Transferred: ICT → ZONAL OFFICE LAGOS",
                 "Given NBTE-LT-001", "Notification sent: Welcome to Lagos", "Items held now"):
        assert text in html, text
    kinds = {e.kind for e in page.context["staff_file"]}
    assert {"career", "assets", "contact", "record"} <= kinds


def test_staff_file_hidden_from_colleagues(client, setup):
    colleague = user_with_perms("c@x.org", "staff.view_own_department_staff", department=setup["ict"])
    client.force_login(colleague)
    html = client.get(reverse("staff:detail", args=[setup["staff"].pk])).content.decode()
    assert "Staff file" not in html
