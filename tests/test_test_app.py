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
        (production / "site_accounts.json").write_text(json.dumps({
            "version": 1,
            "users": {
                "admin-id": {
                    "id": "admin-id", "username": "admin", "role": "admin",
                    "password_hash": "pbkdf2_sha256$1$salt$hash", "created_at": 1,
                },
                "reader-id": {
                    "id": "reader-id", "username": "reader", "role": "user",
                    "password_hash": "pbkdf2_sha256$1$salt$hash", "created_at": 2,
                },
            },
            "groups": {
                "group-id": {"id": "group-id", "name": "Agent", "member_ids": ["reader-id"], "resource_keys": ["gpt:fixture"]},
            },
            "sessions": {"stale": {"user_id": "reader-id", "expires_at": 9999999999}},
        }))
        (production / "site_accounts.sqlite3").write_bytes(b"production database fixture")
        accounts = []
        for name, provider, field, relative in (
            ("account-1", "gmail", "token_file", "gmail_tokens/account-1.json"),
            ("account-2", "gmail", "token_file", "legacy/gmail.json"),
            ("account-3", "outlook", "token_file", "legacy/outlook.json"),
        ):
            token = production / relative
            token.parent.mkdir(parents=True, exist_ok=True)
            token.write_text(name + "-token")
            token.chmod(0o644)
            account = {"name": name, "email": name + "@example.com", "provider": provider, field: str(token)}
            accounts.append(account)
        outside = project / "outside-token.json"
        outside.write_text("outside-token")
        outside.chmod(0o644)
        linked = production / "outlook_tokens/account-5.json"
        linked.parent.mkdir()
        linked.symlink_to(outside)
        accounts.append({"name": "account-5", "provider": "outlook", "token_file": str(linked)})
        accounts.append({"name": "account-6", "provider": "removed-provider", "token_file": str(outside)})
        (production / "gmail_accounts.json").write_text(json.dumps({"items": accounts}))
        environment = dict(os.environ)
        environment.pop("TEST_DATA_DIR", None)
        environment["MAILAPI_MODE"] = "test"
        environment["TEST_EXTERNAL_WATCHERS"] = "0"
        environment["TEST_ADMIN_PASSWORD"] = "snapshot-fixture-entry"
        script = "\n".join([
            "import json",
            "from pathlib import Path",
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
            "assert auth_paths[2:] == [data / path for path in ('legacy/outlook.json', 'outlook_tokens/account-5.json')]",
            "for path, cfg in zip(auth_paths[:3], main.ACCOUNTS[:3], strict=True):",
            "    assert path.read_text() == cfg['name'] + '-token'",
            "assert not any(path.exists() or path.is_symlink() for path in auth_paths[3:])",
            "site_snapshot = json.loads((data / 'site_accounts.json').read_text())",
            "assert list(site_snapshot['users']) == ['admin-id', 'reader-id']",
            "assert site_snapshot['groups']['group-id']['name'] == 'Agent'",
            "assert site_snapshot['sessions'] == {}",
            "assert not (data / 'site_accounts.sqlite3').exists()",
            "with patch('mailapi_shared.site_accounts.password_is_pwned', return_value=False): main.start_watchers()",
            "assert not main.MAIL_WATCHERS",
            "mailbox = Mock()",
            "mailbox.save_refreshed_cache.side_effect = lambda writer: writer({'cache': 'test-only'})",
            "main.save_outlook_cache(mailbox, main.ACCOUNTS[2])",
            "assert json.loads(auth_paths[2].read_text()) == {'cache': 'test-only'}",
            "for source in sources[:3]:",
            "    original = Path(source.get('token_file') or source['session_file'])",
            "    assert original.read_text() == source['name'] + '-token'",
            "    assert original.stat().st_mode & 0o777 == 0o644",
            "outside = production.parent / 'outside-token.json'",
            "assert outside.read_text() == 'outside-token' and outside.stat().st_mode & 0o777 == 0o644",
            "assert production_accounts.read_bytes() == before_accounts",
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
        environment["TEST_ADMIN_PASSWORD"] = "snapshot-fixture-entry"
        script = "\n".join([
            "from unittest.mock import patch",
            "from test_app import main",
            "with patch.object(main, 'start_mail_watcher') as mail, patch.object(main.threading, 'Thread') as thread, patch('mailapi_shared.site_accounts.password_is_pwned', return_value=False):",
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

    def test_configured_snapshot_directory_is_seeded_by_test_entry(self):
        project_directory = self.enterContext(tempfile.TemporaryDirectory())
        project = Path(project_directory)
        shutil.copytree(ROOT / "mailapi_shared", project / "mailapi_shared", ignore=shutil.ignore_patterns("__pycache__"))
        production = project / "runtime"
        production.mkdir()
        (production / "site_accounts.json").write_text(json.dumps({
            "version": 1,
            "users": {"admin-id": {"id": "admin-id", "username": "admin", "role": "admin", "password_hash": "pbkdf2_sha256$1$salt$hash", "created_at": 1}},
            "groups": {},
            "sessions": {},
        }))
        (production / "gmail_accounts.json").write_text(json.dumps({"items": [{"name": "account-1", "email": "one@example.com", "provider": "gmail", "enabled": False}]}))
        test_data = project / "test-data"
        environment = {
            **os.environ,
            "MAILAPI_MODE": "test",
            "TEST_DATA_DIR": str(test_data),
            "TEST_SNAPSHOT_FROM_PRODUCTION": "1",
            "TEST_EXTERNAL_WATCHERS": "0",
            "TEST_ADMIN_PASSWORD": "snapshot-fixture-entry",
        }
        script = "\n".join([
            "import json",
            "from pathlib import Path",
            "from mailapi_shared import application as main",
            "data = Path(main.DATA_DIR)",
            "assert data == Path(__import__('os').environ['TEST_DATA_DIR'])",
            "assert json.loads((data / 'gmail_accounts.json').read_text())['items'][0]['email'] == 'one@example.com'",
            "assert json.loads((data / 'site_accounts.json').read_text())['users']['admin-id']['username'] == 'admin'",
        ])
        result = subprocess.run([sys.executable, "-c", script], cwd=project, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((test_data / "gmail_accounts.json").exists())

    def test_independent_import_ignores_production_accounts_and_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                **os.environ,
                "TEST_DATA_DIR": directory,
                "GMAIL_ACCOUNTS": '[{"name":"production-only","email":"real@example.com"}]',
                "GMAIL_OAUTH_REDIRECT_BASE": "http://localhost:8000",
                "TEST_PORT": "8001",
                "TURNSTILE_SITE_KEY": "production-site-key",
                "TURNSTILE_SECRET_KEY": "production-secret-key",
                "TEST_TURNSTILE_SITE_KEY": "",
                "TEST_TURNSTILE_SECRET_KEY": "",
            }
            result = subprocess.run(
                [sys.executable, "-c", "\n".join([
                    "from test_app import main",
                    "assert main.ACCOUNTS == []",
                    "assert main.DATA_DIR == __import__('os').environ['TEST_DATA_DIR']",
                    "assert main.GMAIL_OAUTH_REDIRECT_BASE == 'http://localhost:8001'",
                    "assert main.AccountCredentialStore.__module__ == 'mailapi_shared.account_credentials'",
                    "assert main.CODEX_LOGIN_SERVER is None",
                    "assert main.login_verifier is None",
                    "assert isinstance(main.should_use_console_auth(), bool)",
                    "assert (main.STATIC_DIR / 'index.html').is_file()",
                ])],
                cwd=ROOT, env=environment, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_turnstile_configuration_is_scoped_and_incomplete_configuration_fails_startup(self):
        from mailapi_shared.login_protection import TEST_SECRET_KEY, TEST_SITE_KEY

        with tempfile.TemporaryDirectory() as directory:
            environment = {
                **os.environ, "MAILAPI_MODE": "test", "TEST_DATA_DIR": directory,
                "TEST_SNAPSHOT_FROM_PRODUCTION": "0", "TEST_EXTERNAL_WATCHERS": "0",
                "TEST_APP_BASE_URL": "http://localhost:8001",
                "TURNSTILE_SITE_KEY": "production-site-key", "TURNSTILE_SECRET_KEY": "production-secret-key",
                "TEST_TURNSTILE_SITE_KEY": TEST_SITE_KEY, "TEST_TURNSTILE_SECRET_KEY": TEST_SECRET_KEY,
            }
            script = "\n".join([
                "from test_app import main",
                "assert main.login_verifier.testing is True",
                "assert main.login_verifier._hostname == 'localhost'",
            ])
            result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=environment,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            environment["TEST_TURNSTILE_SECRET_KEY"] = ""
            result = subprocess.run([sys.executable, "-c", "from test_app import main"], cwd=ROOT, env=environment,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("启用登录验证必须同时配置", result.stderr)
            self.assertNotIn("production-secret-key", result.stderr)

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
