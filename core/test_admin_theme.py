"""Phase 8 admin theme: every admin page renders, the custom User admin still
works, and the shared bulk actions do what they say."""
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from notifications.models import Notification
from staff.models import Department

PW = "Pass-word-123"


class AdminThemeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.dept = Department.objects.create(name="ICT", code="ict")
        cls.root = U.objects.create_superuser("root.theme@example.org", PW)
        cls.root.must_change_password = False
        cls.root.save()
        cls.alice = U.objects.create_user("alice.theme@example.org", PW, department=cls.dept,
                                          must_change_password=False)

    def setUp(self):
        self.client.force_login(self.root)

    def test_every_model_admin_is_themed(self):
        stock = [m._meta.label for m, a in admin.site._registry.items() if not isinstance(a, UnfoldModelAdmin)]
        self.assertEqual(stock, [])

    def test_every_list_and_add_page_renders(self):
        for model in admin.site._registry:
            o = model._meta
            url = reverse(f"admin:{o.app_label}_{o.model_name}_changelist")
            self.assertEqual(self.client.get(url).status_code, 200, url)
            self.assertEqual(self.client.get(url, {"q": "x"}).status_code, 200, url)
            add = self.client.get(reverse(f"admin:{o.app_label}_{o.model_name}_add"))
            self.assertIn(add.status_code, (200, 403), o.label)

    def test_dashboard_shows_real_counts(self):
        r = self.client.get(reverse("admin:index"))
        self.assertEqual(r.status_code, 200)
        tiles = {t["key"]: t["value"] for t in r.context["nbte_tiles"]}
        self.assertEqual(tiles["users"], get_user_model().objects.filter(is_active=True).count())
        self.assertIn("NBTE Admin", r.content.decode())

    def test_user_change_and_password_pages(self):
        for name in ("admin:accounts_user_change", "admin:auth_user_password_change"):
            r = self.client.get(reverse(name, args=[self.alice.pk]))
            self.assertEqual(r.status_code, 200, name)
        r = self.client.post(reverse("admin:auth_user_password_change", args=[self.alice.pk]),
                             {"password1": "Another-pass-2026!", "password2": "Another-pass-2026!"})
        self.assertEqual(r.status_code, 302)
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.check_password("Another-pass-2026!"))

    def test_user_add_uses_the_temporary_password_flow(self):
        r = self.client.post(reverse("admin:accounts_user_add"), {
            "email": "new.theme@example.org", "first_name": "New", "last_name": "User",
            "department": self.dept.pk, "temporary_password": "Temp-pass-2026!x",
            "permission_overrides-TOTAL_FORMS": "0", "permission_overrides-INITIAL_FORMS": "0",
            "_save": "Save"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context.get("errors"))
        user = get_user_model().objects.get(email="new.theme@example.org")
        self.assertTrue(user.must_change_password)

    def test_activate_and_deactivate_users(self):
        url = reverse("admin:accounts_user_changelist")
        self.client.post(url, {"action": "deactivate_users", "_selected_action": [self.alice.pk, self.root.pk]})
        self.alice.refresh_from_db()
        self.root.refresh_from_db()
        self.assertFalse(self.alice.is_active)
        self.assertTrue(self.root.is_active)  # never lock yourself out
        self.client.post(url, {"action": "activate_users", "_selected_action": [self.alice.pk]})
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.is_active)

    def test_export_selected_to_csv(self):
        url = reverse("admin:accounts_user_changelist")
        r = self.client.post(url, {"action": "export_as_csv", "_selected_action": [self.alice.pk]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "text/csv; charset=utf-8")
        body = r.content.decode("utf-8-sig")
        self.assertIn("alice.theme@example.org", body)
        self.assertNotIn("pbkdf2", body)  # password hashes never exported

    def test_mark_notifications_read(self):
        n = Notification.objects.create(recipient=self.alice, title="hello")
        self.client.post(reverse("admin:notifications_notification_changelist"),
                         {"action": "mark_read", "_selected_action": [n.pk]})
        n.refresh_from_db()
        self.assertTrue(n.is_read)
