"""v2.1 Phase D (G7): a confirmed QA visit team is "On official duty" on the
visit days; auto-absent skips them; the availability query reports them."""
from datetime import timedelta

import pytest

from accounts.models import User

pytestmark = pytest.mark.django_db


def test_g7_visit_writes_on_duty_and_updates_on_date_change(p5_visit):
    from attendance import duty
    from attendance.models import AttendanceRecord
    from inspection.models import Inspection

    insp = Inspection.unscoped.get(pk=p5_visit.inspection.pk)
    members = duty.duty_user_ids(insp)
    assert members, "the confirmed visit team has NBTE members"
    days = duty.duty_days(insp)
    recs = AttendanceRecord.objects.filter(duty_inspection=insp, status="on_duty")
    assert recs.count() == len(members) * len(days)
    assert all(r.source == "visit" and insp.reference in r.notes for r in recs)
    # dates move: old days dropped, new ones written
    shift = timedelta(days=7)
    Inspection.unscoped.filter(pk=insp.pk).update(scheduled_start=insp.scheduled_start + shift,
                                                  scheduled_end=insp.scheduled_end + shift)
    insp.refresh_from_db()
    duty.sync_visit_duty(insp)
    dates = set(AttendanceRecord.objects.filter(duty_inspection=insp).values_list("date", flat=True))
    assert dates == set(duty.duty_days(insp)) and not dates & set(days)
    # cancelled: duty days removed
    Inspection.unscoped.filter(pk=insp.pk).update(status="cancelled")
    insp.refresh_from_db()
    duty.sync_visit_duty(insp)
    assert not AttendanceRecord.objects.filter(duty_inspection=insp).exists()


def test_g7_auto_absent_skips_duty_and_availability(p5_visit):
    from attendance import availability, duty, services
    from attendance.models import AttendanceRecord
    from inspection.models import Inspection
    from staff.models import Staff

    insp = Inspection.unscoped.get(pk=p5_visit.inspection.pk)
    uid = sorted(duty.duty_user_ids(insp))[0]
    day = duty.duty_days(insp)[0]
    AttendanceRecord.objects.filter(user_id=uid, date=day).delete()   # as if the signal never ran
    Staff.objects.filter(user_id=uid).update(is_deleted=False)
    services.auto_mark_absent(for_date=day)
    rec = AttendanceRecord.objects.get(user_id=uid, date=day)
    assert rec.status == "on_duty" and rec.duty_inspection_id == insp.pk
    user = User.objects.get(pk=uid)
    av = availability(user, insp.scheduled_start, insp.scheduled_end)
    assert set(av) >= {"leave", "holiday", "duty", "unavailable", "free_working_days"}
    assert av["duty"] == duty.duty_days(insp) and not av["free_working_days"]


