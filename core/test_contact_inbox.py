from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from accounts.models import UserPermissionOverride
from core.models import ContactMessage, ContactMessageActivity
from staff.models import Department, Staff


class PublicContactInboxTests(TestCase):
    def setUp(self):
        self.department = Department.objects.create(name="ICT", code="ict")
        self.other_department = Department.objects.create(
            name="Finance",
            code="finance",
        )
        self.manager = self._make_user("handler@example.org", self.department)
        self.assignee = self._make_user("assignee@example.org", self.department)
        self.other_handler = self._make_user(
            "finance@example.org",
            self.other_department,
        )
        self._grant_contact_permission(self.manager)
        self._grant_contact_permission(self.assignee)
        self._grant_contact_permission(self.other_handler)

    def _make_user(self, email, department):
        return get_user_model().objects.create_user(
            email,
            "Pass-word-123",
            department=department,
            must_change_password=False,
        )

    def _grant_contact_permission(self, user):
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="can_handle_contact_messages",
        )
        UserPermissionOverride.objects.create(
            user=user,
            permission=permission,
            effect=UserPermissionOverride.GRANT,
        )

    def _message(self, **overrides):
        defaults = {
            "name": "External sender",
            "email": "sender@example.org",
            "subject": "A public enquiry",
            "message": "Please help with this enquiry.",
            "handling_department": self.department,
        }
        defaults.update(overrides)
        message = ContactMessage.objects.create(**defaults)
        ContactMessageActivity.objects.create(
            contact_message=message,
            action=ContactMessageActivity.ACTION_RECEIVED,
            details="Submitted through the public contact form.",
        )
        return message

    def test_public_submission_creates_a_message_without_creating_a_staff_account(self):
        user_count = get_user_model().objects.count()
        staff_count = Staff.objects.count()
        with patch("core.email_utils.send_templated_email"):
            response = self.client.post(reverse("core:contact_submit"), {
                "name": "External sender",
                "email": "sender@example.org",
                "subject": "Question",
                "message": "I need help with a public service.",
                "handling_department": self.department.pk,
                "website": "",
            })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        message = ContactMessage.objects.get(email="sender@example.org")
        self.assertEqual(message.handling_department, self.department)
        self.assertEqual(get_user_model().objects.count(), user_count)
        self.assertEqual(Staff.objects.count(), staff_count)
        self.assertTrue(
            message.activities.filter(
                action=ContactMessageActivity.ACTION_RECEIVED
            ).exists()
        )

    def test_inbox_requires_explicit_permission(self):
        inbox_url = reverse("core:contact_inbox")
        self.assertEqual(self.client.get(inbox_url).status_code, 302)

        unprivileged = self._make_user("staff@example.org", self.department)
        self.client.force_login(unprivileged)
        self.assertEqual(self.client.get(inbox_url).status_code, 403)

    def test_inbox_and_detail_are_scoped_to_department(self):
        own_message = self._message()
        other_message = self._message(
            email="finance-sender@example.org",
            handling_department=self.other_department,
        )
        self.client.force_login(self.manager)

        response = self.client.get(reverse("core:contact_inbox"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, own_message.subject)
        self.assertNotContains(response, other_message.email)
        self.assertEqual(
            self.client.get(
                reverse("core:contact_detail", kwargs={"pk": other_message.pk})
            ).status_code,
            404,
        )

    def test_assignment_read_handled_reopen_and_private_notes_are_recorded(self):
        message = self._message()
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("core:contact_assign", kwargs={"pk": message.pk}),
            {"assigned_to": self.assignee.pk},
        )
        self.assertEqual(response.status_code, 302)
        message.refresh_from_db()
        self.assertEqual(message.assigned_to, self.assignee)

        self.client.post(
            reverse("core:contact_mark_read", kwargs={"pk": message.pk})
        )
        message.refresh_from_db()
        self.assertTrue(message.is_read)

        self.client.post(
            reverse("core:contact_update_status", kwargs={"pk": message.pk}),
            {"action": "handled"},
        )
        message.refresh_from_db()
        self.assertTrue(message.is_handled)
        self.assertEqual(message.handled_by, self.manager)
        self.assertIsNotNone(message.handled_at)

        self.client.post(
            reverse("core:contact_add_note", kwargs={"pk": message.pk}),
            {"details": "Called the sender and requested more details."},
        )
        response = self.client.get(
            reverse("core:contact_detail", kwargs={"pk": message.pk})
        )
        self.assertContains(response, "Called the sender and requested more details.")
        self.assertContains(response, "Nothing in this history is shown to the sender.")

        response = self.client.post(
            reverse("core:contact_update_status", kwargs={"pk": message.pk}),
            {"action": "reopen"},
        )
        self.assertEqual(response.status_code, 302)
        message.refresh_from_db()
        self.assertFalse(message.is_handled)
        self.assertIsNone(message.handled_by)
        self.assertIsNone(message.handled_at)
        self.assertEqual(
            message.activities.count(),
            6,
        )
