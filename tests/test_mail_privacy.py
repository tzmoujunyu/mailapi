from datetime import datetime, timezone
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from mailapi_shared.log_privacy import error_diagnostic, redact_log_text
from mailapi_shared.mail_retention import retain_mail_records


class MailPrivacyTests(unittest.TestCase):
    def test_expiration_boundary(self):
        now = datetime(2026, 1, 2, tzinfo=timezone.utc).timestamp()
        fresh = {"created_at": "2026-01-01T00:00:01Z"}
        self.assertEqual(retain_mail_records([
            fresh, {}, {"created_at": "2026-01-01T00:00:00Z"},
            {"created_at": "2026-01-02T00:00:01Z"}, {"created_at": "2026-01-02"},
        ], now=now, retention_seconds=86400), [fresh])

    def test_sweep_removes_expired_orphan_records_and_latest_display(self):
        with tempfile.TemporaryDirectory() as root:
            result = subprocess.run([sys.executable, "-c", """
from mailapi_shared import application as main
from pathlib import Path
import json
root = Path(main.HISTORY_DIR)
record = {"created_at": "2000-01-01T00:00:00Z", "code": "synthetic-secret", "email": "test@example.test"}
current = {**record, "created_at": main.now_iso()}
name = "account-1"
file = root / f"{name}.codes.history.json"
file.write_text(json.dumps([record, current]))
orphan = root / "orphan.codes.history.json"
orphan.write_text(json.dumps([record]))
main.prune_code_histories()
assert json.loads(file.read_text()) == [current]
assert json.loads(orphan.read_text()) == []
main.CODE_HISTORY[name] = [record]
main.STATE[name] = {"last_code": record["code"], "email": record["email"], "updated_at": record["created_at"]}
assert main.public_mailbox({"name":name, "email":record["email"]})["last_code"] is None
"""], env={**os.environ, "MAILAPI_MODE": "test", "TEST_DATA_DIR": root, "TEST_EXTERNAL_WATCHERS": "0",
          "TEST_SITE_ACCOUNTS_BACKEND": "json", "TEST_RESOURCE_BACKEND": "json",
          "TEST_OAUTH_ENCRYPTION": "0", "TEST_OAUTH_CLIENTS_ENCRYPTED": "0", "TEST_CREDENTIAL_KEY_FILE": ""},
                cwd=Path(__file__).resolve().parents[1], check=False, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_oauth_errors_omit_raw_values(self):
        raw = 'HTTP status 401 invalid_grant {"clientSecret":"fixture secret"} https://example.test/cb?code=fixture-code'
        self.assertEqual(error_diagnostic(RuntimeError(raw)), "RuntimeError invalid_grant HTTP 401")
        self.assertNotIn("fixture", redact_log_text(raw))
