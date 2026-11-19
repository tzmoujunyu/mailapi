from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from mailapi_shared.test_features import install_test_features


class SiteAnnouncementTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.app = FastAPI()

        @self.app.middleware("http")
        async def identify(request: Request, call_next):
            role = request.headers.get("x-role")
            request.state.site_user = {"id": request.headers.get("x-user", role), "username": role, "role": role} if role else None
            return await call_next(request)

        self.store = install_test_features(self.app, Path("mailapi_shared/static"), directory, None)
        self.client = self.enterContext(TestClient(self.app))
        self.admin = {"x-role": "admin"}
        self.reader = {"x-role": "user", "x-user": "reader"}

    def create(self):
        result = self.client.post("/api/admin/site-announcements", headers=self.admin,
                                  json={"title": "Site update", "body": "Message", "pinned": True})
        self.assertEqual(result.status_code, 201, result.text)
        return self.items(self.admin)[0]

    def items(self, headers):
        return self.client.get("/api/site-announcements", headers=headers).json()["items"]

    def test_independent_storage_and_admin_only_management(self):
        item = self.create()
        self.assertEqual(self.store.announcements(), [])
        self.store.save_announcement(announcement_id=None, title="Finance", body="Finance only", pinned=False, actor="admin")
        self.assertEqual(len(self.items(self.reader)), 1)
        self.assertEqual(self.client.get("/api/site-announcements").status_code, 401)
        for role in ("user", "manager"):
            headers = {"x-role": role}
            for method, url, payload in (
                ("POST", "/api/admin/site-announcements", {"title": "Bad", "body": "Bad"}),
                ("PUT", "/api/admin/site-announcements/" + item["id"], {"title": "Bad", "body": "Bad", "revision": 1}),
                ("DELETE", "/api/admin/site-announcements/" + item["id"], None),
            ):
                self.assertEqual(self.client.request(method, url, headers=headers, json=payload).status_code, 403)
        self.assertEqual(self.items(self.reader)[0]["title"], "Site update")

    def test_read_state_is_per_user_and_edited_revisions_become_unread(self):
        item = self.create()
        read = {"items": [{"id": item["id"], "revision": item["revision"]}]}
        self.assertEqual(self.client.post("/api/site-announcements/read", headers=self.reader, json=read).status_code, 200)
        self.assertFalse(self.items(self.reader)[0]["unread"])
        self.assertTrue(self.items({"x-role": "user", "x-user": "other"})[0]["unread"])
        updated = {"title": "Updated", "body": "New message", "revision": 1}
        self.assertEqual(self.client.put("/api/admin/site-announcements/" + item["id"], headers=self.admin, json=updated).status_code, 200)
        self.assertTrue(self.items(self.reader)[0]["unread"])
        self.client.post("/api/site-announcements/read", headers=self.reader, json=read)
        self.assertTrue(self.items(self.reader)[0]["unread"])
        self.assertEqual(self.client.put("/api/admin/site-announcements/" + item["id"], headers=self.admin, json=updated).status_code, 409)
        self.assertEqual(self.client.delete("/api/admin/site-announcements/" + item["id"], headers=self.admin).status_code, 200)
        with self.store._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM site_announcement_reads").fetchone()[0], 0)

    def test_invalid_content_and_future_read_revision_are_rejected(self):
        self.assertEqual(self.client.post("/api/admin/site-announcements", headers=self.admin,
                                         json={"title": "x" * 121, "body": "body"}).status_code, 400)
        item = self.create()
        self.assertEqual(self.client.post("/api/site-announcements/read", headers=self.reader,
                                         json={"items": [{"id": item["id"], "revision": 999}]}).status_code, 400)
        self.assertTrue(self.items(self.reader)[0]["unread"])
