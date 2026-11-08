import unittest
from datetime import datetime, timezone

from mailapi_shared.subscription_dates import public_subscription, read_subscription_claims


class SubscriptionDateTests(unittest.TestCase):
    now = 1794000000

    def claims(self, age=86400):
        def iso(value):
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        return {"exp": self.now - 3600, "https://api.openai.com/auth": {
            "chatgpt_subscription_active_start": iso(self.now - 20 * 86400),
            "chatgpt_subscription_active_until": iso(self.now + 10 * 86400),
            "chatgpt_subscription_last_checked": iso(self.now - age),
        }}

    def read(self, claims, matches=True):
        return read_subscription_claims(claims, "account-id", "workspace-id", matches)

    def test_expired_identity_token_can_supply_informational_dates(self):
        snapshot = self.read(self.claims())
        result = public_subscription(snapshot)
        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "id_token")
        self.assertEqual(result["expires_at"], self.now + 10 * 86400)
        self.assertEqual(result["checked_at"], self.now - 86400)
        self.assertNotIn("account_id", result)
        self.assertNotIn("exp", result)

    def test_old_verification_dates_remain_available_without_resetting_the_time(self):
        for age in (7 * 86400 + 1, 365 * 86400):
            with self.subTest(age=age):
                snapshot = self.read(self.claims(age))
                result = public_subscription(snapshot)
                self.assertTrue(result["available"])
                self.assertIsNone(result["unavailable_reason"])
                self.assertEqual(result["expires_at"], self.now + 10 * 86400)
                self.assertEqual(result["checked_at"], self.now - age)
                self.assertEqual(snapshot["checked_at"], result["checked_at"])

    def test_missing_or_invalid_expiry_dates_are_unavailable(self):
        for value in (None, "invalid", "2026-11-01"):
            with self.subTest(value=value):
                claims = self.claims()
                claims["https://api.openai.com/auth"]["chatgpt_subscription_active_until"] = value
                self.assertFalse(public_subscription(self.read(claims))["available"])
        self.assertFalse(public_subscription(self.read({}))["available"])

    def test_verification_time_is_optional_information(self):
        for value in (None, "invalid", "2026-11-01"):
            with self.subTest(value=value):
                claims = self.claims()
                claims["https://api.openai.com/auth"]["chatgpt_subscription_last_checked"] = value
                result = public_subscription(self.read(claims))
                self.assertTrue(result["available"])
                self.assertIsNone(result["checked_at"])
                self.assertEqual(result["expires_at"], self.now + 10 * 86400)
        self.assertTrue(public_subscription(self.read(self.claims(-60)))["available"])

    def test_mismatched_account_does_not_expose_its_dates(self):
        result = public_subscription(self.read(self.claims(), False))
        self.assertEqual(result["unavailable_reason"], "account_mismatch")
        self.assertIsNone(result["checked_at"])
        self.assertIsNone(result["expires_at"])

    def test_invalid_period_and_missing_subscription_fields_do_not_use_other_timestamps(self):
        claims = self.claims()
        auth = claims["https://api.openai.com/auth"]
        auth["chatgpt_subscription_active_start"] = auth["chatgpt_subscription_active_until"]
        self.assertEqual(public_subscription(self.read(claims))["unavailable_reason"], "invalid_dates")
        self.assertFalse(public_subscription(self.read({"exp": self.now + 86400}))["available"])
