import unittest
from datetime import datetime, timezone

from mailapi_shared.subscription_dates import (
    MAX_SUBSCRIPTION_AGE_SECONDS, public_subscription, read_subscription_claims,
)


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
        result = public_subscription(snapshot, self.now)
        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "id_token")
        self.assertEqual(result["expires_at"], self.now + 10 * 86400)
        self.assertEqual(result["checked_at"], self.now - 86400)
        self.assertNotIn("account_id", result)
        self.assertNotIn("exp", result)

    def test_exactly_seven_days_is_allowed_and_later_reads_do_not_refresh_it(self):
        snapshot = self.read(self.claims(MAX_SUBSCRIPTION_AGE_SECONDS))
        self.assertTrue(public_subscription(snapshot, self.now)["available"])
        result = public_subscription(snapshot, self.now + 1)
        self.assertFalse(result["available"])
        self.assertEqual(result["unavailable_reason"], "stale")
        self.assertIsNone(result["expires_at"])
        self.assertEqual(result["checked_at"], self.now - MAX_SUBSCRIPTION_AGE_SECONDS)
        self.assertEqual(snapshot["checked_at"], result["checked_at"])

    def test_missing_invalid_and_future_verification_dates_are_unavailable(self):
        for key, value in (("chatgpt_subscription_active_until", None),
                           ("chatgpt_subscription_active_until", "invalid"),
                           ("chatgpt_subscription_last_checked", None),
                           ("chatgpt_subscription_last_checked", "2026-11-01")):
            with self.subTest(key=key, value=value):
                claims = self.claims()
                claims["https://api.openai.com/auth"][key] = value
                self.assertFalse(public_subscription(self.read(claims), self.now)["available"])
        self.assertEqual(public_subscription(self.read(self.claims(-60)), self.now)["unavailable_reason"], "future_check_time")
        self.assertFalse(public_subscription(self.read({}), self.now)["available"])

    def test_mismatched_account_does_not_expose_its_dates(self):
        result = public_subscription(self.read(self.claims(), False), self.now)
        self.assertEqual(result["unavailable_reason"], "account_mismatch")
        self.assertIsNone(result["checked_at"])
        self.assertIsNone(result["expires_at"])

    def test_invalid_period_and_missing_subscription_fields_do_not_use_other_timestamps(self):
        claims = self.claims()
        auth = claims["https://api.openai.com/auth"]
        auth["chatgpt_subscription_active_start"] = auth["chatgpt_subscription_active_until"]
        self.assertEqual(public_subscription(self.read(claims), self.now)["unavailable_reason"], "invalid_dates")
        self.assertFalse(public_subscription(self.read({"exp": self.now + 86400}), self.now)["available"])
