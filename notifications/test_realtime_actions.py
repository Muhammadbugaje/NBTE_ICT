from django.test import Client, TestCase, override_settings

from accounts.models import User
from notifications import engine
from notifications.models import Notification


class NotificationActionTests(TestCase):
    def test_read_actions_return_json_state_without_redirect(self):
        user = User.objects.create_user(
            "notify@x.org", "Pass-word-123", must_change_password=False,
        )
        notification = Notification.objects.create(
            recipient=user,
            title="A notification",
            body="The item body",
        )
        client = Client()
        client.force_login(user)

        read_response = client.post(
            f"/notifications/{notification.pk}/read/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(read_response.json(), {
            "ok": True,
            "unread": 0,
            "id": notification.pk,
            "is_read": True,
        })

        unread_response = client.post(
            f"/notifications/{notification.pk}/unread/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            HTTP_ACCEPT="application/json",
        )
        self.assertEqual(unread_response.status_code, 200)
        self.assertEqual(unread_response.json(), {
            "ok": True,
            "unread": 1,
            "id": notification.pk,
            "is_read": False,
        })

    def test_email_routing_skips_unconfigured_builtin_gmail(self):
        with override_settings(
            EMAIL_PROVIDERS={
                "brevo": {"BACKEND": "anymail.backends.brevo.EmailBackend"},
                "gmail": {
                    "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
                    "OPTIONS": {"host": "smtp.gmail.com", "username": "", "password": ""},
                },
            },
            ANYMAIL={"BREVO_API_KEY": "configured-for-test"},
        ):
            self.assertEqual(engine.provider_order("general"), ["brevo"])
