import json
from pathlib import Path
import tempfile
import unittest

from resource_accounts import ResourceStore as ProductionStore, ResourceStoreError as ProductionError
from test_app.services.resource_accounts import ResourceStore as TestStore, ResourceStoreError as TestError


class RelayURLChecks:
    def test_create_requires_a_valid_website_url(self):
        invalid = (None, "", " ", 42, "relay.example", "//relay.example", "https://",
                   "javascript:alert(1)", "data:text/html,test", "ftp://relay.example",
                   "https://relay.example:bad", "https://[broken", "https://bad host.example",
                   "https://relay.example/\npath", "https://relay.example\\@other.example")
        with tempfile.TemporaryDirectory() as directory:
            store = self.Store(Path(directory) / "relays.json", "password", "密码")
            for url in invalid:
                with self.subTest(url=url), self.assertRaises(self.Error):
                    store.create({"name": "中转站", "account": "user", "password": "secret", "url": url})
            with self.assertRaises(self.Error):
                store.create({"name": "中转站", "account": "user", "password": "secret"})
            self.assertEqual(store.list_public(), [])

    def test_url_persists_and_updates_without_changing_password(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relays.json"
            store = self.Store(path, "password", "密码")
            url = "https://relay.example/panel?a=1&b=2#account"
            item = store.create({"name": "中转站", "account": "user", "password": "secret", "url": " " + url + " "})
            self.assertEqual(item["url"], url)
            self.assertNotEqual(item["password"], "secret")
            reopened = self.Store(path, "password", "密码")
            self.assertEqual(reopened.list_public()[0]["url"], url)
            updated = reopened.update(item["id"], {"name": "新名称", "password": ""})
            self.assertEqual(updated["url"], url)
            updated = reopened.update(item["id"], {"url": "http://localhost:8080/dashboard"})
            self.assertEqual(updated["url"], "http://localhost:8080/dashboard")
            self.assertEqual(reopened.get_secret(item["id"]), "secret")
            with self.assertRaises(self.Error):
                reopened.update(item["id"], {"url": "javascript:alert(1)"})
            self.assertEqual(reopened.list_public()[0]["url"], updated["url"])

    def test_legacy_accounts_survive_and_can_add_the_missing_url(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relays.json"
            old_data = {"version": 1, "items": [{"id": "old-relay", "name": "旧中转站", "account": "user", "password": "secret"}]}
            path.write_text(json.dumps(old_data), encoding="utf-8")
            store = self.Store(path, "password", "密码")
            self.assertEqual(store.list_public()[0]["url"], "")
            self.assertEqual(store.get_secret("old-relay"), "secret")
            self.assertEqual(json.loads(path.read_text()), old_data)
            updated = store.update("old-relay", {"url": "https://relay.example"})
            self.assertEqual(updated["url"], "https://relay.example")
            self.assertEqual(store.get_secret("old-relay"), "secret")

    def test_unsafe_stored_url_does_not_become_a_link_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "relays.json"
            path.write_text(json.dumps({"items": [{"id": "old-relay", "name": "旧中转站", "account": "user", "password": "secret", "url": "javascript:alert(1)"}]}))
            store = self.Store(path, "password", "密码")
            self.assertEqual(store.list_public()[0]["url"], "")
            self.assertEqual(store.get_secret("old-relay"), "secret")


class ProductionRelayURLTests(RelayURLChecks, unittest.TestCase):
    Store = ProductionStore
    Error = ProductionError


class TestVersionRelayURLTests(RelayURLChecks, unittest.TestCase):
    Store = TestStore
    Error = TestError


if __name__ == "__main__":
    unittest.main()
