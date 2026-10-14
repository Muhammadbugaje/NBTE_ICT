from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Role, UserPermissionOverride
from config.models import ConfigEntry
from portal.models import Holiday, Scope
from staff.models import Department
from workflow.models import ApprovalChain, Request, RequestCategory, RequestType

from .models import LeaveApplication, LeaveEntitlement
from .services import (
    ROLE_ENTITLEMENT_CONFIG_KEY,
    balance_for,
    request_summary,
    working_days,
)


class AnnualLeaveWorkingDayTests(SimpleTestCase):
    def test_weekdays_exclude_configured_holidays(self):
        holiday = Holiday(
            date_start=date(2026, 6, 10),
            date_end=date(2026, 6, 10),
            is_recurring=False,
        )
        with patch(
            "leave.services._visible_holidays",
            return_value=[holiday],
        ):
            count = working_days(
                date(2026, 6, 8),
                date(2026, 6, 12),
                user=None,
            )
        self.assertEqual(count, 4)


class VisibleHolidayTests(TestCase):
    def test_recurring_department_holiday_is_scoped_and_applied(self):
        department = Department.objects.create(name="ICT", code="ict")
        other_department = Department.objects.create(name="Finance", code="finance")
        user = get_user_model().objects.create_user(
            "staff@example.org",
            "Pass-word-123",
            department=department,
            must_change_password=False,
        )
        Holiday.objects.create(
            name="ICT holiday",
            date_start=date(2020, 6, 10),
            date_end=date(2020, 6, 10),
            department=department,
            visibility=Scope.DEPARTMENT,
            is_recurring=True,
        )
        Holiday.objects.create(
            name="Finance holiday",
            date_start=date(2020, 6, 11),
            date_end=date(2020, 6, 11),
            department=other_department,
            visibility=Scope.DEPARTMENT,
            is_recurring=True,
        )

        count = working_days(
            date(2026, 6, 10),
            date(2026, 6, 11),
            user=user,
        )

        self.assertEqual(count, 1)


class AnnualLeaveBalanceTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name="ICT", code="ict")
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            "staff@example.org",
            "Pass-word-123",
            department=self.department,
            must_change_password=False,
        )
        self.staff = self.user.staff_profile
        self.staff.employee_no = "NBTE-002"
        self.staff.first_name = "Staff"
        self.staff.last_name = "Member"
        self.staff.save()
        category = RequestCategory.objects.create(name="HR", code="hr")
        chain = ApprovalChain.objects.create(name="Leave approvals", code="leave")
        self.request_type = RequestType.objects.create(
            name="Annual leave",
            code="annual-leave",
            category=category,
            handling_department=self.department,
            approval_chain=chain,
        )

    def _request(self, reference, status):
        return Request.objects.create(
            reference=reference,
            request_type=self.request_type,
            requester=self.user,
            subject=reference,
            body="Annual leave",
            receiving_department=self.department,
            status=status,
        )

    def test_only_approved_leave_is_deducted_from_calendar_year_balance(self):
        approved = self._request("REQ-APPROVED", Request.IN_PROGRESS)
        pending = self._request("REQ-PENDING", Request.AWAITING)
        LeaveApplication.objects.create(
            request=approved,
            staff=self.staff,
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 12),
        )
        LeaveApplication.objects.create(
            request=pending,
            staff=self.staff,
            start_date=date(2026, 6, 15),
            end_date=date(2026, 6, 16),
        )
        LeaveEntitlement.objects.create(
            staff=self.staff,
            year=2026,
            days=20,
        )

        with patch("leave.services._visible_holidays", return_value=[]):
            balance = balance_for(self.staff, 2026)

        self.assertEqual(balance["entitlement"], 20)
        self.assertEqual(balance["used"], 5)
        self.assertEqual(balance["pending"], 2)
        self.assertEqual(balance["remaining"], 15)

    def test_workflow_review_flags_requests_over_the_available_balance(self):
        used_request = self._request("REQ-USED", Request.IN_PROGRESS)
        leave_request = self._request("REQ-OVER", Request.AWAITING)
        LeaveApplication.objects.create(
            request=used_request,
            staff=self.staff,
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 1),
        )
        LeaveApplication.objects.create(
            request=leave_request,
            staff=self.staff,
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 12),
        )
        LeaveEntitlement.objects.create(staff=self.staff, year=2026, days=2)
        self.client.force_login(self.user)

        with patch("leave.services._visible_holidays", return_value=[]):
            summary = request_summary(leave_request)
            response = self.client.get(
                reverse("workflow:detail", kwargs={"pk": leave_request.pk})
            )

        self.assertTrue(summary["has_over_balance"])
        self.assertEqual(summary["years"][0]["over_balance_days"], 4)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Annual leave balance review")
        self.assertContains(response, "HR may approve this exception.")

    def test_workflow_review_does_not_count_its_own_approved_days_as_prior_usage(self):
        leave_request = self._request("REQ-CURRENT", Request.IN_PROGRESS)
        LeaveApplication.objects.create(
            request=leave_request,
            staff=self.staff,
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 12),
        )
        LeaveEntitlement.objects.create(staff=self.staff, year=2026, days=5)

        with patch("leave.services._visible_holidays", return_value=[]):
            summary = request_summary(leave_request)

        self.assertFalse(summary["has_over_balance"])
        self.assertEqual(summary["years"][0]["remaining"], 5)

    def test_approved_leave_syncs_attendance_and_withdrawal_cleans_it(self):
        from attendance.models import AttendanceRecord

        start = timezone.localdate() + timedelta(days=1)
        while start.weekday() != 0:
            start += timedelta(days=1)
        end = start + timedelta(days=4)
        leave_request = self._request("REQ-ATTENDANCE", Request.AWAITING)

        with patch("leave.services._visible_holidays", return_value=[]):
            application = LeaveApplication.objects.create(
                request=leave_request,
                staff=self.staff,
                start_date=start,
                end_date=end,
            )
            self.assertFalse(
                AttendanceRecord.objects.filter(
                    user=self.user,
                    date__range=(start, end),
                ).exists()
            )

            leave_request.status = Request.IN_PROGRESS
            leave_request.save(update_fields=["status"])
            records = AttendanceRecord.objects.filter(
                user=self.user,
                date__range=(start, end),
            )
            self.assertEqual(records.count(), 5)
            self.assertTrue(all(
                record.status == AttendanceRecord.ON_LEAVE
                and record.source == AttendanceRecord.SOURCE_LEAVE
                and record.leave_application_id == application.pk
                for record in records
            ))

            leave_request.status = Request.REJECTED
            leave_request.save(update_fields=["status"])
            self.assertFalse(
                AttendanceRecord.objects.filter(
                    leave_application=application,
                    status=AttendanceRecord.ON_LEAVE,
                ).exists()
            )

    def test_gps_checkin_during_leave_preserves_leave_dates_and_return_date(self):
        from attendance.models import AttendanceLocation, AttendanceRecord
        from attendance.services import check_in_user
        from leave.services import resumption_date

        friday = timezone.localdate() + timedelta(days=1)
        while friday.weekday() != 4:
            friday += timedelta(days=1)
        leave_request = self._request("REQ-EARLY-RETURN", Request.IN_PROGRESS)
        location = AttendanceLocation.objects.create(
            code="leave-checkin-test",
            name="Test office",
            latitude=0,
            longitude=0,
            work_start_time=time(9),
            grace_minutes=15,
        )

        with patch("leave.services._visible_holidays", return_value=[]):
            application = LeaveApplication.objects.create(
                request=leave_request,
                staff=self.staff,
                start_date=friday,
                end_date=friday,
            )
            return_date = resumption_date(friday, user=self.user)
            now = timezone.make_aware(datetime.combine(friday, time(9, 5)))
            with (
                patch("attendance.services.is_working_day", return_value=True),
                patch(
                    "attendance.services.find_matching_location",
                    return_value=(location, 5),
                ),
            ):
                result = check_in_user(self.user, 0, 0, now=now)

            self.assertTrue(result.ok)
            self.assertEqual(result.status, AttendanceRecord.PRESENT)
            record = AttendanceRecord.objects.get(user=self.user, date=friday)
            self.assertIsNone(record.leave_application_id)
            self.assertEqual(record.source, AttendanceRecord.SOURCE_GPS)
            application.refresh_from_db()
            self.assertEqual(application.end_date, friday)
            self.assertEqual(resumption_date(application.end_date, user=self.user), return_date)


class LeaveEntitlementPermissionTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name="HR", code="hr")
        user_model = get_user_model()
        self.manager = user_model.objects.create_user(
            "hr@example.org",
            "Pass-word-123",
            department=self.department,
            must_change_password=False,
        )
        self.staff = self.manager.staff_profile
        self.staff.employee_no = "NBTE-003"
        self.staff.first_name = "HR"
        self.staff.last_name = "Manager"
        self.staff.save()
        permission = Permission.objects.get(
            content_type__app_label="leave",
            codename="manage_leave_entitlements",
        )
        UserPermissionOverride.objects.create(
            user=self.manager,
            permission=permission,
            effect=UserPermissionOverride.GRANT,
        )

    def test_entitlement_screen_requires_explicit_permission(self):
        response = self.client.get(reverse("leave:entitlements"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.manager)
        response = self.client.get(reverse("leave:entitlements"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Annual leave entitlements")

    def test_manager_can_filter_leave_management_and_open_workflow_record(self):
        from workflow.models import ApprovalChain, Request, RequestCategory, RequestType

        category = RequestCategory.objects.create(name="HR", code="leave-hr")
        chain = ApprovalChain.objects.create(name="Leave chain", code="leave-hr")
        request_type = RequestType.objects.create(
            name="Annual leave",
            code="annual-leave",
            category=category,
            handling_department=self.department,
            approval_chain=chain,
        )
        workflow_request = Request.objects.create(
            reference="REQ-LEAVE-MANAGE",
            request_type=request_type,
            requester=self.manager,
            subject="Annual leave request",
            body="Annual leave",
            receiving_department=self.department,
            status=Request.AWAITING,
        )
        LeaveApplication.objects.create(
            request=workflow_request,
            staff=self.staff,
            start_date=date(timezone.localdate().year, 8, 3),
            end_date=date(timezone.localdate().year, 8, 7),
        )
        self.client.force_login(self.manager)

        response = self.client.get(reverse("leave:management"), {
            "year": timezone.localdate().year,
            "status": Request.AWAITING,
            "q": "REQ-LEAVE-MANAGE",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Leave management")
        self.assertContains(response, "REQ-LEAVE-MANAGE")
        self.assertContains(
            response,
            reverse("workflow:detail", kwargs={"pk": workflow_request.pk}),
        )

    def test_manager_can_save_a_calendar_year_entitlement(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("leave:entitlements"), {
            "staff_id": self.staff.pk,
            "year": 2026,
            "days": "24",
        })
        self.assertEqual(response.status_code, 302)
        entitlement = LeaveEntitlement.objects.get(
            staff=self.staff,
            year=2026,
        )
        self.assertEqual(entitlement.days, 24)
        self.assertEqual(entitlement.updated_by, self.manager)

    def test_manager_is_scoped_to_their_department(self):
        other_department = Department.objects.create(
            name="Finance",
            code="finance",
        )
        other_user = get_user_model().objects.create_user(
            "finance@example.org",
            "Pass-word-123",
            department=other_department,
            must_change_password=False,
        )
        other_staff = other_user.staff_profile
        other_staff.employee_no = "NBTE-004"
        other_staff.first_name = "Finance"
        other_staff.last_name = "Member"
        other_staff.save()
        self.client.force_login(self.manager)

        response = self.client.get(reverse("leave:entitlements"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "HR Manager")
        self.assertNotContains(response, "Finance Member")

        response = self.client.post(reverse("leave:entitlements"), {
            "staff_id": other_staff.pk,
            "year": 2026,
            "days": "20",
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            LeaveEntitlement.objects.filter(staff=other_staff).exists()
        )


class RoleBasedLeaveEntitlementTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name="ICT", code="ict")
        self.role = Role.objects.create(
            code="officer-technician",
            name="Officer / Technician",
            rank=20,
        )
        self.user = get_user_model().objects.create_user(
            "technician@example.org",
            "Pass-word-123",
            department=self.department,
            role=self.role,
            must_change_password=False,
        )
        self.staff = self.user.staff_profile
        self.client.force_login(
            get_user_model().objects.create_superuser(
                "admin@example.org",
                "Pass-word-123",
            )
        )

    def test_role_policy_provides_default_and_person_override_wins(self):
        ConfigEntry.objects.create(
            key=ROLE_ENTITLEMENT_CONFIG_KEY,
            value='{"officer-technician":"22.5"}',
            value_type="json",
            group="Leave",
        )

        balance = balance_for(self.staff, 2026)
        self.assertEqual(balance["entitlement"], 22.5)
        self.assertEqual(balance["entitlement_source"], "role")
        self.assertTrue(balance["is_set"])

        LeaveEntitlement.objects.create(
            staff=self.staff,
            year=2026,
            days=18,
        )
        balance = balance_for(self.staff, 2026)
        self.assertEqual(balance["entitlement"], 18)
        self.assertEqual(balance["entitlement_source"], "override")

        response = self.client.post(reverse("leave:entitlements"), {
            "staff_id": self.staff.pk,
            "year": 2026,
            "days": "",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            LeaveEntitlement.objects.filter(
                staff=self.staff,
                year=2026,
            ).exists()
        )
        self.assertEqual(balance_for(self.staff, 2026)["entitlement"], 22.5)

    def test_settings_lists_roles_and_saves_days_for_role(self):
        response = self.client.get(reverse("config:settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Annual leave days by role")
        self.assertContains(response, "Officer / Technician")
        self.assertContains(response, "Rank 20")

        response = self.client.post(
            reverse("config:save_leave_role_entitlements"),
            {f"role_{self.role.pk}": "24.5"},
        )
        self.assertRedirects(response, reverse("config:settings"))
        self.assertEqual(
            balance_for(self.staff, 2026)["entitlement"],
            24.5,
        )


class AnnualLeaveApplicationFlowTests(TestCase):
    def setUp(self):
        from workflow.models import ApprovalStep

        self.department = Department.objects.create(name="ICT", code="ict")
        user_model = get_user_model()
        self.applicant = user_model.objects.create_user(
            "applicant@example.org",
            "Pass-word-123",
            department=self.department,
            must_change_password=False,
        )
        self.applicant_staff = self.applicant.staff_profile
        self.applicant_staff.employee_no = "NBTE-TEST-001"
        self.applicant_staff.first_name = "Test"
        self.applicant_staff.last_name = "Applicant"
        self.applicant_staff.save()
        self.manager = user_model.objects.create_user(
            "leave-manager@example.org",
            "Pass-word-123",
            department=self.department,
            must_change_password=False,
        )
        self.support_admin = user_model.objects.create_superuser(
            "support-admin@example.org",
            "Pass-word-123",
        )
        permission = Permission.objects.get(
            content_type__app_label="leave",
            codename="manage_leave_entitlements",
        )
        UserPermissionOverride.objects.create(
            user=self.manager,
            permission=permission,
            effect=UserPermissionOverride.GRANT,
        )

        category = RequestCategory.objects.create(name="Leave", code="leave")
        chain = ApprovalChain.objects.create(name="Leave test approval", code="leave-test")
        ApprovalStep.objects.create(
            chain=chain,
            position=1,
            name="Leave manager approval",
            approver_kind=ApprovalStep.APPROVER_SPECIFIC,
            approver_user=self.manager,
            is_hierarchical=False,
        )
        self.request_type = RequestType.objects.create(
            name="Annual leave application",
            code="annual-leave",
            category=category,
            handling_department=self.department,
            approval_chain=chain,
            form_schema={
                "fields": [
                    {"key": "start_date", "label": "First day of leave", "type": "date", "required": True},
                    {"key": "end_date", "label": "Last day of leave", "type": "date", "required": True},
                ],
            },
        )

    def test_staff_can_apply_manager_sees_request_and_approval_marks_leave_attendance(self):
        from attendance.models import AttendanceRecord
        from workflow.models import Request

        start = timezone.localdate() + timedelta(days=7)
        while start.weekday() != 0:
            start += timedelta(days=1)
        end = start + timedelta(days=1)

        from core.impersonation import IMPERSONATE_SESSION_KEY

        self.client.force_login(self.support_admin)
        session = self.client.session
        session[IMPERSONATE_SESSION_KEY] = self.applicant.pk
        session.save()
        request_types_page = self.client.get(reverse("workflow:raise"))
        self.assertEqual(request_types_page.status_code, 200)
        self.assertContains(request_types_page, "Leave")
        self.assertContains(request_types_page, "Annual leave application")

        with (
            patch("leave.services._visible_holidays", return_value=[]),
            patch("workflow.services._notify_step_actor"),
            patch("workflow.services.workflow_emails.send_request_awaiting_approval_email"),
            patch("workflow.services.workflow_emails.send_request_completed_email"),
            patch("workflow.pdf.generate_request_record"),
        ):
            response = self.client.post(
                reverse("workflow:raise_type", kwargs={"type_code": "annual-leave"}),
                {
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "subject": "Annual leave test",
                    "body": "Requesting two days of annual leave.",
                    "priority": "normal",
                },
            )

        self.assertEqual(response.status_code, 302)
        leave_request = Request.objects.get(
            requester=self.applicant,
            subject="Annual leave test",
        )
        application = LeaveApplication.objects.get(request=leave_request)
        self.assertEqual(leave_request.request_type.category.code, "leave")
        self.assertEqual((application.start_date, application.end_date), (start, end))
        self.assertEqual(leave_request.form_data, {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        })
        self.assertEqual(leave_request.status, Request.AWAITING)
        self.assertFalse(
            AttendanceRecord.objects.filter(leave_application=application).exists()
        )

        self.client.force_login(self.manager)
        listing = self.client.get(reverse("leave:management"), {
            "year": start.year,
            "status": Request.AWAITING,
            "q": "Annual leave test",
        })
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, leave_request.reference)

        with (
            patch("leave.services._visible_holidays", return_value=[]),
            patch("workflow.services._notify_step_actor"),
            patch("workflow.services.workflow_emails.send_request_awaiting_approval_email"),
            patch("workflow.services.workflow_emails.send_request_completed_email"),
            patch("workflow.pdf.generate_request_record"),
        ):
            response = self.client.post(
                reverse("workflow:approve", kwargs={"pk": leave_request.pk}),
                {"comment": "Approved for test."},
            )

        self.assertEqual(response.status_code, 302)
        leave_request.refresh_from_db()
        self.assertEqual(leave_request.status, Request.COMPLETED)
        leave_records = AttendanceRecord.objects.filter(
            leave_application=application,
            status=AttendanceRecord.ON_LEAVE,
        )
        self.assertEqual(leave_records.count(), 2)
        self.assertTrue(all(
            record.source == AttendanceRecord.SOURCE_LEAVE
            for record in leave_records
        ))

        self.client.force_login(self.applicant)
        attendance_page = self.client.get(
            reverse("attendance:home"),
            {"month": start.strftime("%Y-%m")},
        )
        self.assertEqual(attendance_page.status_code, 200)
        month_cells = [
            cell
            for week in attendance_page.context["month_grid"]
            for cell in week
        ]
        visible_leave_cells = [
            cell for cell in month_cells
            if cell["record"] and cell["record"].status == AttendanceRecord.ON_LEAVE
        ]
        self.assertEqual(len(visible_leave_cells), 2)
