"""Isolation and HTTP permission checks for the independent test application."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestAppIsolationTests(unittest.TestCase):
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
                    "assert main.AccountCredentialStore.__module__.startswith('test_app.services.')",
                    "main.ensure_codex_login_server()",
                    "assert main.CODEX_LOGIN_SERVER is None",
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
