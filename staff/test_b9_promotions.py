"""Phase B v2.1 — B.9 promotion engine (D2-D12), Master Plan §7.7."""
import io
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from config.models import ConfigEntry
from notifications.models import Notification
from staff.models import Department, Designation, Promotion, Staff
from staff.promotions import (
    PromotionError, apply_promotion, eligibility, eligible_staff, idfilter, levels, overdue_staff,
    plan_promotion, rollback_promotion, send_overdue_notice,
)
from staff.testing import user_with_perms

pytestmark = pytest.mark.django_db


def years_ago(n, extra_days=0):
    today = timezone.localdate()
    return today - timedelta(days=int(n * 365.25) + extra_days)


@pytest.fixture
def ladder():
    spo = Designation.objects.create(name="SNR. PROG. OFFR.", rank=57, contediss_min="9")
    po1 = Designation.objects.create(name="PROG. OFF. I", rank=40, contediss_min="8", next_designation=spo)
    po2 = Designation.objects.create(name="PROG. OFF. II", rank=35, contediss_min="7", next_designation=po1)
    return {"spo": spo, "po1": po1, "po2": po2}


@pytest.fixture
def depts():
    return Department.objects.create(name="ICT", code="ict"), Department.objects.create(name="FIN", code="fin")


def mk(no, desig, dpa, dept, grade=None):
    return Staff.objects.create(employee_no=no, first_name="F" + no, last_name="L" + no, department=dept,
                                designation=desig, grade_level=grade if grade is not None else desig.contediss_min,
                                date_present_appointment=dpa)


@pytest.fixture
def hr():
    return user_with_perms("hr@x.org", "staff.promote_staff", "staff.view_staff",
                           "accounts.access_all_departments")


@pytest.fixture
def es():
    return user_with_perms("es@x.org", "staff.promote_staff", "staff.view_staff", "accounts.access_all_departments",
                           "staff.override_promotion_step", "staff.rollback_promotion")


def test_d2_next_step_only(ladder, depts, hr, es):
    s = mk("A", ladder["po2"], years_ago(4), depts[0])
    assert levels.next_level(s).designation == ladder["po1"]
    # Skipping a level is refused for HR …
    plan = plan_promotion([s], ladder["spo"], actor=hr)
    assert not plan.changes and "next step" in plan.skipped[0][1]
    # … refused for the ES without a reason …
    plan = plan_promotion([s], ladder["spo"], actor=es)
    assert not plan.changes and "reason" in plan.skipped[0][1]
    # … allowed for the ES with a reason, and recorded as an override.
    plan = plan_promotion([s], ladder["spo"], actor=es, override_reason="Board decision 12/3")
    assert plan.changes and plan.changes[0].override
    with pytest.raises(PromotionError):                      # HR cannot apply an ES plan
        apply_promotion(plan, effective_date=date(2026, 10, 1), reason="", approver=hr)
    p = apply_promotion(plan, effective_date=date(2026, 10, 1), reason="x", approver=es).promotions[0]
    assert "ES override: Board decision" in p.reason
    # No demotion, ever: a lower target is skipped even for the ES.
    plan = plan_promotion([Staff.objects.get(pk=s.pk)], ladder["po1"], actor=es, override_reason="r")
    assert not plan.changes and "no demotion" in plan.skipped[0][1]


def test_d2_bulk_default_is_each_persons_next_step(ladder, depts):
    a = mk("A", ladder["po2"], years_ago(4), depts[0])
    b = mk("B", ladder["po1"], years_ago(4), depts[1])
    top = mk("C", ladder["spo"], years_ago(4), depts[1])
    plan = plan_promotion([a, b, top])
    got = {c.staff.employee_no: c.to_designation for c in plan.changes}
    assert got == {"A": ladder["po1"], "B": ladder["spo"]}
    assert [s.employee_no for s, _ in plan.skipped] == ["C"]


def test_d2_grade_never_goes_down(ladder, depts):
    s = mk("A", ladder["po2"], years_ago(4), depts[0], grade="9")   # already GL 9
    step = levels.next_level(s)
    assert step.designation == ladder["po1"] and step.grade == "9"


def test_d4_eligibility_by_years(ladder, depts):
    ok = mk("A", ladder["po2"], years_ago(3, 2), depts[0])
    young = mk("B", ladder["po2"], years_ago(2), depts[0])
    top = mk("C", ladder["spo"], years_ago(9), depts[0])
    nodate = mk("D", ladder["po2"], None, depts[0])
    assert eligibility(ok).eligible and not eligibility(young).eligible
    assert eligibility(young).due_on is not None
    assert not eligibility(top).eligible and not eligibility(nodate).eligible
    assert set(eligible_staff(Staff.objects.all())) == {ok}
    # The threshold is a setting.
    ConfigEntry.objects.update_or_create(key="hr.promotion.min_years_in_grade", scope="global",
                                         defaults={"value": "1", "value_type": "int"})
    assert set(eligible_staff(Staff.objects.all())) == {ok, young}


def test_d10_overdue_list(ladder, depts):
    overdue = mk("A", ladder["po2"], years_ago(3, 120), depts[0])   # eligible ~120 days ago
    recent = mk("B", ladder["po2"], years_ago(3, 10), depts[0])     # eligible 10 days ago
    assert set(overdue_staff(Staff.objects.all())) == {overdue}
    assert eligibility(overdue).overdue and not eligibility(recent).overdue


def test_d10_weekly_notice_reaches_hr_and_es(ladder, depts, hr, es):
    mk("A", ladder["po2"], years_ago(3, 200), depts[0])
    assert send_overdue_notice() >= 2
    assert Notification.objects.filter(recipient=hr, title__contains="overdue").exists()
    assert Notification.objects.filter(recipient=es, title__contains="overdue").exists()
    from nbte_ict.celery import app
    assert any(e["task"] == "staff.tasks.promotion_overdue_notice" for e in app.conf.beat_schedule.values())


def _xlsx(rows):
    import openpyxl
    wb = openpyxl.Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    buf.name = "ids.xlsx"
    return buf


def test_d6_excel_filter_matches(client, ladder, depts, hr):
    a = mk("NBTE-1", ladder["po2"], years_ago(4), depts[0])
    mk("NBTE-2", ladder["po2"], years_ago(4), depts[0])
    assert idfilter.read_ids(_xlsx([["Name", "Employee ID"], ["x", "nbte-1"], ["y", "GHOST-9"]])) == ["NBTE-1", "GHOST-9"]
    client.force_login(hr)
    resp = client.post(reverse("staff:promotion_id_filter"),
                       {"file": _xlsx([["Staff No"], ["NBTE-1"], ["GHOST-9"]]), "tab": "all"})
    assert resp.status_code == 302
    page = client.get(reverse("staff:promotions") + "?tab=all")
    body = page.content.decode()
    assert "NBTE-1" in body and "NBTE-2" not in body
    assert "GHOST-9" in body and "1 not found" in body
    client.post(reverse("staff:promotion_id_filter"), {"clear": "1"})
    assert "NBTE-2" in client.get(reverse("staff:promotions") + "?tab=all").content.decode()
    assert a


def test_d5_tabs_and_htmx_partial(client, ladder, depts, hr):
    mk("ELIG", ladder["po2"], years_ago(4), depts[0])
    mk("YOUNG", ladder["po2"], years_ago(1), depts[0])
    client.force_login(hr)
    eligible = client.get(reverse("staff:promotions")).content.decode()
    assert "ELIG" in eligible and "YOUNG" not in eligible
    allv = client.get(reverse("staff:promotions") + "?tab=all").content.decode()
    assert "YOUNG" in allv
    part = client.get(reverse("staff:promotions") + "?tab=all", HTTP_HX_REQUEST="true", HTTP_HX_TARGET="promo-list")
    assert part.content.decode().lstrip().startswith('<div id="promo-list"')
    assert "<html" not in part.content.decode()
    assert client.get(reverse("staff:promotions") + "?tab=history").status_code == 302


def test_d7_bulk_apply(client, ladder, depts, hr):
    a = mk("A", ladder["po2"], years_ago(4), depts[0])
    b = mk("B", ladder["po1"], years_ago(4), depts[1])
    client.force_login(hr)
    data = {"ids": [a.pk, b.pk], "mode": "next", "effective_date": "2026-10-01", "reason": "2026 exercise"}
    prev = client.post(reverse("staff:promotion_preview"), data, HTTP_X_REQUESTED_WITH="fetch")
    assert b'data-preview-ok="1"' in prev.content and b"SNR. PROG. OFFR." in prev.content
    assert client.post(reverse("staff:promotion_apply"), data).status_code == 302
    assert Staff.objects.get(pk=a.pk).designation == ladder["po1"]
    assert Staff.objects.get(pk=b.pk).designation == ladder["spo"]
    assert Promotion.objects.filter(staff__in=[a, b]).count() == 2       # one history entry each


def test_d7_hr_cannot_force_a_target(client, ladder, depts, hr):
    a = mk("A", ladder["po2"], years_ago(4), depts[0])
    client.force_login(hr)
    client.post(reverse("staff:promotion_apply"), {"ids": [a.pk], "mode": "target", "target": ladder["spo"].pk,
                                                   "override_reason": "x", "effective_date": "2026-10-01"})
    assert Staff.objects.get(pk=a.pk).designation == ladder["po2"]


def test_d8_cascade_grade_scope(ladder, depts):
    root = mk("R", ladder["po2"], years_ago(4), depts[0])
    same_grade_other_dept = mk("S", ladder["po2"], years_ago(1), depts[1])
    other_grade = mk("T", ladder["po1"], years_ago(1), depts[1])
    plan = plan_promotion([root], cascade=True, cascade_scope="grade", scope=Staff.objects.all())
    cascaded = {c.staff.pk: c.to_designation for c in plan.cascaded}
    assert cascaded == {same_grade_other_dept.pk: ladder["po1"]}
    assert other_grade.pk not in cascaded
    dept_plan = plan_promotion([root], cascade=True, cascade_scope="department", scope=Staff.objects.all())
    assert not dept_plan.cascaded                       # S is in another department


def test_d9_profile_promote_preselects_next_step(client, ladder, depts, hr):
    a = mk("A", ladder["po2"], years_ago(4), depts[0])
    client.force_login(hr)
    body = client.get(reverse("staff:detail", args=[a.pk])).content.decode()
    assert "data-promo-single" in body and "promo-next-card" in body
    assert "PROG. OFF. I" in body.split("promo-next-card", 1)[1][:800]
    assert 'value="target"' not in body                # HR gets no skip option


def test_d3_rollback_es_only(client, ladder, depts, hr, es):
    a = mk("A", ladder["po2"], years_ago(4), depts[0])
    promo = apply_promotion(plan_promotion([a]), effective_date=date(2026, 10, 1), reason="", approver=hr).promotions[0]
    with pytest.raises(PromotionError):
        rollback_promotion(promo, reason="error", approver=hr)
    client.force_login(hr)
    assert client.post(reverse("staff:promotion_rollback", args=[promo.pk]), {"reason": "x"}).status_code == 403
    hist = client.get(reverse("staff:promotion_history")).content.decode()
    assert "data-promo-rollback\n" not in hist and "data-promo-rollback-modal" not in hist and "Only the Executive Secretary" in hist
    client.force_login(es)
    assert "data-promo-rollback-modal" in client.get(reverse("staff:promotion_history")).content.decode()
    client.post(reverse("staff:promotion_rollback", args=[promo.pk]), {"reason": "entered in error"})
    assert Staff.objects.get(pk=a.pk).designation == ladder["po2"]
    from core.models import AuditLogEntry
    assert AuditLogEntry.objects.filter(action="promotion_reversal").exists()


def test_d3_es_role_has_powers_after_migration():
    from accounts.models import Role
    role = Role.objects.filter(code="executive-secretary").first()
    if role is None:
        pytest.skip("roles not seeded in this database")
    codes = set(role.permissions.values_list("codename", flat=True))
    assert {"override_promotion_step", "rollback_promotion", "promote_staff"} <= codes
