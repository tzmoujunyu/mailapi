from base64 import b64encode
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

from mailapi_shared.credential_crypto import CredentialCryptoError, CredentialKeyring
from mailapi_shared.oauth_files import OAuthFiles
from mailapi_shared.resource_database import ResourceDatabase, migrate_resources
from mailapi_shared.site_accounts import SiteAccountStore
from mailapi_shared.site_database import migrate_site_accounts
from mailapi_shared.test_credential_storage import copy_backup, rewrite, rollback_oauth


ROOT = Path(__file__).resolve().parents[1]
GMAIL = "gmail_tokens/account-1.json"
OUTLOOK = "outlook_tokens/outlook-1.json"
CODEX = "codex_auth/codex-fixture.json"
ARCHIVE = "auth_artifact_versions/codex/codex-fixture/previous.json"
MARKERS = ("gmail-original-refresh", "outlook-original-refresh", "codex-original-refresh",
           "gmail-renewed-refresh", "outlook-renewed-refresh", "codex-renewed-refresh")


class OAuthEncryptionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.encrypted = self.root / "encrypted"
        self.backup = self.root / "backup"
        self.restored = self.root / "restored"
        self.key = self.root / "oauth-key.json"
        self.key.touch(mode=0o600)
        self.key.write_text(json.dumps({
            "namespace": "test-oauth", "active": "v1", "keys": {
                "v1": b64encode(AESGCM.generate_key(bit_length=256)).decode(),
            },
        }))
        self.gmail = {
            "token": "gmail-old-access", "refresh_token": MARKERS[0],
            "client_id": "gmail-client", "client_secret": "gmail-client-fixture",
            "expiry": "2000-01-01T00:00:00Z",
        }
        self.outlook = {
            "Account": {"fixture": {"username": "outlook@example.test", "home_account_id": "fixture",
                                    "environment": "login.microsoftonline.com", "authority_type": "MSSTS",
                                    "realm": "common"}},
            "RefreshToken": {"fixture": {"secret": MARKERS[1], "home_account_id": "fixture",
                                         "environment": "login.microsoftonline.com", "client_id": "fixture",
                                         "credential_type": "RefreshToken", "target": "User.Read Mail.ReadWrite"}},
        }
        self.codex = {
            "account_id": "codex-fixture", "email": "codex@example.test",
            "tokens": {"access_token": "codex-old-access", "refresh_token": MARKERS[2], "id_token": ""},
        }
        self._write("token_account1.json", self.gmail)
        self._write(GMAIL, self.gmail)
        self._write(OUTLOOK, self.outlook)
        self._write(CODEX, self.codex)
        self._write(ARCHIVE, self.codex)
        self._write("gmail_accounts.json", {"items": [
            {"name": "account-1", "email": "gmail@example.test", "provider": "gmail",
             "token_file": str(self.source / "token_account1.json")},
            {"name": "outlook-1", "email": "outlook@example.test", "provider": "outlook",
             "token_file": str(self.source / OUTLOOK)},
        ]})
        self._write("codex_accounts.json", {"items": [
            {"id": "codex-fixture", "email": "codex@example.test", "token": self.codex["tokens"]},
        ]})
        with patch("mailapi_shared.site_accounts.password_is_pwned", return_value=False):
            SiteAccountStore(self.source / "site_accounts.json").ensure_admin("oauth-fixture-entry-89")
        migrate_site_accounts(self.source)
        migrate_resources(self.source)
        self.project = self.root / "project"
        shutil.copytree(ROOT / "mailapi_shared", self.project / "mailapi_shared", ignore=shutil.ignore_patterns("__pycache__"))

    def _write(self, relative, value):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def _app(self, directory, script):
        env = {**os.environ, "MAILAPI_MODE": "test", "TEST_DATA_DIR": str(directory),
               "TEST_SITE_ACCOUNTS_BACKEND": "sqlite", "TEST_RESOURCE_BACKEND": "sqlite",
               "TEST_CREDENTIAL_KEY_FILE": str(self.key), "TEST_OAUTH_ENCRYPTION": "1",
               "TEST_EXTERNAL_WATCHERS": "0"}
        result = subprocess.run([sys.executable, "-c", script], cwd=self.project, env=env,
                                text=True, capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)

    def _no_plaintext(self, directory):
        for file in directory.rglob("*"):
            if file.is_file():
                for marker in MARKERS:
                    self.assertNotIn(marker.encode(), file.read_bytes(), file.relative_to(directory))

    def test_migrate_refresh_backup_restore_and_restart(self):
        snap = {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        rewrite(self.source, self.encrypted, self.key, oauth=True)
        self.assertEqual(snap, {p.relative_to(self.source): p.read_bytes() for p in self.source.rglob("*") if p.is_file()})
        self._no_plaintext(self.encrypted)
        self._app(self.encrypted, r"""
import json, threading
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs
from unittest.mock import Mock, patch
from msal import ConfidentialClientApplication, SerializableTokenCache
from mailapi_shared import application as main
from mailapi_shared.mail_providers import outlook
from google.oauth2 import reauth

def gmail_refresh(request, token_uri, refresh_token, *args, **kwargs):
    assert refresh_token == "gmail-original-refresh"
    return "gmail-new-access", "gmail-renewed-refresh", datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1), {}, None

assert not main.EXTERNAL_WATCHERS
main.start_watchers()
gmail, ms = main.ACCOUNTS
with patch.object(reauth, "refresh_grant", side_effect=gmail_refresh) as grant, \
        patch.object(main, "build", side_effect=lambda *a, **kw: kw["credentials"]), \
        patch.object(main, "request_gmail_browser_auth", side_effect=AssertionError("unexpected reauthorization")):
    creds = main.get_gmail_service(gmail, threading.Event())
    assert creds.refresh_token == "gmail-renewed-refresh"
    assert main.read_oauth_json(gmail["token_file"])["token"] == "gmail-new-access"
    assert grant.call_count == 1
    grant.side_effect = None
    grant.return_value = ("gmail-later-access", "gmail-renewed-refresh", creds.expiry, {}, None)
    creds.refresh(None)
    assert main.read_oauth_json(gmail["token_file"])["token"] == "gmail-later-access"

def response(body):
    return Mock(status_code=200, text=json.dumps(body), headers={}, json=lambda: body)

def microsoft_discovery(url, **kwargs):
    assert url.endswith("/common/v2.0/.well-known/openid-configuration")
    return response({"authorization_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                     "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token"})

def microsoft_refresh(url, data, **kwargs):
    assert url.endswith("/common/oauth2/v2.0/token")
    assert data["refresh_token"] == "outlook-original-refresh"
    return response({"token_type": "Bearer", "access_token": "outlook-new-access",
                     "refresh_token": "outlook-renewed-refresh", "expires_in": 3600,
                     "scope": "User.Read Mail.ReadWrite"})

http = Mock()
http.get.side_effect = microsoft_discovery
http.post.side_effect = microsoft_refresh

def silent_client(*args, **kwargs):
    return ConfidentialClientApplication(*args, **kwargs, http_client=http)

def outlook_refresh_tokens(cache):
    return {entry["secret"] for entry in cache["RefreshToken"].values()}

settings = outlook.OutlookSettings("fixture", "fixture", "https://login.microsoftonline.com/common", "http://localhost")
with patch.object(outlook, "_msal_types", return_value=(silent_client, SerializableTokenCache)), \
        patch.object(outlook, "fetch_outlook_profile", return_value={"email": "outlook@example.test"}):
    mailbox = outlook.OutlookMailbox(settings, ms["token_file"], ms["email"], oauth_files=main.OAUTH_FILES)
    assert mailbox.connect()["email"] == ms["email"]
    main.save_outlook_cache(mailbox, ms)
    assert mailbox.access_token == "outlook-new-access"
    assert "outlook-renewed-refresh" in outlook_refresh_tokens(main.read_oauth_json(ms["token_file"]))
    assert http.post.call_count == 1

def codex_refresh(url, headers, *, data):
    assert parse_qs(data.decode())["refresh_token"] == ["codex-original-refresh"]
    return {"access_token": "codex-new-access", "refresh_token": "codex-renewed-refresh"}

def usage(token, workspace):
    if token == "codex-old-access":
        raise RuntimeError("upstream status 401 Unauthorized")
    assert token == "codex-new-access"
    return {}

assert main.load_codex_auth_files()[0]["tokens"]["refresh_token"] == "codex-original-refresh"
with patch.object(main, "http_json", side_effect=codex_refresh) as grant, \
        patch.object(main, "fetch_codex_usage", side_effect=usage), \
        patch.object(main, "fetch_codex_reset_credits", return_value={}):
    record = main.refresh_codex_quota_once("codex-fixture", manual=True)
    assert record["token"]["refresh_token"] == "codex-renewed-refresh"
    assert not record["reauth_required"]
    assert grant.call_count == 1
assert main.RESOURCE_DATABASE.load("codex_accounts.json")["items"][0]["token"]["refresh_token"] == "codex-renewed-refresh"
assert main.load_codex_auth_files()[0]["tokens"]["refresh_token"] == "codex-renewed-refresh"
""")
        self._no_plaintext(self.encrypted)
        copy_backup(self.encrypted, self.backup, self.key, oauth=True)
        copy_backup(self.backup, self.restored, self.key, oauth=True)
        self._no_plaintext(self.restored)
        self._app(self.restored, r"""
from pathlib import Path
from mailapi_shared import application as main
main.start_watchers()
gmail, outlook = main.ACCOUNTS
assert main.read_oauth_json(gmail["token_file"])["refresh_token"] == "gmail-renewed-refresh"
assert any(entry["secret"] == "outlook-renewed-refresh"
           for entry in main.read_oauth_json(outlook["token_file"])["RefreshToken"].values())
assert main.CODEX_ACCOUNTS["codex-fixture"]["token"]["refresh_token"] == "codex-renewed-refresh"
assert main.load_codex_auth_files()[0]["tokens"]["refresh_token"] == "codex-renewed-refresh"
archive = Path(main.DATA_DIR) / "auth_artifact_versions/codex/codex-fixture/previous.json"
assert main.read_oauth_json(archive)["tokens"]["refresh_token"] == "codex-original-refresh"
entries = main.AUTH_ARTIFACT_VERSIONS.list_public(provider="codex")
saved = next(entry for entry in entries if entry.get("archive_file"))
old = Path(main.AUTH_ARTIFACT_ARCHIVE_DIR) / saved["archive_file"]
assert main.read_oauth_json(old)["tokens"]["refresh_token"] == "codex-original-refresh"
""")

    def test_migrate_from_phase_three_and_reject_swapped_files(self):
        stage = self.root / "phase-three"
        rewrite(self.source, stage, self.key)
        # The configured legacy files were copied to this stopped snapshot.
        with sqlite3.connect(stage / "resources.sqlite3") as db:
            for name, payload in db.execute("SELECT record_id,data FROM records WHERE pool='gmail_accounts.json'").fetchall():
                item = json.loads(payload)
                item["token_file"] = str(stage / Path(item["token_file"]).relative_to(self.source))
                db.execute("UPDATE records SET data=? WHERE pool='gmail_accounts.json' AND record_id=?",
                           (json.dumps(item), name))
        rewrite(stage, self.encrypted, self.key, old_key_file=self.key, oauth=True, enable_oauth=True)
        storage = OAuthFiles(self.encrypted, CredentialKeyring(self.key, data_dirs=(self.encrypted,)))
        self.assertEqual(storage.read_json(self.encrypted / GMAIL), self.gmail)
        next_key = self.root / "oauth-key-v2.json"
        next_key.touch(mode=0o600)
        next_key.write_text(json.dumps({
            "namespace": "test-oauth", "active": "v2", "keys": {
                "v2": b64encode(AESGCM.generate_key(bit_length=256)).decode(),
            },
        }))
        rotated = self.root / "rotated"
        rewrite(self.encrypted, rotated, next_key, old_key_file=self.key, oauth=True)
        new_storage = OAuthFiles(rotated, CredentialKeyring(next_key, data_dirs=(rotated,)))
        self.assertEqual(new_storage.read_json(rotated / ARCHIVE), self.codex)
        with self.assertRaises(CredentialCryptoError):
            copy_backup(rotated, self.backup, self.key, oauth=True)
        self.assertFalse(self.backup.exists())
        shutil.copyfile(self.encrypted / GMAIL, self.encrypted / OUTLOOK)
        with self.assertRaises(CredentialCryptoError):
            copy_backup(self.encrypted, self.backup, self.key, oauth=True)
        self.assertFalse(self.backup.exists())

    def test_codex_restart_recovers_rotated_token_if_database_write_failed(self):
        rewrite(self.source, self.encrypted, self.key, oauth=True)
        self._app(self.encrypted, r"""
from mailapi_shared import application as main
main.start_watchers()
record = main.CODEX_ACCOUNTS["codex-fixture"]
updated = {**record, "token": {**record["token"], "access_token": "codex-new-access",
                              "refresh_token": "codex-renewed-refresh"}}
main.save_codex_record_auth_file(updated, previous_token=record["token"])
assert main.RESOURCE_DATABASE.load("codex_accounts.json")["items"][0]["token"] == record["token"]
""")
        self._app(self.encrypted, r"""
from mailapi_shared import application as main
main.start_watchers()
assert main.CODEX_ACCOUNTS["codex-fixture"]["token"]["refresh_token"] == "codex-renewed-refresh"
assert main.RESOURCE_DATABASE.load("codex_accounts.json")["items"][0]["token"]["refresh_token"] == "codex-renewed-refresh"
""")

    def test_production_rollback_rebuilds_files_with_latest_tokens(self):
        config = json.loads(self.key.read_text())
        config["environment"] = "production"
        self.key.write_text(json.dumps(config))
        baseline = self.root / "runtime"
        with sqlite3.connect(self.source / "resources.sqlite3") as db:
            for name, raw in db.execute("SELECT record_id,data FROM records WHERE pool='gmail_accounts.json'").fetchall():
                item = json.loads(raw)
                relative = Path(item["token_file"]).relative_to(self.source)
                item["token_file"] = str(Path("runtime") / relative) if item["provider"] == "outlook" else str(baseline / relative)
                db.execute("UPDATE records SET data=? WHERE pool='gmail_accounts.json' AND record_id=?", (json.dumps(item), name))
        rewrite(self.source, self.encrypted, self.key, production=True, oauth=True, original_root=baseline)
        ring = CredentialKeyring(self.key, data_dirs=(self.encrypted,), production=True)
        OAuthFiles(self.encrypted, ring).write_json(self.encrypted / GMAIL, {**self.gmail, "refresh_token": MARKERS[3]})
        copy_backup(self.encrypted, self.backup, self.key, production=True, oauth=True)
        runtime = self.root / "runtime"
        rollback_oauth(self.backup, self.restored, self.key, runtime_root=runtime, production=True)
        self.assertEqual(json.loads((self.restored / GMAIL).read_text())["refresh_token"], MARKERS[3])
        db = ResourceDatabase(self.restored, keyring=ring)
        items = db.load("gmail_accounts.json")["items"]
        self.assertEqual(items[0]["token_file"], str(runtime / "token_account1.json"))
        self.assertEqual(db.load("codex_accounts.json")["items"][0]["token"], self.codex["tokens"])
