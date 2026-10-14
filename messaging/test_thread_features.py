from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from messaging.models import MessageRead, start_thread


class ThreadFeatureTests(TestCase):
    def setUp(self):
        self.sender = User.objects.create_user(
            "sender@example.test", "Pass-word-123", must_change_password=False,
        )
        self.recipient = User.objects.create_user(
            "recipient@example.test", "Pass-word-123", must_change_password=False,
        )
        self.thread = start_thread(
            creator=self.sender,
            participants=[self.recipient],
            initial_body="Original message",
        )
        self.original = self.thread.messages.get()

    def test_inbox_has_search_and_read_state_filters(self):
        self.client.force_login(self.sender)

        response = self.client.get("/messages/")

        self.assertContains(response, 'data-message-list="inbox"')
        self.assertContains(response, 'data-message-search')
        self.assertContains(response, 'data-message-state')
        self.assertContains(response, "Unread")

    def test_archived_list_has_search_and_state_filters(self):
        participant = self.thread.participants.get(user=self.sender)
        participant.is_archived = True
        participant.save(update_fields=["is_archived"])
        self.client.force_login(self.sender)

        response = self.client.get("/messages/archived/")

        self.assertContains(response, 'data-message-list="archive"')
        self.assertContains(response, 'data-message-search')
        self.assertContains(response, 'data-message-state')
        self.assertContains(response, "Unarchive")

    def test_destructive_message_actions_use_in_app_confirmation(self):
        self.client.force_login(self.sender)

        response = self.client.get(f"/messages/{self.thread.pk}/")

        self.assertContains(response, 'data-confirm-title="Remove this chat?"')
        self.assertContains(response, 'data-confirm-action="Delete for everyone"')
        self.assertContains(response, "js/messaging.js?v=4")
        self.assertContains(response, "js/dialog.js?v=1")

    def test_thread_messages_do_not_become_global_toasts(self):
        self.client.force_login(self.sender)
        self.client.post(
            f"/messages/{self.thread.pk}/reply/",
            {"body": ""},
        )
        response = self.client.get(f"/messages/{self.thread.pk}/")
        self.assertContains(response, "Original message")
        self.assertContains(response, "Write a message or attach a file.")
        self.assertNotContains(response, 'data-toast="', html=False)

    def test_message_can_be_edited_while_unread_within_15_minutes(self):
        self.original.sent_at = timezone.now() - timedelta(minutes=4)
        self.original.save(update_fields=["sent_at"])
        self.client.force_login(self.sender)

        response = self.client.post(
            f"/messages/{self.thread.pk}/message/{self.original.pk}/edit/",
            {"body": "Updated text"},
        )

        self.assertEqual(response.status_code, 302)
        self.original.refresh_from_db()
        self.assertEqual(self.original.body, "Updated text")
        self.assertIsNotNone(self.original.edited_at)

    def test_message_cannot_be_edited_after_read_window_expires(self):
        now = timezone.now()
        self.original.sent_at = now - timedelta(minutes=7)
        self.original.save(update_fields=["sent_at"])
        MessageRead.objects.create(
            message=self.original,
            user=self.recipient,
            read_at=now - timedelta(minutes=6),
        )
        self.client.force_login(self.sender)

        self.client.post(
            f"/messages/{self.thread.pk}/message/{self.original.pk}/edit/",
            {"body": "Must not change"},
        )

        self.original.refresh_from_db()
        self.assertEqual(self.original.body, "Original message")

    def test_read_receipt_time_does_not_shift_when_chat_is_reopened(self):
        self.client.force_login(self.recipient)
        self.client.get(f"/messages/{self.thread.pk}/")
        first_read_at = self.original.read_receipts.get(user=self.recipient).read_at

        self.client.get(f"/messages/{self.thread.pk}/")

        receipt = self.original.read_receipts.get(user=self.recipient)
        self.assertEqual(receipt.read_at, first_read_at)

    def test_delete_for_me_does_not_hide_message_from_other_participant(self):
        self.client.force_login(self.sender)
        self.client.post(
            f"/messages/{self.thread.pk}/message/{self.original.pk}/delete/",
            {"scope": "me"},
        )
        self.assertNotContains(
            self.client.get(f"/messages/{self.thread.pk}/"),
            "Original message",
        )

        other_client = self.client_class()
        other_client.force_login(self.recipient)
        self.assertContains(
            other_client.get(f"/messages/{self.thread.pk}/"),
            "Original message",
        )

    def test_delete_for_everyone_replaces_message_content(self):
        self.client.force_login(self.sender)
        response = self.client.post(
            f"/messages/{self.thread.pk}/message/{self.original.pk}/delete/",
            {"scope": "everyone"},
        )

        self.assertEqual(response.status_code, 302)
        self.original.refresh_from_db()
        self.assertTrue(self.original.is_deleted_for_everyone)
        recipient_client = self.client_class()
        recipient_client.force_login(self.recipient)
        recipient_page = recipient_client.get(f"/messages/{self.thread.pk}/")
        self.assertContains(recipient_page, "This message was deleted.")
        self.assertNotContains(recipient_page, "Original message")

    def test_reply_references_are_saved_in_the_same_thread(self):
        self.client.force_login(self.recipient)
        response = self.client.post(
            f"/messages/{self.thread.pk}/reply/",
            {"body": "A reply", "reply_to": str(self.original.pk)},
        )

        self.assertEqual(response.status_code, 302)
        reply_message = self.thread.messages.get(body="A reply")
        self.assertEqual(reply_message.reply_to_id, self.original.pk)

    def test_uploads_over_five_megabytes_are_rejected(self):
        self.client.force_login(self.sender)
        oversized = SimpleUploadedFile(
            "too-large.txt", b"x" * (5 * 1024 * 1024 + 1),
        )

        response = self.client.post(
            f"/messages/{self.thread.pk}/reply/",
            {"body": "File should not be attached", "attachments": oversized},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.thread.messages.count(), 1)
