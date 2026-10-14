from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserPermissionOverride
from assets.models import Asset, AssetAssignment, AssetCategory
from core.models import ContactMessage
from dashboards.action_queue import build_action_queue
from staff.models import Department
from tickets.models import Ticket, TicketCategory
from workflow.models import (
    ApprovalChain,
    ApprovalStep,
    Request,
    RequestApproval,
    RequestCategory,
    RequestType,
)


class ActionQueueTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.department = Department.objects.create(name="ICT", code="ict")
        self.other_department = Department.objects.create(
            name="Finance",
            code="finance",
        )
        self.user = user_model.objects.create_user(
            "handler@example.org",
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
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_handle_contact_messages",
        )
        UserPermissionOverride.objects.create(
            user=self.user,
            permission=permission,
            effect=UserPermissionOverride.GRANT,
        )

    def _approval_request(self, *, approver=None, reference="REQ-2027-00001"):
        chain = ApprovalChain.objects.create(
            name=f"Chain {reference}",
            code=reference.lower().replace("-", ""),
        )
        step = ApprovalStep.objects.create(
            chain=chain,
            position=1,
            name="Manager approval",
            kind=ApprovalStep.KIND_APPROVAL,
            approver_kind=ApprovalStep.APPROVER_SPECIFIC,
            approver_user=approver or self.user,
            is_hierarchical=False,
        )
        category = RequestCategory.objects.create(
            name=f"Category {reference}",
            code=f"category-{reference.lower().replace('-', '')}",
        )
        request_type = RequestType.objects.create(
            name=f"Type {reference}",
            code=f"type-{reference.lower().replace('-', '')}",
            category=category,
            handling_department=self.department,
            approval_chain=chain,
        )
        return Request.objects.create(
            reference=reference,
            request_type=request_type,
            requester=self.requester,
            subject="Approve equipment request",
            body="Request for equipment approval.",
            receiving_department=self.department,
            status=Request.AWAITING,
            priority="high",
            current_step=step,
            current_step_started_at=timezone.now() - timedelta(days=3),
            metadata={"current_step_key": f"db:{step.pk}"},
        )

    def test_queue_merges_sources_and_respects_each_source_scope(self):
        ContactMessage.objects.create(
            name="Public sender",
            email="public@example.org",
            subject="ICT enquiry",
            message="Please direct this to ICT.",
            handling_department=self.department,
        )
        ContactMessage.objects.create(
            name="Other sender",
            email="finance@example.org",
            subject="Finance enquiry",
            message="This belongs to Finance.",
            handling_department=self.other_department,
        )
        ContactMessage.objects.create(
            name="Assigned handler",
            email="colleague@example.org",
            subject="Already assigned enquiry",
            message="Another handler owns this one.",
            handling_department=self.department,
            assigned_to=self.requester,
        )

        category = AssetCategory.objects.create(name="Laptop", code="laptop")
        asset = Asset.objects.create(
            asset_tag="ICT-1001",
            category=category,
            owner_department=self.department,
            state="in_service",
            current_custodian=self.user,
        )
        AssetAssignment.objects.create(
            asset=asset,
            assigned_to=self.user,
            assigned_by=self.requester,
            expected_return_at=timezone.localdate() - timedelta(days=4),
        )

        ticket_category = TicketCategory.objects.create(
            code="hardware",
            name="Hardware",
            handling_department=self.department,
        )
        Ticket.objects.create(
            reference="TKT-2027-00001",
            raised_by=self.requester,
            assigned_to=self.user,
            handling_department=self.department,
            category=ticket_category,
            priority="urgent",
            subject="Laptop outage",
            body="The assigned laptop is not starting.",
            state=Ticket.ESCALATED,
            escalation_level=1,
            escalated_at=timezone.now() - timedelta(days=9),
        )
        self._approval_request()

        items, _filters = build_action_queue(self.user)

        self.assertEqual(
            {item["source"] for item in items},
            {"contact", "request", "asset", "ticket"},
        )
        self.assertNotIn(
            "Finance enquiry",
            {item["title"] for item in items},
        )
        self.assertNotIn(
            "Already assigned enquiry",
            {item["title"] for item in items},
        )
        self.assertEqual(items[0]["source"], "ticket")

        self.client.force_login(self.user)
        response = self.client.get(reverse("workflow:awaiting_action"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ICT enquiry")
        self.assertContains(response, "Approve equipment request")
        self.assertContains(response, "Laptop outage")
        self.assertContains(response, "ICT-1001")

    def test_filters_are_applied_server_side(self):
        self._approval_request()

        items, filters = build_action_queue(
            self.user,
            source="request",
            priority="high",
            age="week",
            query="equipment",
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "request")
        self.assertEqual(filters["age"], "week")

    def test_bulk_approve_uses_workflow_service_and_records_actor(self):
        request = self._approval_request()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("workflow:bulk_awaiting_action"),
            {
                "request_ids": [request.pk],
                "bulk_action": "approve",
                "comment": "Reviewed and approved.",
            },
        )

        self.assertRedirects(response, reverse("workflow:awaiting_action"))
        approval = RequestApproval.objects.get(request=request)
        self.assertEqual(approval.decision, RequestApproval.APPROVE)
        self.assertEqual(approval.actor, self.user)
        self.assertEqual(approval.comment, "Reviewed and approved.")

    def test_bulk_return_requires_comment_and_rejects_another_users_request(self):
        request = self._approval_request()
        other_request = self._approval_request(
            approver=self.requester,
            reference="REQ-2027-00002",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("workflow:bulk_awaiting_action"),
            {
                "request_ids": [request.pk],
                "bulk_action": "return",
                "comment": "",
            },
        )
        self.assertRedirects(response, reverse("workflow:awaiting_action"))
        self.assertFalse(RequestApproval.objects.filter(request=request).exists())

        response = self.client.post(
            reverse("workflow:bulk_awaiting_action"),
            {
                "request_ids": [request.pk, other_request.pk],
                "bulk_action": "approve",
                "comment": "",
            },
        )
        self.assertRedirects(response, reverse("workflow:awaiting_action"))
        self.assertFalse(RequestApproval.objects.filter(
            request__in=[request, other_request],
        ).exists())

        response = self.client.post(
            reverse("workflow:bulk_awaiting_action"),
            {
                "request_ids": [request.pk],
                "bulk_action": "return",
                "comment": "Please add the missing justification.",
            },
        )
        self.assertRedirects(response, reverse("workflow:awaiting_action"))
        returned = RequestApproval.objects.get(request=request)
        self.assertEqual(returned.decision, RequestApproval.RETURN)
        request.refresh_from_db()
        self.assertEqual(request.status, Request.RETURNED)

    def test_action_queue_renders_an_empty_state_for_a_user_without_items(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("workflow:awaiting_action"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You’re all caught up")
