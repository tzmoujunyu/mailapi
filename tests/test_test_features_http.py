import io
import json
import zipfile
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

        self.store = install_test_features(self.app, Path(__file__).resolve().parents[1] / "mailapi_shared" / "static", self.directory, None)
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

    def test_group_attribution_is_validated_and_stays_separate_from_current_groups(self):
        self.site.groups["group-id"]["member_ids"] = ["alice-id"]
        one = self.create_application().json()["item"]
        self.assertEqual(one["expense_group_id"], "group-id")
        self.site.groups["second"] = {"id": "second", "name": "第二组", "member_ids": ["alice-id"]}
        self.assertEqual(self.create_application().status_code, 400)
        for selected in ("second", "not-a-member"):
            response = self.client.post("/api/finance/applications", headers={"x-test-user": "alice-id"},
                                        data={"project": "第二次申请", "amount": "20.00", "expense_group_id": selected}, files=self.upload())
            self.assertEqual(response.status_code, 201 if selected == "second" else 400)
        self.site.groups["group-id"]["member_ids"] = []
        self.site.users["alice-id"]["note"] = "新备注"
        current = next(item for item in self.client.get("/api/finance/applications", headers={"x-test-user": "alice-id"}).json()["items"] if item["id"] == one["id"])
        self.assertEqual(current["groups"], ["第二组"])
        self.assertEqual(current["user_note"], "新备注")
        self.assertEqual(current["expense_group_name"], "Agent")
        self.assertEqual(current["expense_group_id"], "group-id")

    def test_rejected_resubmission_keeps_versioned_files_and_exact_review_history(self):
        item = self.create_application().json()["item"]
        app_id = item["id"]
        review_url = f"/api/admin/finance/applications/{app_id}/review"
        self.assertEqual(self.client.post(review_url, json={"status": "rejected", "note": "", "version": 1}).status_code, 400)
        self.assertEqual(self.client.post(review_url, headers={"x-test-user": "alice-id"}, json={"status": "approved"}).status_code, 403)
        rejected = self.client.post(review_url, json={"status": "rejected", "note": "补充发票", "version": 1})
        self.assertEqual(rejected.status_code, 200, rejected.text)
        resubmitted = self.client.put(f"/api/finance/applications/{app_id}", headers={"x-test-user": "alice-id"},
                                    data={"project": "订阅重提", "amount": "18.00", "version": "1"}, files=self.upload("new.pdf", b"new invoice"))
        self.assertEqual(resubmitted.status_code, 200, resubmitted.text)
        self.assertEqual(resubmitted.json()["item"]["status"], "pending")
        self.assertEqual(resubmitted.json()["item"]["version"], 2)
        self.assertEqual(self.client.post(review_url, json={"status": "approved", "version": 1}).status_code, 409)
        stale = self.client.put(f"/api/finance/applications/{app_id}", headers={"x-test-user": "alice-id"},
                               data={"project": "过期修改", "amount": "1.00", "version": "1"}, files=self.upload("stale.pdf", b"stale"))
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(len(list((Path(self.directory)/"finance_files").iterdir())), 2)
        self.assertEqual(self.client.post(review_url, json={"status": "approved", "version": 2}).status_code, 200)
        self.assertEqual(self.client.post(review_url, json={"status": "approved", "version": 2}).status_code, 409)
        self.assertEqual(self.client.put(f"/api/finance/applications/{app_id}", headers={"x-test-user": "alice-id"}, data={"project": "修改", "amount": "1.00", "version": "2"}).status_code, 409)
        records = self.client.get(f"/api/finance/applications/{app_id}/history", headers={"x-test-user": "alice-id"}).json()["items"]
        self.assertEqual([event["action"] for event in records], ["approved", "resubmitted", "rejected", "submitted"])
        old = next(event for event in records if event["action"] == "rejected")
        self.assertEqual(old["note"], "补充发票")
        self.assertEqual(old["snapshot"]["project"], "模型订阅")
        self.assertNotIn("stored_name", json.dumps(records))
        self.assertNotIn("sha256", json.dumps(records))
        self.assertNotIn("user_id", records[0]["snapshot"])
        self.assertEqual(self.client.get(f"/api/finance/applications/{app_id}/download?kind=invoice&event_id={old['id']}", headers={"x-test-user": "alice-id"}).content, b"invoice")
        self.assertEqual(self.client.get(f"/api/finance/applications/{app_id}/download?kind=invoice").content, b"new invoice")
        approved = records[0]
        batch = self.client.post("/api/admin/finance/download.zip", json={"event_ids": [approved["id"]]})
        self.assertEqual(batch.status_code, 200, batch.text if batch.status_code != 200 else "")
        with zipfile.ZipFile(io.BytesIO(batch.content)) as archive:
            manifest = json.loads(archive.read("审批清单.json"))
            self.assertEqual(manifest[0]["version"], 2)
            self.assertIn(b"new invoice", [archive.read(name) for name in archive.namelist()])
        self.assertEqual(self.client.post("/api/admin/finance/download.zip", json={"event_ids": [old["id"]]}).status_code, 400)
        self.assertEqual(self.client.get("/api/admin/finance/history", headers={"x-test-user": "alice-id"}).status_code, 403)

    def test_other_users_cannot_read_change_or_download_an_application(self):
        item = self.create_application(user_id="admin-id").json()["item"]
        headers = {"x-test-user": "alice-id"}
        for suffix in ("history", "download?kind=invoice", "download.zip"):
            self.assertEqual(self.client.get(f"/api/finance/applications/{item['id']}/{suffix}", headers=headers).status_code, 404)
        self.assertEqual(self.client.put(f"/api/finance/applications/{item['id']}", headers=headers, data={"project": "修改", "amount": "1"}).status_code, 404)
        self.assertEqual(self.client.post("/api/admin/finance/download.zip", headers=headers, json={"application_ids": [item["id"]]}).status_code, 403)

    def test_invalid_amount_and_missing_archive_file_do_not_fail_silently(self):
        for amount in ("NaN", "Infinity", "0", "1000000.01"):
            response = self.client.post("/api/finance/applications", data={"project": "项目", "amount": amount}, files=self.upload())
            self.assertEqual(response.status_code, 400)
        item = self.create_application().json()["item"]
        self.client.post(f"/api/admin/finance/applications/{item['id']}/review", json={"status": "approved"})
        record = self.client.get("/api/admin/finance/history").json()["items"][0]
        for path in (Path(self.directory)/"finance_files").iterdir():
            path.unlink()
        self.assertEqual(self.client.post("/api/admin/finance/download.zip", json={"event_ids": [record["id"]]}).status_code, 410)


if __name__ == "__main__":
    unittest.main()
