import os
import tempfile
import unittest
from pathlib import Path

from auth_storage import archive_auth_file, migrate_auth_file


class AuthStorageTests(unittest.TestCase):
    def test_moves_auth_file_and_secures_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy.json"
            target = root / "tokens" / "account-1.json"
            source.write_text("current-token", encoding="utf-8")

            result = migrate_auth_file(source, target, root / "backups")

            self.assertEqual(result, "moved")
            self.assertFalse(source.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "current-token")
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(target.parent).st_mode & 0o777, 0o700)

    def test_deduplicates_identical_legacy_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy.json"
            target = root / "tokens" / "account-1.json"
            target.parent.mkdir()
            source.write_text("same-token", encoding="utf-8")
            target.write_text("same-token", encoding="utf-8")

            result = migrate_auth_file(source, target, root / "backups")

            self.assertEqual(result, "deduplicated")
            self.assertFalse(source.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "same-token")

    def test_preserves_conflicting_target_before_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "legacy.json"
            target = root / "tokens" / "account-1.json"
            backups = root / "backups"
            target.parent.mkdir()
            source.write_text("configured-token", encoding="utf-8")
            target.write_text("different-token", encoding="utf-8")

            result = migrate_auth_file(source, target, backups)

            self.assertEqual(result, "moved")
            self.assertEqual(target.read_text(encoding="utf-8"), "configured-token")
            archived = list(backups.iterdir())
            self.assertEqual(len(archived), 1)
            self.assertEqual(archived[0].read_text(encoding="utf-8"), "different-token")

    def test_archives_legacy_backup_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "token.json.bak.1"
            source.write_text("backup-token", encoding="utf-8")

            result = archive_auth_file(source, root / "backups")

            self.assertEqual(result, "archived")
            self.assertFalse(source.exists())
            self.assertEqual(
                (root / "backups" / source.name).read_text(encoding="utf-8"),
                "backup-token",
            )


if __name__ == "__main__":
    unittest.main()
