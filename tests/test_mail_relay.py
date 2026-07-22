import json
import unittest

from mail_relay import (
    RelayAuthenticationError,
    RelayMessage,
    RelayValidationError,
    ReplayGuard,
    encode_message,
    parse_message,
    sign_request,
    verify_request,
)


class MailRelayTests(unittest.TestCase):
    def setUp(self):
        self.secret = "a" * 64
        self.timestamp = 1_800_000_000
        self.nonce = "nonce_1234567890abcdef"
        self.message = RelayMessage(
            account_name="account-4",
            code="123456",
            message_id="proton-message-1",
            subject="Your temporary ChatGPT login code",
        )

    def test_signed_message_verifies_and_parses(self):
        body = encode_message(self.message)
        signature = sign_request(self.secret, self.timestamp, self.nonce, body)
        verified_at = verify_request(
            self.secret,
            str(self.timestamp),
            self.nonce,
            signature,
            body,
            now=self.timestamp,
        )
        self.assertEqual(verified_at, self.timestamp)
        self.assertEqual(parse_message(json.loads(body)), self.message)

    def test_tampered_body_is_rejected(self):
        body = encode_message(self.message)
        signature = sign_request(self.secret, self.timestamp, self.nonce, body)
        with self.assertRaises(RelayAuthenticationError):
            verify_request(
                self.secret,
                str(self.timestamp),
                self.nonce,
                signature,
                body.replace(b"123456", b"654321"),
                now=self.timestamp,
            )

    def test_expired_request_is_rejected(self):
        body = encode_message(self.message)
        signature = sign_request(self.secret, self.timestamp, self.nonce, body)
        with self.assertRaises(RelayAuthenticationError):
            verify_request(
                self.secret,
                str(self.timestamp),
                self.nonce,
                signature,
                body,
                now=self.timestamp + 301,
            )

    def test_nonce_can_only_be_consumed_once(self):
        guard = ReplayGuard()
        self.assertTrue(guard.consume(self.nonce, self.timestamp, now=self.timestamp))
        self.assertFalse(guard.consume(self.nonce, self.timestamp, now=self.timestamp))

    def test_invalid_code_is_rejected(self):
        value = self.message.as_dict()
        value["code"] = "<script>"
        with self.assertRaises(RelayValidationError):
            parse_message(value)


if __name__ == "__main__":
    unittest.main()
