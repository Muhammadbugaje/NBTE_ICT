"""seed_nbte demo set for v2.1 Phase D (only with --with-demo).

Creates two HR demo accounts and a believable month of attendance for the
DPAP demo staff (check-ins and check-outs, late days, an absence, a missed
check-out), so the analytics page, the holidays page and the exports have
something to show. Idempotent: existing accounts and records are kept.
"""
import random
from datetime import datetime, time, timedelta

from core.seeding import seed_set

DEMO_PASSWORD = "NBTE-Demo#2026"
HR_ACCOUNTS = (
    ("hr.director@demo.nbte.local", "Rahila", "HR Director (Demo)", "director", "HR-DEMO-001", "F"),
    ("hr.officer@demo.nbte.local", "Musa", "HR Officer (Demo)", "officer", "HR-DEMO-002", "M"),
)
DEMO_GENDERS = {"qa.officer@demo.nbte.local": "F", "qa.busy_officer@demo.nbte.local": "F",
                "qa.director@demo.nbte.local": "M", "qa.team_lead@demo.nbte.local": "M",
                "qa.division_head@demo.nbte.local": "F", "qa.cqac@demo.nbte.local": "M"}


def _ensure_hr_accounts(ctx):
    from accounts.models import Role, User
    from staff.models import Department, Staff

    hr = Department.objects.filter(code__iexact="hr").first()
    if hr is None:
        ctx.warn("No HR department (code 'hr'): HR demo accounts skipped")
        return
    for email, first, last, role_code, emp, gender in HR_ACCOUNTS:
        user = User.objects.filter(email__iexact=email).first()
        if user is None:
            user = User(email=email, first_name=first, last_name=last, user_type="staff",
                        role=Role.objects.filter(code=role_code).first(), department=hr, is_active=True)
            if hasattr(user, "must_change_password"):
                user.must_change_password = False
            user.set_password(DEMO_PASSWORD)
            user.save()
            try:
                from qa_core.models import DemoTag
                DemoTag.tag(user)
            except Exception:  # pragma: no cover
                pass
            ctx.res.created += 1
            ctx.note(f"{email} ({role_code}, HR) password {DEMO_PASSWORD}")
        else:
            ctx.res.unchanged += 1
        if not Staff._base_manager.filter(user=user).exists():
            Staff.objects.create(user=user, employee_no=emp, first_name=first, last_name=last, email=email,
                                 department=hr, gender=gender)
            ctx.res.created += 1
    import org
    director = User.objects.filter(email__iexact=HR_ACCOUNTS[0][0]).first()
    if director is not None and org.director_of(hr) is None:
        try:
            org.assign_director(hr, director, actor=None, reason="Phase D demo")
        except Exception as exc:  # the one-director rule may refuse; the role still works
            ctx.note(f"HR demo director not assigned as department director: {exc}")


def _demo_location(ctx):
    """An office for the demo (coordinates are placeholders: Kaduna)."""
    from attendance.models import AttendanceLocation

    loc, created = AttendanceLocation.objects.get_or_create(
        code="demo-hq", defaults={"name": "NBTE Headquarters (Demo)", "latitude": "10.523300",
                                  "longitude": "7.440300", "radius_meters": 200, "gps_offset_meters": 50,
                                  "work_start_time": time(8, 0), "work_end_time": time(16, 0), "grace_minutes": 15,
                                  "notes": "Demo office (seed_nbte --with-demo). Coordinates are placeholders."})
    if created:
        ctx.res.created += 1
    return loc


def _demo_permissions(ctx):
    """The HR demo director may open the office codes and the QR screen."""
    from django.contrib.auth.models import Permission

    from accounts.models import User

    perm = Permission.objects.filter(content_type__app_label="attendance", codename="view_location_codes").first()
    u = User.objects.filter(email__iexact=HR_ACCOUNTS[0][0]).first()
    if perm is not None and u is not None and not u.user_permissions.filter(pk=perm.pk).exists():
        u.user_permissions.add(perm)
        ctx.res.filled += 1


def _demo_genders(ctx):
    from staff.models import Staff

    for email, g in DEMO_GENDERS.items():
        n = Staff.objects.filter(user__email__iexact=email, gender="").update(gender=g)
        ctx.res.filled += n


def _demo_month(ctx):
    from django.utils import timezone

    from accounts.models import User
    from attendance.models import AttendanceLocation, AttendanceRecord
    from attendance.services import holidays_on, is_holiday_for, is_working_day

    loc = AttendanceLocation.objects.filter(code="demo-hq").first() or \
        AttendanceLocation.objects.filter(is_active=True).order_by("pk").first()
    users = list(User.objects.filter(email__iendswith="@demo.nbte.local", user_type="staff", is_active=True,
                                     department__code__in=["dpap", "hr"]).order_by("email"))
    if not users:
        return
    today = timezone.localdate()
    start = today - timedelta(days=42)
    tz = timezone.get_current_timezone()
    made = 0
    for u in users:
        rnd = random.Random(f"phase-d-{u.email}")
        punctual = rnd.random()
        d = start
        while d < today:
            if is_working_day(d) and not AttendanceRecord.objects.filter(user=u, date=d).exists():
                hols = holidays_on(d)
                if hols and is_holiday_for(u, d, hols):
                    d += timedelta(days=1)
                    continue
                roll = rnd.random()
                if roll < 0.05:
                    AttendanceRecord.objects.create(user=u, date=d, status=AttendanceRecord.ABSENT,
                                                    source=AttendanceRecord.SOURCE_AUTO,
                                                    notes="Demo data: no check-in.")
                else:
                    minute = int(rnd.gauss(8 * 60 + 40 - punctual * 25, 18))
                    cin = datetime.combine(d, time(min(23, minute // 60), minute % 60), tz)
                    late = minute > 9 * 60 + 15
                    missing = rnd.random() < 0.08
                    out_min = 16 * 60 + 30 + int(rnd.gauss(20, 25))
                    cout = None if missing else datetime.combine(d, time(out_min // 60, out_min % 60), tz)
                    AttendanceRecord.objects.create(
                        user=u, date=d, status=AttendanceRecord.LATE if late else AttendanceRecord.PRESENT,
                        source=rnd.choice([AttendanceRecord.SOURCE_GPS, AttendanceRecord.SOURCE_QR,
                                           AttendanceRecord.SOURCE_GPS]),
                        check_in_at=cin, location=loc, check_out_at=cout,
                        check_out_source=AttendanceRecord.SOURCE_GPS if cout else "",
                        missing_checkout=missing, notes="Demo data.")
                made += 1
            d += timedelta(days=1)
    ctx.res.created += made
    if not made:
        ctx.res.unchanged += 1


@seed_set("demo.attendance", group="demo", depends=("roles", "hr.leave_types", "hr.holidays"), order=910,
          phase="D", demo_only=True)
def seed_demo_attendance(ctx):
    _ensure_hr_accounts(ctx)
    _demo_location(ctx)
    _demo_permissions(ctx)
    _demo_genders(ctx)
    _demo_month(ctx)
