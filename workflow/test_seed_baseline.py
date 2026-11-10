from django.test import TestCase

from accounts.models import Role
from core.seeding import Context, Result
from messaging.models import MemoRoute, MemoRouteRule, MemoRouteStep
from messaging.workflow_seed import seed_memo_routing
from staff.models import Department
from workflow.models import ApprovalChain, ApprovalStep, RequestCategory, RequestType
from workflow.seed_sets import seed_request_catalog


class WorkflowBaselineSeedTests(TestCase):
    def setUp(self):
        self.ict, _ = Department.objects.get_or_create(code="ict", defaults={"name": "ICT"})
        self.hr, _ = Department.objects.get_or_create(
            code="hr", defaults={"name": "Human Resources"},
        )
        self.finance, _ = Department.objects.get_or_create(
            code="finance", defaults={"name": "Finance"},
        )
        for code in (
            "director",
            "deputy-director",
            "executive-secretary",
            "staff",
            "division-head",
            "office-head-ts",
            "office-head-dev",
            "office-head-admin",
            "super-admin",
        ):
            Role.objects.get_or_create(code=code, defaults={"name": code})

    @staticmethod
    def context(name):
        return Context(set_name=name, version=1, res=Result())

    def test_request_catalog_seeds_current_chains_and_types_idempotently(self):
        ctx = self.context("workflow.request_catalog")

        seed_request_catalog(ctx)
        seed_request_catalog(ctx)

        self.assertEqual(RequestCategory.objects.get(code="funds").name, "Funds")
        self.assertEqual(
            list(ApprovalStep.objects.filter(chain__code="ticket-funds-release")
                 .values_list("approver_kind", "approver_role__code", "is_hierarchical")),
            [("receiving_director", "director", False)],
        )
        self.assertTrue(
            RequestType.objects.get(code="annual-leave").requires_es_approval,
        )
        self.assertTrue(RequestType.objects.get(code="annual-leave").is_cross_department)
        self.assertEqual(
            RequestType.objects.get(code="ticket-funds").handling_department,
            self.finance,
        )
        self.assertEqual(ApprovalChain.objects.filter(code="internal-4-step").count(), 1)

        request_type = RequestType.objects.get(code="ticket-funds")
        request_type.is_active = False
        request_type.save(update_fields=["is_active"])
        seed_request_catalog(self.context("workflow.request_catalog"))
        request_type.refresh_from_db()
        self.assertFalse(request_type.is_active)

    def test_memo_routes_and_rules_seed_without_account_bound_approvers(self):
        ctx = self.context("messaging.memo_routing")

        seed_memo_routing(ctx)
        seed_memo_routing(ctx)

        self.assertEqual(MemoRoute.objects.count(), 8)
        self.assertGreaterEqual(MemoRouteRule.objects.count(), 10)
        self.assertFalse(MemoRouteStep.objects.filter(approver_user__isnull=False).exists())
        self.assertEqual(
            MemoRoute.objects.get(code="staff-to-es").steps.count(),
            3,
        )
        self.assertEqual(
            MemoRouteRule.objects.get(name="Staff memo to Director").target_role_codes,
            ["director"],
        )
