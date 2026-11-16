import io
from pathlib import Path
import tempfile
import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from mailapi_shared.test_features import install_test_features
from mailapi_shared.web_security import BodyLimitMiddleware


class FakeSiteAccounts:
    def __init__(self):
        self.users = {
            "admin-id": {"id": "admin-id", "username": "admin", "role": "admin", "note": ""},
            "alice-id": {"id": "alice-id", "username": "alice", "role": "user", "note": "Agent"},
        }
        self.groups = {"group-id": {"id": "group-id", "name": "Agent", "member_ids": []}}

    def get_user(self, user_id, *, include_note=False):
        user = self.users.get(user_id)
        return dict(user) if user else None

    def user_groups(self, user_id):
        return [{"id": group["id"], "name": group["name"]}
                for group in self.groups.values() if user_id in group["member_ids"]]


class FinanceFeatureHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.site = FakeSiteAccounts()
        self.app = FastAPI()
        self.app.state.site_accounts = self.site

        @self.app.middleware("http")
        async def set_user(request: Request, call_next):
            user_id = request.headers.get("x-test-user", "admin-id")
            request.state.site_user = dict(self.site.users[user_id])
            return await call_next(request)

        install_test_features(self.app, Path(__file__).resolve().parents[1] / "mailapi_shared" / "static", self.directory, None)
        self.app.add_middleware(BodyLimitMiddleware)
        self.client = self.enterContext(TestClient(self.app))

    @staticmethod
    def upload(filename="invoice.pdf", content=b"invoice", content_type="application/pdf"):
        return {"invoice": (filename, io.BytesIO(content), content_type)}

    def create_application(self, user_id="alice-id", project="模型订阅"):
        return self.client.post(
            "/api/finance/applications",
            headers={"x-test-user": user_id},
            data={"project": project, "amount": "12.50"},
            files=self.upload(),
        )

    def test_upload_limit_project_validation_and_current_metadata(self):
        long_filename = "发票" * 180 + ".pdf"
        response = self.client.post(
            "/api/finance/applications",
            headers={"x-test-user": "alice-id"},
            data={"project": "模型订阅", "amount": "12.50"},
            files=self.upload(long_filename, b"x" * (1024 * 1024 + 20)),
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["item"]["files"][0]["original_name"], long_filename)

        too_long = self.create_application(project="x" * 21)
        self.assertEqual(too_long.status_code, 400)
        self.assertIn("20", too_long.json()["detail"])

        self.site.groups["group-id"]["member_ids"].append("alice-id")
        admin_items = self.client.get("/api/admin/finance/applications", headers={"x-test-user": "admin-id"}).json()["items"]
        self.assertEqual(admin_items[0]["groups"], ["Agent"])

    def test_my_applications_are_user_scoped_and_pending_requests_can_change(self):
        created = self.create_application()
        self.assertEqual(created.status_code, 201, created.text)
        app_id = created.json()["item"]["id"]
        self.assertEqual(len(self.client.get("/api/finance/applications", headers={"x-test-user": "alice-id"}).json()["items"]), 1)
        self.assertEqual(self.client.get("/api/finance/applications", headers={"x-test-user": "admin-id"}).json()["items"], [])

        updated = self.client.put(
            f"/api/finance/applications/{app_id}",
            headers={"x-test-user": "alice-id"},
            data={"project": "模型续费", "amount": "18.00"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["item"]["project"], "模型续费")
        cancelled = self.client.post(f"/api/finance/applications/{app_id}/cancel", headers={"x-test-user": "alice-id"})
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["item"]["status"], "cancelled")

    def test_admin_can_edit_announcements(self):
        created = self.client.post("/api/admin/finance/announcements", headers={"x-test-user": "admin-id"}, json={"title": "公告", "body": "内容", "pinned": True})
        self.assertEqual(created.status_code, 201, created.text)
        announcement_id = created.json()["item"]["id"]
        updated = self.client.put(
            f"/api/admin/finance/announcements/{announcement_id}",
            headers={"x-test-user": "admin-id"},
            json={"title": "新公告", "body": "新内容", "pinned": False},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertFalse(updated.json()["item"]["pinned"])
        self.assertEqual(updated.json()["item"]["title"], "新公告")


if __name__ == "__main__":
    unittest.main()
