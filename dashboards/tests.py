from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from staff.models import Department
from tasks.models import Task, TaskAssignee
from tickets.models import Ticket, TicketCategory


class SelfServiceDashboardTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name="ICT", code="ict")
        self.other_department = Department.objects.create(
            name="Finance", code="finance"
        )
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            "staff@example.org",
            "Pass-word-123",
            department=self.department,
            must_change_password=False,
        )
        self.other_user = user_model.objects.create_user(
            "other@example.org",
            "Pass-word-123",
            department=self.other_department,
            must_change_password=False,
        )
        self.staff = self.user.staff_profile
        self.staff.employee_no = "NBTE-001"
        self.staff.first_name = "Staff"
        self.staff.last_name = "Member"
        self.staff.save()
        self.client.force_login(self.user)

    def test_dashboard_requires_authentication(self):
        self.client.logout()
        response = self.client.get(reverse("dashboards:self_service"))
        self.assertEqual(response.status_code, 302)

    def test_dashboard_only_lists_owned_tickets_and_assigned_tasks(self):
        category = TicketCategory.objects.create(
            code="general",
            name="General",
            handling_department=self.department,
        )
        mine = Ticket.objects.create(
            reference="TKT-2026-00001",
            raised_by=self.user,
            handling_department=self.department,
            category=category,
            subject="My private ticket",
            body="Personal ticket details",
        )
        Ticket.objects.create(
            reference="TKT-2026-00002",
            raised_by=self.other_user,
            handling_department=self.other_department,
            category=category,
            subject="Another department ticket",
            body="Must not appear",
        )
        my_task = Task.objects.create(
            reference="TSK-2026-00001",
            title="My assigned task",
            created_by=self.user,
            owner_department=self.department,
            state=Task.OPEN,
        )
        TaskAssignee.objects.create(task=my_task, user=self.user)
        other_task = Task.objects.create(
            reference="TSK-2026-00002",
            title="Another department task",
            created_by=self.other_user,
            owner_department=self.other_department,
            state=Task.OPEN,
        )
        TaskAssignee.objects.create(task=other_task, user=self.other_user)

        response = self.client.get(reverse("dashboards:self_service"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, mine.subject)
        self.assertContains(response, my_task.title)
        self.assertNotContains(response, "Another department ticket")
        self.assertNotContains(response, "Another department task")
        self.assertContains(response, reverse("tickets:my_tickets"))
        self.assertContains(response, reverse("tasks:assigned_to_me"))
