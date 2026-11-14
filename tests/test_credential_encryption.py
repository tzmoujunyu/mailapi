from base64 import b64encode
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import pyotp

from mailapi_shared.account_credentials import AccountCredentialStore
from mailapi_shared.credential_crypto import CredentialCryptoError, CredentialKeyring
from mailapi_shared.resource_accounts import ResourceStore
from mailapi_shared.resource_database import DATABASE_NAME as RESOURCE_NAME, GPT, ResourceDatabase, migrate_resources
from mailapi_shared.site_database import DATABASE_NAME as SITE_NAME, migrate_site_accounts, open_site_accounts
from mailapi_shared.site_accounts import SiteAccountStore
from mailapi_shared.test_credential_storage import LEGACY_RESTORE_KEY, copy_backup, rewrite


ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "fixture-website-entry-24"
GPT_PASSWORD = "fixture-gpt-current-pass"
OLD_PASSWORD = "fixture-gpt-original-pass"
PLAN_KEY = "fixture-plan-private-key"
RELAY_PASSWORD = "fixture-relay-private-password"
ADMIN_TOTP = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
GPT_TOTP = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


class EncryptedCredentialTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.source = self.root / "source"
        self.source.mkdir(mode=0o700)
        self.before = self.root / "encrypted-v1"
        self.after = self.root / "encrypted-v2"
        self.backup = self.root / "backup"
        self.restored = self.root / "restored"
        self.key1 = self._create_key("v1")
        self.key2 = self._create_key("v2")

        admin = {"source": "admin", "name": "admin", "id": "admin-fixture"}
        member = {"source": "user", "name": "alice", "id": "member-fixture"}
        self.gpt = AccountCredentialStore(self.source / GPT)
        self.gpt.migrate([], None, None, {})
        self.gpt.migrate_pool([], [])
        self.gpt_account = self.gpt.save_account(None, {
            "login": "gpt@example.test", "password": OLD_PASSWORD, "secret": GPT_TOTP, "listed": True,
        }, {}, actor=admin)
        key = self.gpt_account["key"]
        self.gpt.save_account(key, {"password": GPT_PASSWORD}, {}, actor=member)
        self.change = self.gpt.history(key)["items"][0]["id"]
        self.gpt.record_codex_change(key, admin, "codex:fixture", {
            "before": {"token": {"refresh_token": "fixture-oauth-archived"}}, "email": "gpt@example.test", "version": "dummy",
        }, "Codex fixture")

        plan = ResourceStore(self.source / "coding_plans.json", "api_key", "API Key")
        self.plan_id = plan.create({"name": "Fixture plan", "provider": "kimi", "api_key": PLAN_KEY})["id"]
        relay = ResourceStore(self.source / "relay_accounts.json", "password", "密码")
        self.relay_id = relay.create({"name": "Fixture relay", "url": "https://relay.example.test",
                                     "account": "test", "password": RELAY_PASSWORD})["id"]
        (self.source / "gmail_accounts.json").write_text('{"version":1,"items":[]}')
        (self.source / "codex_accounts.json").write_text(json.dumps({"items": [
            {"id": "codex-test", "token": {"refresh_token": "fixture-oauth-current"}},
        ]}))
        (self.source / "codex_auth").mkdir()
        (self.source / "codex_auth" / "unchanged.fixture").write_bytes(b"synthetic authorization file")
        (self.source / "outlook_tokens").mkdir()
        (self.source / "outlook_tokens" / "sample.json").write_bytes(b'{"fixture":"outlook OAuth"}')

        site = SiteAccountStore(self.source / "site_accounts.json")
        with patch("mailapi_shared.site_accounts.password_is_pwned", return_value=False):
            site.provision_admin("admin", PASSWORD, ADMIN_TOTP)
            _, admin_user = site.login("admin", PASSWORD, pyotp.TOTP(ADMIN_TOTP).now())
            alice = site.create_user(admin_user["id"], "alice", PASSWORD)
            site.create_user(admin_user["id"], "bob", PASSWORD)
        # Preserve a legacy encoded verifier through migration as well.
        with site._edit() as data:
            self.timestamp = (data["users"][admin_user["id"]]["totp_last_step"] + 2) * 30
            salt = "legacy-fixture-salt"
            digest = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode(), salt.encode(), 600000).hex()
            data["users"][alice["id"]]["password_hash"] = f"pbkdf2_sha256$600000${salt}${digest}"
        resources = ["gpt:" + key, "coding-plan:" + self.plan_id, "relay-account:" + self.relay_id]
        site.save_group(admin_user["id"], None, {
            "name": "Fixture group", "member_ids": [alice["id"]], "resource_keys": resources,
        }, set(resources))
        migrate_site_accounts(self.source)
        migrate_resources(self.source)

    def _create_key(self, key_id, *, namespace="mailapi-test-fixture", production=False):
        path = self.root / (key_id + ".json")
        path.touch(mode=0o600)
        path.write_text(json.dumps({
            "environment": "production" if production else "test",
            "namespace": namespace, "active": key_id, "keys": {
                key_id: b64encode(AESGCM.generate_key(bit_length=256)).decode("ascii"),
            },
        }))
        return path

    def _ring(self, path):
        return CredentialKeyring(path, data_dirs=())

    def _run_http(self, data, key_file, operation, timestamp):
        script = r"""
import os
from unittest.mock import patch
import pyotp
from fastapi.testclient import TestClient
from mailapi_shared import application as main
from test_credential_encryption import ADMIN_TOTP, GPT_TOTP, PASSWORD, PLAN_KEY, RELAY_PASSWORD, GPT_PASSWORD, OLD_PASSWORD

key, change, plan, relay = (os.environ[name] for name in ("FIXTURE_GPT", "FIXTURE_CHANGE", "FIXTURE_PLAN", "FIXTURE_RELAY"))
origin = {"Origin": "http://127.0.0.1:8001"}
timestamp = int(os.environ["FIXTURE_TIME"])
operation = os.environ["FIXTURE_OPERATION"]

def login(client, name, *, code=None):
    payload = {"username": name, "password": PASSWORD}
    if code is not None:
        payload["totp_code"] = code
    return client.post("/api/auth/login", json=payload).status_code

def match(response, field, expected):
    assert response.status_code == 200, response.status_code
    assert response.json()[field] == expected, field

def check_member(client, expected_password):
    assert login(client, "alice") == 200
    match(client.post(f"/api/accounts/{key}/gpt-password"), "password", expected_password)
    codes = client.post(f"/api/accounts/{key}/totp-code")
    assert codes.status_code == 200, codes.status_code
    assert codes.json()["code"] == pyotp.TOTP(GPT_TOTP).at(codes.json()["server_time"])
    match(client.get(f"/api/coding-plans/{plan}/api-key"), "api_key", PLAN_KEY)
    match(client.get(f"/api/relay-accounts/{relay}/password"), "password", RELAY_PASSWORD)
    assert client.get(f"/api/admin/gpt/accounts/{key}/changes").status_code == 403
    client.cookies.clear()
    assert login(client, "bob") == 200
    assert client.get(f"/api/coding-plans/{plan}/api-key").status_code == 404
    assert client.post(f"/api/accounts/{key}/gpt-password").status_code == 404

with patch("mailapi_shared.site_accounts.time.time", return_value=timestamp), \
        TestClient(main.app, base_url="http://127.0.0.1:8001", headers=origin) as client:
    assert main.IS_TEST and not main.EXTERNAL_WATCHERS and not main.MAIL_WATCHERS
    assert login(client, "admin") == 401
    totp = pyotp.TOTP(ADMIN_TOTP).at(timestamp)
    assert login(client, "admin", code=totp) == 200
    client.cookies.clear()
    assert login(client, "admin", code=totp) == 401
    assert login(client, "admin", code=pyotp.TOTP(ADMIN_TOTP).at(timestamp + 30)) == 200
    path = f"/api/admin/gpt/accounts/{key}/changes"
    response = client.get(path)
    assert response.status_code == 200
    history = response.json()["items"]
    assert all(marker not in response.text for marker in (GPT_PASSWORD, OLD_PASSWORD, GPT_TOTP))
    if operation == "restore":
        original = next(event for event in history if event["id"] == change)
        assert any(field["field"] == "password" for field in original["restorable_fields"])
        assert client.post(f"{path}/{change}/review").status_code == 200
        assert client.post(f"{path}/{change}/restore", json={"field": "password"}).status_code == 200
        events = client.get(path).json()["items"]
        assert any(event.get("review_of") == change for event in events)
        assert any(event.get("restore_of") == change for event in events)
    else:
        assert not any(event.get("restore_of") == change for event in history)
    client.cookies.clear()
    check_member(client, OLD_PASSWORD if operation == "restore" else GPT_PASSWORD)
"""
        environment = {
            "PATH": os.environ["PATH"], "PYTHONPATH": str(ROOT / "tests"),
            "MAILAPI_MODE": "test", "TEST_DATA_DIR": str(data), "TEST_SITE_ACCOUNTS_BACKEND": "sqlite",
            "TEST_RESOURCE_BACKEND": "sqlite", "TEST_CREDENTIAL_KEY_FILE": str(key_file),
            "TEST_EXTERNAL_WATCHERS": "0", "FIXTURE_GPT": self.gpt_account["key"],
            "FIXTURE_CHANGE": self.change, "FIXTURE_PLAN": self.plan_id, "FIXTURE_RELAY": self.relay_id,
            "FIXTURE_TIME": str(timestamp), "FIXTURE_OPERATION": operation,
        }
        # Copy only code so a local untracked .env.test cannot change the test configuration.
        project = self.root / "sandbox"
        if not project.exists():
            shutil.copytree(ROOT / "mailapi_shared", project / "mailapi_shared", ignore=shutil.ignore_patterns("__pycache__"))
        result = subprocess.run([sys.executable, "-c", script], cwd=project,
                                env=environment, capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_http_migration_rotation_and_backup_rollback(self):
        key = self.gpt_account["key"]
        originals = {file.relative_to(self.source): file.read_bytes()
                     for file in self.source.rglob("*") if file.is_file()}
        rewrite(self.source, self.before, self.key1)
        self.assertEqual(originals, {file.relative_to(self.source): file.read_bytes()
                                     for file in self.source.rglob("*") if file.is_file()})
        backup_key = self.root / "offline-key-copy.json"
        shutil.copyfile(self.key1, backup_key)
        backup_key.chmod(0o600)
        copy_backup(self.before, self.backup, self.key1)
        rewrite(self.before, self.after, self.key2, old_key_file=self.key1)

        old_site = open_site_accounts(self.before, backend="sqlite", keyring=self._ring(self.key1))
        new_site = open_site_accounts(self.after, backend="sqlite", keyring=self._ring(self.key2))
        self.assertEqual(old_site._data, new_site._data)
        for directory, path in ((self.before, self.key1), (self.after, self.key2)):
            ring = self._ring(path)
            resources = ResourceDatabase(directory, keyring=ring)
            with closing(sqlite3.connect(directory / RESOURCE_NAME)) as db:
                rows = db.execute("SELECT event_id, value FROM encrypted_restore_points WHERE account_key=? AND field='password'", (key,)).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], self.change)
            self.assertEqual(json.loads(rows[0][1])[1], ring.active)
            self.assertEqual(resources.load("codex_accounts.json")["items"][0]["token"]["refresh_token"],
                             "fixture-oauth-current")
            self.assertEqual((directory / LEGACY_RESTORE_KEY).read_bytes(), (self.source / LEGACY_RESTORE_KEY).read_bytes())
            legacy = resources.load(GPT)["restore_points"][key]["codex:fixture"]
            self.assertEqual(legacy, self.gpt._data["restore_points"][key]["codex:fixture"])
            for filename in ("codex_auth/unchanged.fixture", "outlook_tokens/sample.json"):
                self.assertEqual((directory / filename).read_bytes(), originals[Path(filename)])
            self.assertFalse(any((directory / name).exists()
                                 for name in ("site_accounts.json", GPT, "coding_plans.json", "relay_accounts.json")))
            for file in [directory / SITE_NAME, directory / RESOURCE_NAME, *resources.secrets.iterdir()]:
                for marker in (PASSWORD, GPT_PASSWORD, OLD_PASSWORD, PLAN_KEY, RELAY_PASSWORD, ADMIN_TOTP, GPT_TOTP):
                    self.assertNotIn(marker.encode(), file.read_bytes())
            with self.assertRaises(CredentialCryptoError):
                resources.export_json(self.root)

        self._run_http(self.after, self.key2, "restore", self.timestamp)
        self.assertEqual((self.after / "codex_auth/unchanged.fixture").read_bytes(),
                         originals[Path("codex_auth/unchanged.fixture")])
        self.assertEqual((self.after / "outlook_tokens/sample.json").read_bytes(),
                         originals[Path("outlook_tokens/sample.json")])
        self.key1.unlink()
        with self.assertRaises(CredentialCryptoError):
            copy_backup(self.backup, self.restored, self.key2)
        self.assertFalse(self.restored.exists())
        copy_backup(self.backup, self.restored, backup_key)
        self._run_http(self.restored, backup_key, "verify", self.timestamp + 90)

    def test_missing_key_bad_permissions_wrong_scope_and_ciphertext_fail_closed(self):
        rewrite(self.source, self.before, self.key1)
        self.key1.chmod(0o644)
        with self.assertRaises(CredentialCryptoError):
            self._ring(self.key1)
        self.key1.chmod(0o600)
        ring = self._ring(self.key1)
        with self.assertRaises(CredentialCryptoError):
            open_site_accounts(self.before, backend="sqlite")
        with self.assertRaises(CredentialCryptoError):
            ResourceDatabase(self.before, keyring=self._ring(self.key2))
        with self.assertRaises(CredentialCryptoError):
            ResourceDatabase(self.before)
        envelope = ring.seal(GPT_PASSWORD, "resource", GPT, "id", "target", "password")
        for scope in (("resource", GPT, "id", "wrong", "password"), ("resource", GPT, "id", "target", "secret")):
            with self.assertRaises(CredentialCryptoError):
                ring.open(envelope, *scope)
        # Wrong key material or namespace fails even when the key ID matches.
        original = self.key1.read_bytes()
        wrong_key = self._create_key("v1", namespace="other-keyring")
        with self.assertRaises(CredentialCryptoError):
            ResourceDatabase(self.before, keyring=self._ring(wrong_key))
        self.key1.write_bytes(original)
        with closing(sqlite3.connect(self.before / RESOURCE_NAME)) as db, db:
            data, = db.execute("SELECT data FROM records WHERE pool=?", (GPT,)).fetchone()
            record = json.loads(data)
            record["password"][3] = b64encode(b"tampered fixture").decode("ascii")
            db.execute("UPDATE records SET data=? WHERE pool=?", (json.dumps(record), GPT))
        with self.assertRaises(CredentialCryptoError):
            ResourceDatabase(self.before, keyring=ring).load(GPT)
        with self.assertRaises(CredentialCryptoError):
            rewrite(self.before, self.after, self.key2, old_key_file=self.key1)
        self.assertFalse(self.after.exists())

    def test_production_key_migrates_and_restores_with_distinct_environment(self):
        key = self._create_key("production-v1", production=True)
        with self.assertRaises(CredentialCryptoError):
            rewrite(self.source, self.before, key)
        rewrite(self.source, self.before, key, production=True)
        ring = CredentialKeyring(key, data_dirs=(self.before,), production=True)
        test_ring = self._ring(self.key1)
        with self.assertRaises(CredentialCryptoError):
            open_site_accounts(self.before, backend="sqlite", keyring=test_ring)
        copy_backup(self.before, self.backup, key, production=True)
        store = open_site_accounts(self.backup, backend="sqlite", keyring=ring, require_admin_mfa=True)
        with patch("mailapi_shared.site_accounts.time.time", return_value=self.timestamp):
            self.assertIsNone(store.login("admin", PASSWORD))
            result = store.login("admin", PASSWORD, pyotp.TOTP(ADMIN_TOTP).at(self.timestamp))
            self.assertEqual(result[1]["role"], "admin")
        db = ResourceDatabase(self.backup, keyring=ring)
        self.assertEqual(db.load(GPT)["items"]["gpt@example.test"]["password"], GPT_PASSWORD)
