"""Isolation and HTTP permission checks for the independent test application."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestAppIsolationTests(unittest.TestCase):
    def test_production_and_test_import_paths_use_shared_service_modules(self):
        import account_credentials
        import account_passwords
        import auth_storage
        import mail_messages
        import mail_providers.outlook as production_outlook
        import resource_accounts
        import test_app.services.account_credentials as test_credentials
        import test_app.services.account_passwords as test_passwords
        import test_app.services.auth_storage as test_storage
        import test_app.services.mail_messages as test_messages
        import test_app.services.mail_providers.outlook as test_outlook
        import test_app.services.resource_accounts as test_resources

        self.assertIs(account_credentials, test_credentials)
        self.assertIs(account_passwords, test_passwords)
        self.assertIs(auth_storage, test_storage)
        self.assertIs(mail_messages, test_messages)
        self.assertIs(production_outlook, test_outlook)
        self.assertIs(resource_accounts, test_resources)

    def test_default_startup_snapshots_production_data_and_cleans_it_on_exit(self):
        environment = dict(os.environ)
        environment.pop("TEST_DATA_DIR", None)
        environment["TEST_EXTERNAL_WATCHERS"] = "0"
        environment["TEST_ADMIN_PASSWORD"] = "snapshot-admin-password"
        script = "\n".join([
            "import json",
            "from pathlib import Path",
            "from test_app import main",
            "data = Path(main.DATA_DIR)",
            "production = Path(main.TEST_DATA_SOURCE)",
            "assert main.TEST_DATA_EPHEMERAL is True",
            "assert data != production",
            "assert (data / 'gmail_accounts.json').read_bytes() == (production / 'gmail_accounts.json').read_bytes()",
            "assert not (data / 'site_accounts.json').exists()",
            "if (production / 'mail_relay_secret').is_file(): assert (data / 'mail_relay_secret').read_bytes() != (production / 'mail_relay_secret').read_bytes()",
            "before = (production / 'account_credentials.json').read_bytes()",
            "main.CREDENTIAL_STORE.set('simulation@example.com', 'listed', False)",
            "assert (production / 'account_credentials.json').read_bytes() == before",
            "print(json.dumps({'data': str(data), 'external_watchers': main.TEST_EXTERNAL_WATCHERS}))",
        ])
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        data_path = Path(json.loads(result.stdout.strip().splitlines()[-1])["data"])
        self.assertFalse(data_path.exists())

    def test_default_startup_does_not_start_external_watchers(self):
        environment = dict(os.environ)
        environment.pop("TEST_DATA_DIR", None)
        environment["TEST_EXTERNAL_WATCHERS"] = "0"
        environment["TEST_ADMIN_PASSWORD"] = "snapshot-admin-password"
        script = "\n".join([
            "from unittest.mock import patch",
            "from test_app import main",
            "with patch.object(main, 'start_mail_watcher') as mail, patch.object(main.threading, 'Thread') as thread:",
            "    main.start_watchers()",
            "    assert not mail.called",
            "    assert not thread.called",
        ])
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_independent_import_ignores_production_accounts_and_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                **os.environ,
                "TEST_DATA_DIR": directory,
                "GMAIL_ACCOUNTS": '[{"name":"production-only","email":"real@example.com"}]',
                "MAIL_RELAY_SECRET": "production-secret-must-not-be-reused",
                "MAIL_RELAY_SECRET_FILE": str(Path(directory) / "production-secret"),
                "GMAIL_OAUTH_REDIRECT_BASE": "http://localhost:8000",
                "TEST_PORT": "8001",
            }
            result = subprocess.run(
                [sys.executable, "-c", "\n".join([
                    "from test_app import main",
                    "assert main.ACCOUNTS == []",
                    "assert main.DATA_DIR == __import__('os').environ['TEST_DATA_DIR']",
                    "assert main.GMAIL_OAUTH_REDIRECT_BASE == 'http://localhost:8001'",
                    "assert main.MAIL_RELAY_SECRET != 'production-secret-must-not-be-reused'",
                    "assert main.MAIL_RELAY_SECRET_FILE.endswith('/mail_relay_secret')",
                    "assert main.AccountCredentialStore.__module__ == 'mailapi_shared.account_credentials'",
                    "assert main.CODEX_LOGIN_SERVER is None",
                    "assert isinstance(main.should_use_console_auth(), bool)",
                    "assert (main.STATIC_DIR / 'index.html').is_file()",
                ])],
                cwd=ROOT, env=environment, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_runtime_cannot_be_selected(self):
        result = subprocess.run(
            [sys.executable, "-c", "import test_app.main"],
            cwd=ROOT,
            env={**os.environ, "TEST_DATA_DIR": str(ROOT / "runtime")},
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TEST_DATA_DIR", result.stderr)


if __name__ == "__main__":
    unittest.main()
