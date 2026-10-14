from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from assets.models import Asset, AssetAssignment, AssetCategory
from assets.services import assign_asset, return_asset
from staff.models import Department


class AssetReturnDateTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.department = Department.objects.create(
            name="ICT",
            code="ict",
        )
        self.issuer = user_model.objects.create_user(
            "issuer@example.org",
            "Pass-word-123",
            department=self.department,
            must_change_password=False,
        )
        self.assignee = user_model.objects.create_user(
            "assignee@example.org",
            "Pass-word-123",
            department=self.department,
            must_change_password=False,
        )
        category = AssetCategory.objects.create(name="Laptop", code="laptop")
        self.asset = Asset.objects.create(
            asset_tag="ICT-0001",
            category=category,
            owner_department=self.department,
            state="in_service",
        )

    def test_assignment_service_persists_expected_return_date_and_overdue_state(self):
        due_date = timezone.localdate() - timedelta(days=1)

        assignment = assign_asset(
            asset=self.asset,
            to_user=self.assignee,
            by_user=self.issuer,
            expected_return_at=due_date,
        )

        assignment.refresh_from_db()
        self.assertEqual(assignment.expected_return_at, due_date)
        self.assertTrue(assignment.is_overdue)

    def test_assignment_is_not_overdue_when_due_in_future_or_already_returned(self):
        due_date = timezone.localdate() + timedelta(days=1)
        assignment = assign_asset(
            asset=self.asset,
            to_user=self.assignee,
            by_user=self.issuer,
            expected_return_at=due_date,
        )
        self.assertFalse(assignment.is_overdue)

        returned = return_asset(asset=self.asset, by_user=self.issuer)
        returned.refresh_from_db()
        self.assertFalse(returned.is_overdue)

    def test_assignment_without_expected_return_date_is_not_overdue(self):
        assignment = assign_asset(
            asset=self.asset,
            to_user=self.assignee,
            by_user=self.issuer,
        )

        self.assertIsNone(assignment.expected_return_at)
        self.assertFalse(assignment.is_overdue)


class AssetAssignmentFormTests(TestCase):
    def test_expected_return_date_is_optional_and_parsed(self):
        from assets.forms import AssignAssetForm

        user = get_user_model().objects.create_user(
            "assignee@example.org",
            "Pass-word-123",
            must_change_password=False,
        )
        form = AssignAssetForm(data={
            "assigned_to": user.pk,
            "condition_on_assign": "good",
            "expected_return_at": "2027-01-15",
        })

        self.assertTrue(form.fields["expected_return_at"].required is False)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["expected_return_at"], date(2027, 1, 15))
