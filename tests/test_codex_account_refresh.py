import base64
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class CodexAccountRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.previous_directory = os.getcwd()
        temporary_path = Path(cls.temporary_directory.name)
        (temporary_path / "static").mkdir()
        os.chdir(temporary_path)
        try:
            with patch.dict(
                os.environ,
                {
                    "ACCESS_PASSWORD": "test-access-password",
                    "MAIL_RELAY_SECRET": "test-mail-relay-secret-32-characters",
                },
            ):
                spec = importlib.util.spec_from_file_location(
                    "mailapi_code_receive_for_tests",
                    ROOT / "code-receive.py",
                )
                cls.module = importlib.util.module_from_spec(spec)
                assert spec.loader is not None
                spec.loader.exec_module(cls.module)
        finally:
            os.chdir(cls.previous_directory)

    @classmethod
    def tearDownClass(cls):
        for handler in list(cls.module.ERROR_LOGGER.handlers):
            handler.close()
            cls.module.ERROR_LOGGER.removeHandler(handler)
        cls.temporary_directory.cleanup()

    def test_manual_endpoint_forces_token_and_subscription_refresh(self):
        record = {
            "id": "account-id",
            "email": "user@example.com",
            "chatgpt_plan_type": "free",
            "token": {"access_token": "old-access-token"},
        }
        calls = []

        def fake_refresh(value, force_subscription=False, force_token_refresh=False):
            calls.append((force_subscription, force_token_refresh))
            return value

        with patch.object(self.module, "CODEX_ACCOUNTS", {"account-id": record}), patch.object(
            self.module,
            "refresh_codex_account_record",
            side_effect=fake_refresh,
        ), patch.object(self.module, "save_codex_accounts"):
            response = self.module.refresh_codex_account("account-id")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [(True, True)])

    def test_token_refresh_reloads_plan_from_new_access_token(self):
        claims = {
            "sub": "user-id",
            "email": "user@example.com",
            "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
        }
        encoded_claims = base64.urlsafe_b64encode(
            json.dumps(claims).encode("utf-8")
        ).decode("ascii").rstrip("=")
        new_access_token = f"header.{encoded_claims}.signature"
        record = {
            "id": "account-id",
            "email": "user@example.com",
            "chatgpt_plan_type": "free",
            "token": {
                "access_token": "old-access-token",
                "refresh_token": "old-refresh-token",
            },
        }

        with patch.object(
            self.module,
            "http_json",
            return_value={
                "access_token": new_access_token,
                "refresh_token": "new-refresh-token",
            },
        ), patch.object(self.module, "save_codex_record_auth_file"):
            refreshed = self.module.refresh_codex_access_token(record)

        self.assertTrue(refreshed)
        self.assertEqual(record["chatgpt_plan_type"], "pro")
        self.assertEqual(record["token"]["refresh_token"], "new-refresh-token")

    def test_forced_token_refresh_updates_plan_when_subscription_is_blocked(self):
        record = {
            "id": "account-id",
            "email": "user@example.com",
            "chatgpt_account_id": "chatgpt-account-id",
            "workspace_id": "workspace-id",
            "chatgpt_plan_type": "free",
            "token": {
                "access_token": "old-access-token",
                "refresh_token": "refresh-token",
            },
        }

        def fake_token_refresh(value):
            value["token"]["access_token"] = "new-access-token"
            value["chatgpt_plan_type"] = "pro"
            return True

        usage = {
            "used_percent": 1.0,
            "window_minutes": 10080,
            "resets_at": 1234567890,
            "credits": None,
            "captured_at": 1234567890,
        }
        with patch.object(
            self.module,
            "refresh_codex_access_token",
            side_effect=fake_token_refresh,
        ) as token_refresh, patch.object(
            self.module,
            "fetch_codex_subscription",
            side_effect=RuntimeError(" status 403 Forbidden"),
        ), patch.object(
            self.module,
            "fetch_codex_usage",
            return_value=usage,
        ):
            updated = self.module.refresh_codex_account_record(
                record,
                force_subscription=True,
                force_token_refresh=True,
            )

        token_refresh.assert_called_once()
        self.assertEqual(updated["chatgpt_plan_type"], "pro")
        self.assertIn("403", updated["subscription_error"])


if __name__ == "__main__":
    unittest.main()
