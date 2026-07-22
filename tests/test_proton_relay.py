import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.proton_relay import RelayDeliveryError, process_message


class FakeMailbox:
    def __init__(self):
        self.marked = []

    def read_message(self, message):
        return message

    def mark_as_read(self, message_id):
        self.marked.append(message_id)


class ProtonRelayTests(unittest.TestCase):
    def setUp(self):
        self.mailbox = FakeMailbox()
        self.message = SimpleNamespace(
            id="message-1",
            unread=True,
            sender=SimpleNamespace(address="noreply@tm.openai.com"),
            subject="Your temporary ChatGPT login code",
            body="Your login code is 123456.",
            time=1_800_000_000,
        )
        self.options = {
            "account_name": "account-4",
            "server_url": "https://example.com/api/internal/mail-relay",
            "relay_secret": "a" * 64,
            "allowed_senders": {"noreply@tm.openai.com"},
            "timeout": 15,
        }

    @patch("scripts.proton_relay.deliver_message")
    def test_marks_message_read_after_successful_delivery(self, deliver):
        with patch("builtins.print"):
            processed = process_message(self.mailbox, self.message, **self.options)
        self.assertTrue(processed)
        self.assertEqual(self.mailbox.marked, ["message-1"])
        deliver.assert_called_once()

    @patch("scripts.proton_relay.deliver_message")
    def test_keeps_message_unread_when_delivery_fails(self, deliver):
        deliver.side_effect = RelayDeliveryError("server unavailable")
        with self.assertRaises(RelayDeliveryError):
            process_message(self.mailbox, self.message, **self.options)
        self.assertEqual(self.mailbox.marked, [])


if __name__ == "__main__":
    unittest.main()
