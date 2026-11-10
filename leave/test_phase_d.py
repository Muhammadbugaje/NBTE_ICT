"""v2.1 Phase D tests: leave types (F1-F6) and public holidays (F7, F8)."""
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from accounts.models import User
from staff.models import Department

pytestmark = pytest.mark.django_db

@pytest.fixture
def quiet():
    targets = ("workflow.services._notify_step_actor",
               "workflow.services.workflow_emails.send_request_awaiting_approval_email",
               "workflow.services.workflow_emails.send_request_completed_email",
               "workflow.pdf.generate_request_record")
    ps = [patch(t) for t in targets]
    for p in ps:
        p.start()
    yield
    for p in ps:
        p.stop()


@pytest.fixture
def dept():
    return Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})[0]


@pytest.fixture
def hr():
    return Department.objects.get_or_create(code="hr", defaults={"name": "Human Resources"})[0]


def mk(email, dept, gender="", superuser=False):
    if superuser:
        u = User.objects.create_superuser(email, "Pass-word-123")
    else:
        u = User.objects.create_user(email, "Pass-word-123", department=dept, must_change_password=False)
    st = u.staff_profile
    st.employee_no = "E-" + email.split("@")[0]
    st.first_name, st.last_name, st.gender = "Test", email.split("@")[0], gender
    st.save()
    return u


@pytest.fixture
def leave_rt(dept):
    from workflow.models import ApprovalChain, ApprovalStep, RequestCategory, RequestType

    approver = mk("approver@x.org", dept)
    cat = RequestCategory.objects.create(name="Leave", code="leave")
    chain = ApprovalChain.objects.create(name="Leave", code="leave-d")
    ApprovalStep.objects.create(chain=chain, position=1, name="Approve", approver_kind=ApprovalStep.APPROVER_SPECIFIC,
                                approver_user=approver, is_hierarchical=False)
    rt = RequestType.objects.create(name="Leave application", code="annual-leave", category=cat,
                                    handling_department=dept, approval_chain=chain, form_schema={"fields": [
                                        {"key": "start_date", "label": "First day", "type": "date", "required": True},
                                        {"key": "end_date", "label": "Last day", "type": "date", "required": True}]})
    rt.approver = approver
    return rt


@pytest.fixture
def types():
    from core import seeding
    seeding.run(only=["hr.leave_types"], no_deps=True)
    from leave.models import LeaveType
    return {t.code: t for t in LeaveType.objects.all()}


def _monday(after_days=40):
    d = date.today() + timedelta(days=after_days)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# F1 / F3
# ---------------------------------------------------------------------------
def test_f1_annual_migrated(dept, leave_rt):
    """The data migration turns existing rows into annual leave; rows saved
    without a type are annual too."""
    import importlib

    from django.apps import apps as global_apps
    from workflow.models import Request

    from leave.models import LeaveApplication, LeaveEntitlement, LeaveType

    u = mk("f1@x.org", dept)
    req = Request.objects.create(reference="REQ-F1", request_type=leave_rt, requester=u, subject="s", body="b",
                                 receiving_department=dept, status=Request.AWAITING)
    app = LeaveApplication.objects.create(request=req, staff=u.staff_profile, start_date=date(2026, 6, 1),
                                          end_date=date(2026, 6, 2))
    ent = LeaveEntitlement.objects.create(staff=u.staff_profile, year=2026, days=10)
    assert app.leave_type.code == "annual" and ent.leave_type.code == "annual"
    mig = importlib.import_module("leave.migrations.0003_existing_leave_is_annual")
    mig.forwards(global_apps, None)  # idempotent on an up-to-date database
    assert LeaveType.objects.filter(code="annual").count() == 1


def test_f3_seed_sets_create_types_and_holidays_idempotently():
    from core import seeding
    from leave.models import LeaveType
    from portal.models import Holiday

    res = seeding.run(only=["hr.leave_types", "hr.holidays"], no_deps=True)
    assert all(r.status == "ok" for _s, r in res)
    codes = set(LeaveType.objects.values_list("code", flat=True))
    assert {"annual", "casual", "maternity", "paternity", "sick", "study-with-pay", "study-without-pay",
            "leave-of-absence", "lwop", "compassionate", "examination"} <= codes
    assert LeaveType.objects.get(code="maternity").gender_restriction == "F"
    assert Holiday.objects.filter(date_start=date(2026, 10, 1), name="Independence Day").exists()
    assert Holiday.objects.filter(to_confirm=True).count() == 10
    again = seeding.run(only=["hr.leave_types", "hr.holidays"], no_deps=True)
    assert sum(r.changes for _s, r in again) == 0


# ---------------------------------------------------------------------------
# F4
# ---------------------------------------------------------------------------
def test_f4_days_mode_end_date(dept, types):
    """3 working days from Thu 24 Dec 2026 skip Christmas (Fri), Boxing Day
    (Sat), the weekend -> last day Tue 29 Dec."""
    from portal.models import Holiday, Scope

    from leave.services import counted_days, end_date_for

    u = mk("f4@x.org", dept)
    Holiday.objects.create(name="Christmas Day", date_start=date(2026, 12, 25), date_end=date(2026, 12, 25),
                           visibility=Scope.STAFF)
    casual = types["casual"]
    assert casual.is_days_mode
    assert end_date_for(date(2026, 12, 24), 3, casual, user=u) == date(2026, 12, 29)
    assert counted_days(date(2026, 12, 24), date(2026, 12, 29), casual, user=u) == 3
    maternity = types["maternity"]  # calendar days
    assert end_date_for(date(2026, 12, 24), 3, maternity, user=u) == date(2026, 12, 26)


# ---------------------------------------------------------------------------
# F5
# ---------------------------------------------------------------------------
def test_f5_validators(quiet, dept, leave_rt, types):
    from leave.services import apply_for_leave, check_application

    man = mk("f5m@x.org", dept, gender="M")
    woman = mk("f5w@x.org", dept, gender="F")
    start = _monday()
    # gender
    with pytest.raises(ValidationError, match="only available to women"):
        check_application(man, {"leave_type": "maternity", "start_date": start.isoformat(), "days": 10})
    check_application(woman, {"leave_type": "maternity", "start_date": start.isoformat(), "days": 10})
    # notice (annual: 14 days)
    soon = _monday(after_days=1)  # under 14 days away
    with pytest.raises(ValidationError, match="notice"):
        check_application(woman, {"leave_type": "annual", "start_date": soon.isoformat(),
                                  "end_date": (soon + timedelta(days=1)).isoformat()})
    # longest single application (casual: 3)
    with pytest.raises(ValidationError, match="at most 3"):
        check_application(woman, {"leave_type": "casual", "start_date": start.isoformat(), "days": 4})
    # balance (casual: 7 days a year)
    types["casual"].max_days_per_application = None
    types["casual"].save()
    with pytest.raises(ValidationError, match="Not enough casual leave"):
        check_application(woman, {"leave_type": "casual", "start_date": start.isoformat(), "days": 8})
    # document required (sick)
    with pytest.raises(ValidationError, match="supporting document"):
        check_application(woman, {"leave_type": "sick", "start_date": start.isoformat(), "days": 2},
                          has_document=False)
    # overlap across types
    apply_for_leave(woman, {"leave_type": "casual", "start_date": start.isoformat(), "days": 2})
    with pytest.raises(ValidationError, match="overlap"):
        check_application(woman, {"leave_type": "annual", "start_date": start.isoformat(),
                                  "end_date": (start + timedelta(days=4)).isoformat()})
    # applications per year
    types["compassionate"].max_per_year = 1
    types["compassionate"].save()
    later = start + timedelta(days=21)
    apply_for_leave(woman, {"leave_type": "compassionate", "start_date": later.isoformat(), "days": 1})
    with pytest.raises(ValidationError, match="at most 1 time"):
        check_application(woman, {"leave_type": "compassionate",
                                  "start_date": (later + timedelta(days=14)).isoformat(), "days": 1})
    # closed type
    types["examination"].is_active = False
    types["examination"].save()
    with pytest.raises(ValidationError, match="not open"):
        check_application(woman, {"leave_type": "examination", "start_date": start.isoformat(), "days": 1})


# ---------------------------------------------------------------------------
# F6
# ---------------------------------------------------------------------------
def test_f6_attendance_sync_per_type(quiet, dept, leave_rt, types, client):
    from attendance.models import AttendanceRecord
    from workflow.models import Request

    from leave.models import LeaveApplication
    from leave.services import apply_for_leave

    woman = mk("f6@x.org", dept, gender="F")
    types["maternity"].needs_document = False  # documents are covered by test_f5
    types["maternity"].save()
    start = _monday()
    req = apply_for_leave(woman, {"leave_type": "maternity", "start_date": start.isoformat(), "days": 7})
    app = LeaveApplication.objects.get(request=req)
    assert app.leave_type.code == "maternity" and app.days_requested == 7
    assert app.end_date == start + timedelta(days=6)  # calendar days
    Request.objects.filter(pk=req.pk).update(status="completed")
    req.refresh_from_db()
    req.save()  # signal -> sync_attendance
    recs = AttendanceRecord.objects.filter(leave_application=app)
    assert recs.count() == 5  # working days only in attendance
    assert {r.status_label for r in recs} == {"On maternity leave"}


# ---------------------------------------------------------------------------
# F2: HR creates a type; staff apply for it straight away (web)
# ---------------------------------------------------------------------------
def test_f2_hr_creates_type_staff_applies(quiet, dept, hr, leave_rt, types, client):
    from leave.models import LeaveApplication, LeaveType

    officer = mk("hrofficer@x.org", hr)
    staff = mk("f2staff@x.org", dept)
    client.force_login(staff)
    assert client.get(reverse("leave:types")).status_code == 403
    client.force_login(officer)
    r = client.post(reverse("leave:type_new"), {
        "name": "Sabbatical leave", "code": "sabbatical", "days": 20, "input_mode": "days",
        "counting": "working", "paid": "on", "gender_restriction": "", "notice_days": 0, "carry_over_days": 0,
        "sort_order": 50, "is_active": "on"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and b"Sabbatical leave" in r.content
    lt = LeaveType.objects.get(code="sabbatical")
    assert lt.is_days_mode and lt.days == 20
    client.force_login(staff)
    page = client.get(reverse("leave:apply") + "?type=sabbatical")
    assert page.status_code == 200 and b"Number of days" in page.content
    start = _monday()
    r = client.post(reverse("leave:apply"), {"leave_type": "sabbatical", "start_date": start.isoformat(),
                                             "days": 5, "reason": "Research"})
    assert r.status_code == 302, r.content[:400]
    app = LeaveApplication.objects.get(staff=staff.staff_profile)
    assert app.leave_type == lt and app.end_date == start + timedelta(days=4)
    # the HTMX fields fragment and preview
    frag = client.get(reverse("leave:apply_fields"), {"leave_type": "annual"})
    assert b"Last day of leave" in frag.content
    prev = client.get(reverse("leave:apply_preview"), {"leave_type": "sabbatical",
                                                        "start_date": (start + timedelta(days=21)).isoformat(),
                                                        "days": 2})
    assert b"2 working days" in prev.content


# ---------------------------------------------------------------------------
# F7 / F8
# ---------------------------------------------------------------------------
def test_f8_holiday_reconciles_absences(dept, hr, client):
    from attendance.models import AttendanceRecord
    from portal.models import Holiday

    u = mk("f8@x.org", dept)
    officer = mk("f8hr@x.org", hr)
    day = date.today() - timedelta(days=7)
    while day.weekday() > 4:
        day -= timedelta(days=1)
    AttendanceRecord.objects.create(user=u, date=day, status=AttendanceRecord.ABSENT,
                                    source=AttendanceRecord.SOURCE_AUTO)
    present = mk("f8p@x.org", dept)
    AttendanceRecord.objects.create(user=present, date=day, status=AttendanceRecord.PRESENT,
                                    source=AttendanceRecord.SOURCE_GPS)
    client.force_login(officer)
    r = client.post(reverse("leave:holiday_new"), {"name": "Special holiday", "date_start": day.isoformat(),
                                                   "date_end": day.isoformat(), "kind": "public",
                                                   "is_active": "on"})
    assert r.status_code == 302
    rec = AttendanceRecord.objects.get(user=u, date=day)
    assert rec.status == AttendanceRecord.HOLIDAY and "Special holiday" in rec.notes
    assert AttendanceRecord.objects.get(user=present, date=day).status == AttendanceRecord.PRESENT
    # moving the holiday restores the absence
    h = Holiday.objects.get(name="Special holiday")
    h.date_start = h.date_end = day + timedelta(days=60)
    h.save()
    assert AttendanceRecord.objects.get(user=u, date=day).status == AttendanceRecord.ABSENT
    # exports show holidays as rows, not blanks
    h.date_start = h.date_end = day
    h.save()
    from attendance.views import register_rows
    nobody = mk("f8n@x.org", dept)
    rows = register_rows([nobody, u], [], day, day)
    assert [(r["user"], r["code"]) for r in rows] == [(nobody, "H"), (u, "H")]


def test_f7_holidays_page_scoped_to_hr(dept, hr, client):
    client.force_login(mk("f7s@x.org", dept))
    assert client.get(reverse("leave:holidays")).status_code == 403
    client.force_login(mk("f7h@x.org", hr))
    r = client.get(reverse("leave:holidays") + "?year=2026")
    assert r.status_code == 200 and b"Public holidays" in r.content
