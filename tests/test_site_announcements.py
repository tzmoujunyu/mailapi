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

    def test_rich_content_legacy_preservation_and_conflict(self):
        blocks = [{"type": "paragraph", "data": {"text": "<b>Hello</b><script>bad()</script>"}}]
        created = self.client.post("/api/admin/site-announcements", headers=self.admin,
            json={"title": "Rich", "blocks": blocks})
        self.assertEqual(created.status_code, 201, created.text)
        item = self.items(self.admin)[0]
        self.assertIn("<b>Hello</b>", item["blocks"][0]["data"]["text"])
        self.assertNotIn("<script>", str(item["blocks"]))
        self.assertEqual(item["doc_version"], 1)
        self.client.put("/api/admin/site-announcements/"+item["id"], headers=self.admin,
            json={"title": item["title"], "body": item["body"], "pinned": True, "revision": 1})
        self.assertEqual(self.items(self.admin)[0]["blocks"], item["blocks"])
        finance = self.store.save_announcement(announcement_id=None, title="Rich", body=None,
            blocks=blocks, pinned=False, actor="admin")
        self.store.save_announcement(announcement_id=finance["id"], title="Rich", body=finance["body"],
            pinned=True, actor="admin", revision=1)
        self.assertEqual(self.store.announcements()[0]["blocks"], finance["blocks"])
        from mailapi_shared.test_features import TestFeatureError
        with self.assertRaises(TestFeatureError):
            self.store.save_announcement(announcement_id=finance["id"], title="Stale", body="x",
                pinned=False, actor="admin", revision=1)

    def test_scoped_upload_permissions_and_paths(self):
        import io
        from PIL import Image
        raw = io.BytesIO(); Image.new("RGB", (3, 3)).save(raw, "PNG")
        for scope in ("site", "finance"):
            url = "/api/announcement-images/"+scope
            self.assertEqual(self.client.post(url, headers=self.reader,
                files={"image": ("a.png", raw.getvalue(), "image/png")}).status_code, 403)
            response = self.client.post(url, headers=self.admin,
                files={"image": ("a.png", raw.getvalue(), "image/png")})
            self.assertEqual(response.status_code, 201, response.text)
            file = response.json()["file"]
            self.assertEqual(self.client.get(file["url"]).status_code, 401)
            self.assertEqual(self.client.get(file["url"], headers=self.reader).status_code, 200)
            other = "finance" if scope == "site" else "site"
            self.assertEqual(self.client.get(file["url"].replace("/"+scope+"/", "/"+other+"/"), headers=self.reader).status_code, 404)
        self.assertEqual(self.client.post("/api/announcement-images/site", headers={"x-role":"manager"},
            files={"image": ("a.png", raw.getvalue(), "image/png")}).status_code, 403)
        self.assertEqual(self.client.post("/api/announcement-images/finance", headers={"x-role":"manager"},
            files={"image": ("a.png", raw.getvalue(), "image/png")}).status_code, 201)
        blocks=[{"type":"image","data":{"file":{"url":"https://example.com/a.png"}}}]
        self.assertEqual(self.client.post("/api/admin/site-announcements", headers=self.admin,
            json={"title":"Bad", "blocks":blocks}).status_code,400)
