"""Promotion module (staff.promotions + views) — Phase 12."""
from datetime import date

import pytest
from django.urls import reverse

from notifications.models import Notification
from staff.models import Department, Designation, Promotion, Staff
from staff.promotions import PromotionError, apply_promotion, plan_promotion, rollback_promotion
from staff.testing import user_with_perms

pytestmark = pytest.mark.django_db


@pytest.fixture
def ladder():
    spo = Designation.objects.create(name="SNR. PROG. OFFR.", rank=57, contediss_min="9")
    po1 = Designation.objects.create(name="PROG. OFF. I", rank=40, contediss_min="8", next_designation=spo)
    po2 = Designation.objects.create(name="PROG. OFF. II", rank=35, contediss_min="7", next_designation=po1)
    norange = Designation.objects.create(name="SPECIAL POST", rank=60)
    return {"spo": spo, "po1": po1, "po2": po2, "norange": norange}


@pytest.fixture
def people(ladder):
    ict = Department.objects.create(name="ICT", code="ict")
    fin = Department.objects.create(name="FINANCE", code="finance")

    def mk(no, dpa, dept=ict):
        return Staff.objects.create(employee_no=no, first_name="F" + no, last_name="L" + no,
                                    department=dept, designation=ladder["po2"], grade_level="7",
                                    date_present_appointment=dpa)
    return {
        "a": mk("A", date(2020, 1, 1)),
        "b": mk("B", date(2022, 1, 1)),           # same dept, later  -> cascades
        "c": mk("C", date(2023, 1, 1), fin),      # other dept        -> no
        "d": mk("D", date(2019, 1, 1)),           # earlier           -> no
        "ict": ict,
    }


# Phase B v2.1 (D2): promotions are one step at a time, so the targets below
# are the next step (po2 -> po1); skipping a level now needs the ES override.
@pytest.fixture
def es():
    return user_with_perms("es@x.org", "staff.promote_staff", "staff.override_promotion_step",
                           "staff.rollback_promotion")


def test_promote_one(ladder, people):
    approver = user_with_perms("hr@x.org", "staff.promote_staff")
    plan = plan_promotion([people["a"]], ladder["po1"])
    res = apply_promotion(plan, effective_date=date(2026, 10, 1), reason="exercise", approver=approver)
    p = res.promotions[0]
    assert (p.from_designation, p.to_designation) == (ladder["po2"], ladder["po1"])
    assert (p.from_grade_level, p.to_grade_level) == ("7", "8")
    a = Staff.objects.get(pk=people["a"].pk)
    assert a.designation == ladder["po1"]
    assert a.grade_level == "8"                                 # auto-filled from contediss_min
    assert a.date_present_appointment == date(2026, 10, 1)


def test_grade_unchanged_without_range(ladder, people, es):
    plan = plan_promotion([people["a"]], ladder["norange"], actor=es, override_reason="Board decision")
    assert any("Grade level unchanged" in w for w in plan.warnings)
    apply_promotion(plan, effective_date=date(2026, 10, 1), reason="", approver=es)
    assert Staff.objects.get(pk=people["a"].pk).grade_level == "7"


def test_cascade_later_date_same_department_one_step(ladder, people):
    plan = plan_promotion([people["a"]], ladder["po1"], cascade=True, scope=Staff.objects.all())
    assert [c.staff.employee_no for c in plan.cascaded] == ["B"]
    assert plan.cascaded[0].to_designation == ladder["po1"]     # one step only
    res = apply_promotion(plan, effective_date=date(2026, 10, 1), reason="", approver=None)
    root = Promotion.objects.get(staff=people["a"])
    casc = Promotion.objects.get(staff=people["b"])
    assert casc.cascade_source == root and root.cascade_source is None
    assert Staff.objects.get(pk=people["b"].pk).designation == ladder["po1"]
    assert Staff.objects.get(pk=people["c"].pk).designation == ladder["po2"]
    assert Staff.objects.get(pk=people["d"].pk).designation == ladder["po2"]
    assert len(res.promotions) == 2


def test_rollback_restores_and_compensates(ladder, people, es):
    plan = plan_promotion([people["a"]], ladder["po1"])
    promo = apply_promotion(plan, effective_date=date(2026, 10, 1), reason="", approver=None).promotions[0]
    with pytest.raises(PromotionError):
        rollback_promotion(promo, reason="", approver=es)        # reason required
    rev = rollback_promotion(promo, reason="entered in error", approver=es)
    a = Staff.objects.get(pk=people["a"].pk)
    assert a.designation == ladder["po2"] and a.grade_level == "7"
    assert a.date_present_appointment == date(2020, 1, 1)
    assert rev.reverses == promo and rev.to_designation == ladder["po2"]
    assert Promotion.objects.filter(staff=a).count() == 2       # original kept
    with pytest.raises(PromotionError):
        rollback_promotion(promo, reason="again", approver=es)


def test_views_require_permission(client, ladder, people):
    nobody = user_with_perms("nobody@x.org", "staff.view_staff")
    client.force_login(nobody)
    assert client.get(reverse("staff:promotions")).status_code == 403
    resp = client.post(reverse("staff:promotion_apply"),
                       {"ids": [people["a"].pk], "target": ladder["spo"].pk, "effective_date": "2026-10-01"})
    assert resp.status_code == 403
    assert not Promotion.objects.exists()


def test_apply_view_with_notify(client, ladder, people):
    admin = user_with_perms("admin@x.org", superuser=True)
    linked = user_with_perms("a@x.org")
    Staff.all_objects.filter(user=linked).hard_delete()      # drop the auto-created profile
    Staff.objects.filter(pk=people["a"].pk).update(user=linked)
    client.force_login(admin)
    preview = client.post(reverse("staff:promotion_preview"),
                          {"ids": [people["a"].pk], "target": ladder["spo"].pk, "effective_date": "2026-10-01",
                           "cascade": "on"}, HTTP_X_REQUESTED_WITH="fetch")
    assert preview.status_code == 200 and b'data-preview-ok="1"' in preview.content
    resp = client.post(reverse("staff:promotion_apply"),
                       {"ids": [people["a"].pk], "target": ladder["spo"].pk, "effective_date": "2026-10-01",
                        "notify": "on", "reason": "2026 exercise"})
    assert resp.status_code == 302
    assert Promotion.objects.filter(staff=people["a"], approved_by=admin).exists()
    assert Notification.objects.filter(recipient=linked).exists()
    hist = client.get(reverse("staff:promotion_history"))
    assert hist.status_code == 200 and b"2026 exercise" in hist.content
    detail = client.get(reverse("staff:detail", args=[people["a"].pk]))
    assert b"Staff file" in detail.content and b"Promoted: PROG. OFF. II" in detail.content and b"promo-modal" in detail.content
