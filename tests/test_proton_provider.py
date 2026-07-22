import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mail_providers.proton import (
    ProtonAuthorizationRequired,
    ProtonMailbox,
    load_proton_credentials,
)


class ProtonProviderTests(unittest.TestCase):
    def test_loads_credentials_without_persisting_them_elsewhere(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / ".env"
            config.write_text(
                "PROTON_USERNAME=user@proton.me\n"
                "PROTON_PASSWORD=secret-value\n"
                "PROTON_TOTP_SECRET=ABCDEF\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "PROTON_USERNAME": "",
                    "PROTON_PASSWORD": "",
                    "PROTON_TOTP_SECRET": "",
                },
            ):
                credentials = load_proton_credentials(str(config))
            self.assertEqual(credentials.username, "user@proton.me")
            self.assertEqual(credentials.password, "secret-value")
            self.assertEqual(credentials.totp_secret, "ABCDEF")

    def test_missing_session_requires_login(self):
        with tempfile.TemporaryDirectory() as directory:
            mailbox = ProtonMailbox(str(Path(directory) / "missing.pickle"))
            with self.assertRaises(ProtonAuthorizationRequired):
                mailbox.connect()


if __name__ == "__main__":
    unittest.main()
