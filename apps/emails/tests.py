from __future__ import annotations

from unittest.mock import patch

from django.core import mail
from django.test import TestCase

from apps.emails.exceptions import EmailSuppressedError
from apps.emails.models import EmailLog, EmailSuppression
from apps.emails.senders import OutboundEmail, send_outbound_email
from apps.emails.suppression import get_active_suppression, is_email_suppressed, normalize_email


class EmailSuppressionServiceTests(TestCase):
    def test_normalize_email_lowercases_and_trims(self):
        self.assertEqual(normalize_email("  User@Example.COM "), "user@example.com")

    def test_get_active_suppression_ignores_inactive(self):
        EmailSuppression.objects.create(
            email="blocked@example.com",
            reason=EmailSuppression.Reason.MANUAL,
            active=False,
        )

        self.assertIsNone(get_active_suppression("blocked@example.com"))
        self.assertFalse(is_email_suppressed("blocked@example.com"))


class SendOutboundEmailTests(TestCase):
    def test_send_outbound_email_creates_sent_email_log(self):
        email_log = send_outbound_email(
            OutboundEmail(
                recipient="user@example.com",
                subject="Test subject",
                text_body="Hello",
                html_body="<p>Hello</p>",
                notification_type="test",
                template_key="test/template",
                metadata={"source": "unit-test"},
            ),
        )

        self.assertEqual(email_log.status, EmailLog.Status.SENT)
        self.assertIsNotNone(email_log.sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["user@example.com"])
        self.assertEqual(mail.outbox[0].subject, "Test subject")

    def test_send_outbound_email_raises_for_suppressed_recipient(self):
        EmailSuppression.objects.create(
            email="blocked@example.com",
            reason=EmailSuppression.Reason.HARD_BOUNCE,
            active=True,
        )

        with self.assertRaises(EmailSuppressedError) as exc_info:
            send_outbound_email(
                OutboundEmail(
                    recipient="blocked@example.com",
                    subject="Test",
                    text_body="Hello",
                    notification_type="test",
                ),
            )

        self.assertEqual(exc_info.exception.reason, EmailSuppression.Reason.HARD_BOUNCE)
        email_log = EmailLog.objects.get(recipient="blocked@example.com")
        self.assertEqual(email_log.status, EmailLog.Status.SUPPRESSED)
        self.assertEqual(email_log.error, "suppressed:HARD_BOUNCE")
        self.assertEqual(len(mail.outbox), 0)

    def test_send_outbound_email_marks_failed_on_smtp_error(self):
        with patch("apps.emails.senders.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            with self.assertRaises(OSError):
                send_outbound_email(
                    OutboundEmail(
                        recipient="user@example.com",
                        subject="Test",
                        text_body="Hello",
                    ),
                )

        email_log = EmailLog.objects.get(recipient="user@example.com")
        self.assertEqual(email_log.status, EmailLog.Status.FAILED)
        self.assertIn("smtp down", email_log.error)

    def test_send_outbound_email_requires_recipient(self):
        with self.assertRaises(ValueError):
            send_outbound_email(
                OutboundEmail(
                    recipient="   ",
                    subject="Test",
                    text_body="Hello",
                ),
            )
