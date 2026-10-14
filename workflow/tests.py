from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from staff.models import Department
from workflow.models import ApprovalChain, Request, RequestCategory, RequestType


class WorkflowManagementUrlTests(TestCase):
    def test_management_urls_include_delete_routes(self):
        self.assertEqual(
            reverse("workflow_manage:type_delete", args=[42]),
            "/manage/workflow/types/42/delete/",
        )
        self.assertEqual(
            reverse("workflow_manage:category_delete", args=[42]),
            "/manage/workflow/categories/42/delete/",
        )


class AssignedToMeViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.department = Department.objects.create(name="ICT", code="ict")
        self.assignee = user_model.objects.create_user(
            "assignee@example.org",
            "Pass-word-123",
            department=self.department,
            must_change_password=False,
        )
        self.requester = user_model.objects.create_user(
            "requester@example.org",
            "Pass-word-123",
            department=self.department,
            must_change_password=False,
        )
        chain = ApprovalChain.objects.create(name="Test chain", code="test-chain")
        category = RequestCategory.objects.create(name="Test", code="test")
        request_type = RequestType.objects.create(
            name="Test request",
            code="test-request",
            category=category,
            handling_department=self.department,
            approval_chain=chain,
        )
        self.request = Request.objects.create(
            reference="REQ-2027-00001",
            request_type=request_type,
            requester=self.requester,
            subject="Laptop replacement",
            body="The laptop needs replacement.",
            receiving_department=self.department,
            assigned_to=self.assignee,
        )
        self.client.force_login(self.assignee)

    def test_assigned_requests_render_as_linked_cards(self):
        response = self.client.get(reverse("workflow:assigned_to_me"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Laptop replacement")
        self.assertContains(response, "REQ-2027-00001")
        self.assertContains(
            response,
            reverse("workflow:detail", args=[self.request.pk]),
        )
        self.assertContains(response, 'type="search"')
        self.assertContains(response, "Sort by")
        self.assertNotContains(response, 'onclick="window.location')

    def test_invalid_status_filter_falls_back_to_all_requests(self):
        response = self.client.get(
            reverse("workflow:assigned_to_me"),
            {"status": "not-a-status"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status_filter"], "all")
        self.assertContains(response, "Laptop replacement")
