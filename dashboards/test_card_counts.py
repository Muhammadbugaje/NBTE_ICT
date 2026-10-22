"""Phase 8 card audit: every dashboard number equals a direct query.

Each test builds a small, deliberately awkward data set (soft-deleted staff,
drafts, requests at every stage, a colleague who already finished their part
of a shared task, tickets the viewer may not see) and checks the card's value
against the obvious query written out by hand.
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from dashboards import counts, qa_metrics, services
from notifications.models import Notification
from staff.models import Department, Staff
from tasks.models import Task, TaskAssignee
from tickets.models import Ticket, TicketCategory
from workflow.models import ApprovalChain, ApprovalStep, Request, RequestCategory, RequestType

PW = "Pass-word-123"


class CardFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.ict = Department.objects.create(name="ICT", code="ict")
        cls.fin = Department.objects.create(name="Finance", code="finance")
        cls.admin = U.objects.create_superuser("root.cards@example.org", PW)
        cls.admin.must_change_password = False
        cls.admin.save()
        cls.alice = U.objects.create_user("alice@example.org", PW, department=cls.ict, must_change_password=False)
        cls.bob = U.objects.create_user("bob@example.org", PW, department=cls.ict, must_change_password=False)
        cls.carol = U.objects.create_user("carol@example.org", PW, department=cls.fin, must_change_password=False)
        cls.gone = U.objects.create_user("gone@example.org", PW, department=cls.fin, must_change_password=False,
                                         is_active=False)
        # One soft-deleted staff record must not count as active staff.
        Staff.objects.filter(user=cls.carol).update(is_deleted=True, deleted_at=timezone.now())

        chain = ApprovalChain.objects.create(name="Cards chain", code="cards-chain")
        cls.step = ApprovalStep.objects.create(
            chain=chain, position=1, name="Director approval", kind=ApprovalStep.KIND_APPROVAL,
            approver_kind=ApprovalStep.APPROVER_SPECIFIC, approver_user=cls.alice, is_hierarchical=False)
        cls.es_step = ApprovalStep.objects.create(
            chain=chain, position=2, name="ES approval", kind=ApprovalStep.KIND_APPROVAL,
            approver_kind=ApprovalStep.APPROVER_ES, is_hierarchical=False)
        cat = RequestCategory.objects.create(name="Cards", code="cards")
        cls.rtype = RequestType.objects.create(name="Cards type", code="cards-type", category=cat,
                                               handling_department=cls.ict, approval_chain=chain)
        n = 0

        def req(status, *, step=None, key=None, days=1):
            nonlocal n
            n += 1
            return Request.objects.create(
                reference=f"REQ-2099-{n:05d}", request_type=cls.rtype, requester=cls.bob,
                subject=f"Request {n}", body="x", receiving_department=cls.ict, status=status,
                current_step=step, current_step_started_at=timezone.now() - timedelta(days=days),
                metadata={"current_step_key": key or (f"db:{step.pk}" if step else None)})

        req(Request.DRAFT)
        req(Request.AWAITING, step=cls.step, days=10)                      # pending, aged
        req(Request.AWAITING_RECEIVING, step=cls.step)                     # pending
        req(Request.AWAITING, step=cls.es_step)                            # pending, ES (chain step)
        req(Request.AWAITING, key=counts.ES_STEP_KEY)                      # pending, ES (synthetic gate)
        req(Request.AWAITING, key=counts.FILING_STEP_KEY)                  # pending, records filing
        req(Request.IN_PROGRESS)                                           # in flight
        req(Request.READY)                                                 # in flight (handover)
        req(Request.RETURNED)
        done = req(Request.COMPLETED)
        Request.objects.filter(pk=done.pk).update(completed_at=timezone.now())

        # Tasks: one shared task where Bob has finished but Alice has not.
        cls.shared = Task.objects.create(reference="TSK-2099-00001", title="Shared", created_by=cls.admin,
                                         owner_department=cls.ict, state=Task.IN_PROGRESS)
        TaskAssignee.objects.create(task=cls.shared, user=cls.alice, state=TaskAssignee.IN_PROGRESS)
        TaskAssignee.objects.create(task=cls.shared, user=cls.bob, state=TaskAssignee.DONE)
        draft = Task.objects.create(reference="TSK-2099-00002", title="Draft", created_by=cls.admin,
                                    owner_department=cls.ict, state=Task.DRAFT)
        TaskAssignee.objects.create(task=draft, user=cls.alice)
        closed = Task.objects.create(reference="TSK-2099-00003", title="Closed", created_by=cls.admin,
                                     owner_department=cls.ict, state=Task.COMPLETED)
        TaskAssignee.objects.create(task=closed, user=cls.alice)

        # Tickets: Alice raised one open and one closed; Carol's is not hers.
        tcat = TicketCategory.objects.create(code="cards", name="Cards", handling_department=cls.fin)
        for i, (who, state) in enumerate(((cls.alice, Ticket.NEW), (cls.alice, Ticket.CLOSED),
                                          (cls.carol, Ticket.NEW)), 1):
            Ticket.objects.create(reference=f"TKT-2099-{i:05d}", raised_by=who, handling_department=cls.fin,
                                  category=tcat, subject=f"T{i}", body="x", state=state)

    def setUp(self):
        cache.clear()


class LeadershipCardTests(CardFixture):
    def test_active_staff_matches_the_nominal_roll(self):
        expected = Staff.objects.filter(is_deleted=False).count()
        self.assertGreater(expected, 0)
        for ctx in (services.superadmin_context(self.admin), services.director_context(self.admin),
                    services.deputy_context(self.admin), services.chairman_context(self.admin)):
            self.assertEqual(ctx["kpis"]["staff_total"], expected)

    def test_active_users(self):
        expected = get_user_model().objects.filter(is_active=True).count()
        self.assertEqual(services.superadmin_context(self.admin)["kpis"]["users_total"], expected)
        self.assertEqual(services.chairman_context(self.admin)["kpis"]["users_total"], expected)

    def test_staff_by_department_lists_live_staff(self):
        rows = {r["department"].code: r["count"] for r in services.chairman_context(self.admin)["dept_summary"]}
        self.assertEqual(rows.get("ict"), Staff.objects.filter(department=self.ict).count())
        # Carol's record is soft-deleted, so Finance shows only the others.
        self.assertEqual(rows.get("finance"), Staff.all_objects.filter(department=self.fin).count() - 1)

    def test_pending_in_flight_aged_completed(self):
        k = services.superadmin_context(self.admin)["kpis"]
        R = Request
        self.assertEqual(k["pending"], R.objects.filter(status__in=[R.AWAITING, R.AWAITING_RECEIVING]).count())
        self.assertEqual(k["pending"], 5)
        self.assertEqual(k["in_flight"], R.objects.filter(
            status__in=[R.IN_PROGRESS, R.AWAITING_FULFILLMENT, R.READY]).count())
        self.assertEqual(k["in_flight"], 2)
        self.assertEqual(k["aged"], R.objects.filter(
            status__in=[R.AWAITING, R.AWAITING_RECEIVING],
            current_step_started_at__lt=timezone.now() - timedelta(days=7)).count())
        self.assertEqual(k["aged"], 1)
        self.assertEqual(k["completed_30d"], 1)

    def test_es_signoff_and_filing(self):
        ctx = services.es_context(self.admin)
        self.assertEqual(ctx["kpis"]["es_pending"], 2)
        self.assertEqual(ctx["kpis"]["filing_queue"], 1)
        self.assertEqual(len(ctx["queue"]), 2)
        self.assertEqual(len(ctx["queue_filing"]), 1)

    def test_unread_for_you(self):
        Notification.objects.create(recipient=self.admin, title="a")
        Notification.objects.create(recipient=self.admin, title="b", is_read=True)
        Notification.objects.create(recipient=self.alice, title="c")
        self.assertEqual(services.superadmin_context(self.admin)["kpis"]["unread"], 1)

    def test_superadmin_page_shows_the_numbers(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("dashboards:superadmin")).content.decode()
        staff = Staff.objects.count()
        self.assertIn(f'<div class="dash-kpi-num">{staff}</div>', html)
        self.assertIn('<div class="dash-kpi-num">5</div>', html)


class PersonalCardTests(CardFixture):
    def test_my_tasks_survive_a_colleague_finishing(self):
        from core.workboard import tasks_panel

        self.assertEqual(list(counts.my_open_tasks(self.alice)), [self.shared])
        self.assertEqual(tasks_panel(self.alice)["total"], 1)
        self.assertEqual(tasks_panel(self.bob)["total"], 0)  # Bob's part is done
        widgets = {w["key"]: w for w in services.self_service_context(self.alice)["widgets"]}
        self.assertEqual(widgets["tasks"]["count"], 1)

    def test_home_cards_are_scoped_to_the_viewer(self):
        from core.views import _module_cards

        cards = {c["key"]: c for c in _module_cards(self.alice)}
        self.assertEqual(cards["tickets"]["counter"], 1)   # her open one, not every ticket
        self.assertEqual(cards["tasks"]["counter"], 1)
        self.assertEqual(cards["requests"]["counter"], 0)
        bob_cards = {c["key"]: c for c in _module_cards(self.bob)}
        self.assertEqual(bob_cards["requests"]["counter"],
                         Request.objects.filter(requester=self.bob)
                         .exclude(status__in=Request.CLOSED_STATUSES).count())

    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                                           "LOCATION": "cards-home"}})
    def test_home_numbers_refresh_after_a_change(self):
        self.client.force_login(self.alice)
        # A first visit may add the once-a-day profile reminder (Phase 8 nudges); measure after it.
        self.client.get(reverse("core:home"))
        first = self.client.get(reverse("core:home")).context["unread_notifications"]
        Notification.objects.create(recipient=self.alice, title="new")
        second = self.client.get(reverse("core:home")).context["unread_notifications"]
        self.assertEqual(second, first + 1)


class QACardTests(TestCase):
    def test_open_cases_count_every_unfinished_stage(self):
        rows = [("awaiting_sop03", 1), ("supervisors_assigned", 2), ("generate_cost", 1), ("closed", 3)]
        fake = mock.MagicMock()
        fake.order_by.return_value.values_list.return_value.annotate.return_value = rows
        with mock.patch("accreditation.models.QACase.unscoped", fake):
            out = qa_metrics.cases()
        self.assertEqual(out["open"], 4)
        self.assertEqual(out["closed"], 3)
        self.assertEqual(sum(g["count"] for g in out["by_group"]), 4)
