"""Isolation and HTTP permission checks for the independent test application."""
import os
import json
from pathlib import Path
import shutil
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
        project_directory = self.enterContext(tempfile.TemporaryDirectory())
        project = Path(project_directory)
        shutil.copytree(ROOT / "mailapi_shared", project / "mailapi_shared", ignore=shutil.ignore_patterns("__pycache__"))
        production = project / "runtime"
        production.mkdir()
        (production / "account_credentials.json").write_text('{"version": 1, "items": {}}')
        (production / "mail_relay_secret").write_text("production-mail-relay-secret-32-characters")
        (production / "site_accounts.json").write_text("{}")
        accounts = []
        for name, provider, field, relative in (
            ("account-1", "gmail", "token_file", "gmail_tokens/account-1.json"),
            ("account-2", "gmail", "token_file", "legacy/gmail.json"),
            ("account-3", "outlook", "token_file", "legacy/outlook.json"),
            ("account-4", "proton", "session_file", "proton_sessions/account-4.pickle"),
        ):
            token = production / relative
            token.parent.mkdir(parents=True, exist_ok=True)
            token.write_text(name + "-token")
            token.chmod(0o644)
            account = {"name": name, "email": name + "@example.com", "provider": provider, field: str(token)}
            if provider == "proton":
                account["delivery_mode"] = "direct"
            accounts.append(account)
        outside = project / "outside-token.json"
        outside.write_text("outside-token")
        outside.chmod(0o644)
        linked = production / "outlook_tokens/account-5.json"
        linked.parent.mkdir()
        linked.symlink_to(outside)
        accounts.append({"name": "account-5", "provider": "outlook", "token_file": str(linked)})
        accounts.append({"name": "account-6", "provider": "proton", "session_file": str(outside)})
        (production / "gmail_accounts.json").write_text(json.dumps({"items": accounts}))
        environment = dict(os.environ)
        environment.pop("TEST_DATA_DIR", None)
        environment["MAILAPI_MODE"] = "test"
        environment["TEST_EXTERNAL_WATCHERS"] = "0"
        environment["TEST_ADMIN_PASSWORD"] = "snapshot-admin-password"
        script = "\n".join([
            "import json",
            "from pathlib import Path",
            "from types import SimpleNamespace",
            "from unittest.mock import Mock, patch",
            "from mailapi_shared import application as main",
            "data = Path(main.DATA_DIR)",
            "production = Path(main.TEST_DATA_SOURCE)",
            "assert main.TEST_DATA_EPHEMERAL is True",
            "assert data != production",
            "assert (data / 'gmail_accounts.json').read_bytes() == (production / 'gmail_accounts.json').read_bytes()",
            "production_accounts = production / 'gmail_accounts.json'",
            "before_accounts = production_accounts.read_bytes()",
            "sources = json.loads(before_accounts)['items']",
            "auth_paths = [Path(main.account_auth_file(cfg)) for cfg in main.ACCOUNTS]",
            "assert auth_paths[:2] == [data / 'gmail_tokens' / (cfg['name'] + '.json') for cfg in main.ACCOUNTS[:2]]",
            "assert auth_paths[2:] == [data / path for path in ('legacy/outlook.json', 'proton_sessions/account-4.pickle', 'outlook_tokens/account-5.json', 'proton_sessions/account-6.pickle')]",
            "for path, cfg in zip(auth_paths[:4], main.ACCOUNTS[:4], strict=True):",
            "    assert path.read_text() == cfg['name'] + '-token'",
            "assert not any(path.exists() or path.is_symlink() for path in auth_paths[4:])",
            "assert not (data / 'site_accounts.json').exists()",
            "main.start_watchers()",
            "assert not main.MAIL_WATCHERS",
            "mailbox = Mock()",
            "mailbox.save_refreshed_cache.side_effect = lambda writer: writer({'cache': 'test-only'})",
            "main.save_outlook_cache(mailbox, main.ACCOUNTS[2])",
            "assert json.loads(auth_paths[2].read_text()) == {'cache': 'test-only'}",
            "proton = main.ACCOUNTS[3]",
            "credentials = SimpleNamespace(username=proton['email'], password='test-only', totp_secret='', login_type='web')",
            "client = Mock()",
            "client.save_session.side_effect = lambda filename: Path(filename).write_text('new-test-session')",
            "with patch.object(main, 'load_proton_credentials', return_value=credentials), patch.object(main.ProtonMailbox, '_types', return_value=(lambda: client, SimpleNamespace(WEB='web'), None)):",
            "    assert main.login_proton_account(proton['name']).status_code == 200",
            "assert auth_paths[3].read_text() == 'new-test-session'",
            "for source in sources[:4]:",
            "    original = Path(source.get('token_file') or source['session_file'])",
            "    assert original.read_text() == source['name'] + '-token'",
            "    assert original.stat().st_mode & 0o777 == 0o644",
            "outside = production.parent / 'outside-token.json'",
            "assert outside.read_text() == 'outside-token' and outside.stat().st_mode & 0o777 == 0o644",
            "assert production_accounts.read_bytes() == before_accounts",
            "if (production / 'mail_relay_secret').is_file(): assert (data / 'mail_relay_secret').read_bytes() != (production / 'mail_relay_secret').read_bytes()",
            "before = (production / 'account_credentials.json').read_bytes()",
            "main.CREDENTIAL_STORE.set('simulation@example.com', 'listed', False)",
            "assert (production / 'account_credentials.json').read_bytes() == before",
            "print(json.dumps({'data': str(data), 'external_watchers': main.TEST_EXTERNAL_WATCHERS}))",
        ])
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=project,
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
        for directory in (ROOT / "runtime", ROOT):
            with self.subTest(directory=directory):
                result = subprocess.run(
                    [sys.executable, "-c", "import test_app.main"],
                    cwd=ROOT,
                    env={**os.environ, "TEST_DATA_DIR": str(directory)},
                    capture_output=True, text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("TEST_DATA_DIR", result.stderr)

    def test_test_directory_rejects_custom_production_path_and_escaping_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            shutil.copytree(ROOT / "mailapi_shared", project / "mailapi_shared", ignore=shutil.ignore_patterns("__pycache__"))
            production = root / "custom-production"
            production.mkdir()
            production.chmod(0o755)
            (project / ".env").write_text(f'export MAILAPI_DATA_DIR="{production}"\n')
            test_data = root / "test-data"
            test_data.mkdir()
            (test_data / "outlook_tokens").symlink_to(production, target_is_directory=True)
            for selected in (production, production / "nested", test_data):
                with self.subTest(selected=selected):
                    result = subprocess.run(
                        [sys.executable, "-c", "import mailapi_shared.application"],
                        cwd=project,
                        env={**os.environ, "MAILAPI_MODE": "test", "TEST_DATA_DIR": str(selected)},
                        capture_output=True, text=True,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("TEST_DATA_DIR", result.stderr)
            self.assertEqual(list(production.iterdir()), [])
            self.assertEqual(production.stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
