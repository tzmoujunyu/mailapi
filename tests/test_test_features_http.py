import io
import json
import sqlite3
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

    def list_users(self, actor_id):
        return [dict(user) for user in self.users.values()]

    def user_groups(self, user_id):
        return [{"id": group["id"], "name": group["name"]}
                for group in self.groups.values() if user_id in group["member_ids"]]


class FinanceFeatureHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.site = FakeSiteAccounts()
        self.invoice_counter = 0
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

    @staticmethod
    def invoice_fields(number='20261122000000000001'):
        return json.dumps({'number':number,'code':'','date':'2026-11-22','total':'12.50','check_code':''})

    def create_application(self, user_id="alice-id", project="模型订阅", application_note=""):
        self.invoice_counter += 1
        return self.client.post(
            "/api/finance/applications",
            headers={"x-test-user": user_id},
            data={"project": project, "amount": "12.50", "application_note":application_note, "invoice_fields":self.invoice_fields(f'20261122{self.invoice_counter:012d}')},
            files=self.upload(),
        )

    def test_upload_limit_project_validation_and_current_metadata(self):
        long_filename = "发票" * 180 + ".pdf"
        response = self.client.post(
            "/api/finance/applications",
            headers={"x-test-user": "alice-id"},
            data={"project": "模型订阅", "amount": "12.50", "invoice_fields":self.invoice_fields()},
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

    def test_application_notes_survive_edits_resubmission_and_explicit_clearing_with_history(self):
        note = '第一行\n第二行 <补充说明>'
        created = self.create_application(application_note='  '+note+'  ')
        self.assertEqual(created.status_code,201,created.text)
        item = created.json()['item'];app_id = item['id']
        self.assertEqual(item['application_note'],note)
        headers = {'x-test-user':'alice-id'}
        original = self.client.get(f'/api/finance/applications/{app_id}/history',headers=headers).json()['items'][0]['snapshot']
        updated = self.client.put(f'/api/finance/applications/{app_id}',headers=headers,data={'project':'旧页面修改','version':'1'})
        self.assertEqual(updated.status_code,200,updated.text)
        self.assertEqual(updated.json()['item']['application_note'],note)
        reviewed = self.client.post(f'/api/admin/finance/applications/{app_id}/review',json={'status':'rejected','note':'补充用途','version':2})
        self.assertEqual(reviewed.status_code,200,reviewed.text)
        updated = self.client.put(f'/api/finance/applications/{app_id}',headers=headers,
            data={'project':'补充后重提','version':'2','application_note':'新的用途说明'})
        self.assertEqual(updated.status_code,200,updated.text)
        self.assertEqual(updated.json()['item']['application_note'],'新的用途说明')
        stale = self.client.put(f'/api/finance/applications/{app_id}',headers=headers,
            data={'project':'过期页面','version':'2','application_note':'不能覆盖'})
        self.assertEqual(stale.status_code,409,stale.text)
        cleared = self.client.put(f'/api/finance/applications/{app_id}',headers=headers,
            data={'project':'清除备注','version':'3','application_note':''})
        self.assertEqual(cleared.status_code,200,cleared.text)
        self.assertEqual(cleared.json()['item']['application_note'],'')
        events = self.client.get(f'/api/finance/applications/{app_id}/history').json()['items']
        self.assertEqual(events[-1]['snapshot'],original)
        self.assertEqual(next(event for event in events if event['action']=='resubmitted')['snapshot']['application_note'],'新的用途说明')
        self.assertEqual(events[0]['snapshot']['application_note'],'')
        from mailapi_shared.test_features import TestFeatureStore
        reopened = TestFeatureStore(Path(self.directory),None)
        self.assertEqual(reopened.finance_history(app_id)[-1]['snapshot']['application_note'],note)

    def test_application_note_limit_and_proxy_notes_do_not_change_payer_ownership(self):
        rejected = self.create_application(application_note='长'*501)
        self.assertEqual(rejected.status_code,400,rejected.text)
        self.assertEqual(list(self.store.files.iterdir()),[])
        created = self.client.post('/api/admin/finance/applications',
            data={'project':'代提交备注','payer_user_id':'alice-id','application_note':'长'*500,'invoice_fields':self.invoice_fields()},
            files=self.upload())
        self.assertEqual(created.status_code,201,created.text)
        item=created.json()['item'];app_id=item['id']
        self.assertEqual(item['application_note'],'长'*500)
        self.assertEqual(self.store.finance_application(app_id)['user_id'],'alice-id')
        too_long = self.client.put(f'/api/finance/applications/{app_id}',headers={'x-test-user':'alice-id'},
            data={'project':'不能改写','version':'1','application_note':'长'*501})
        self.assertEqual(too_long.status_code,400,too_long.text)
        self.assertEqual(self.store.finance_application(app_id)['version'],1)
        forbidden = self.client.put(f'/api/finance/applications/{app_id}',
            data={'project':'不授予修改权','version':'1','application_note':'管理员不能改他人备注'})
        self.assertEqual(forbidden.status_code,404,forbidden.text)

    def test_payer_directory_is_admin_only_and_contains_no_login_or_security_data(self):
        self.site.users['alice-id']['password_hash'] = 'private-password-hash'
        self.site.users['alice-id']['totp_secret'] = 'private-totp'
        self.site.groups['group-id']['member_ids'] = ['alice-id']
        self.assertEqual(self.client.get('/api/admin/finance/payers', headers={'x-test-user':'alice-id'}).status_code,403)
        response = self.client.get('/api/admin/finance/payers')
        self.assertEqual(response.status_code,200,response.text)
        alice = next(user for user in response.json()['items'] if user['id']=='alice-id')
        self.assertEqual(set(alice),{'id','username','note','groups'})
        self.assertEqual(alice['groups'],[{'id':'group-id','name':'Agent'}])
        self.assertNotIn('private-',response.text)

    def test_dedicated_delegate_endpoint_always_requires_an_admin_and_an_explicit_payer(self):
        url='/api/admin/finance/applications'
        for actor,payload,status in [('alice-id',{'payer_user_id':'alice-id'},403),('admin-id',{},400)]:
            response=self.client.post(url,headers={'x-test-user':actor},data={'project':'不可误记','invoice_fields':self.invoice_fields(),**payload},files=self.upload())
            self.assertEqual(response.status_code,status,response.text)
        self.assertEqual(self.store.list_finance_applications(),[])

    def test_delegate_endpoint_keeps_finance_upload_limits_for_invoice_and_payment_files(self):
        response=self.client.post('/api/admin/finance/applications',
            data={'payer_user_id':'alice-id','project':'代交附件','invoice_fields':self.invoice_fields()},
            files={'invoice':('invoice.pdf',b'i'*(1024*1024+20),'application/pdf'),
                   'payment_record':('payment.pdf',b'p'*(1024*1024+20),'application/pdf')})
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(len(response.json()['item']['files']),2)

    def test_admin_submission_belongs_to_payer_and_keeps_actual_creator_through_resubmission(self):
        self.site.groups['group-id']['member_ids'] = ['alice-id']
        response = self.client.post('/api/admin/finance/applications',
            data={'payer_user_id':'alice-id','project':'代提交订阅','invoice_fields':self.invoice_fields()},files=self.upload())
        self.assertEqual(response.status_code,201,response.text)
        item = response.json()['item'];app_id = item['id']
        self.assertEqual(item['username'],'alice')
        self.assertEqual(item['expense_group_id'],'group-id')
        self.assertTrue(item['submitted_on_behalf'])
        self.assertEqual((item['submitted_by_id'],item['submitted_by_name']),('admin-id','admin'))
        self.assertEqual(self.store.finance_application(app_id)['user_id'],'alice-id')
        self.assertEqual(self.client.get('/api/finance/applications').json()['items'],[])
        self.assertEqual(len(self.client.get('/api/finance/applications',headers={'x-test-user':'alice-id'}).json()['items']),1)
        events = self.client.get(f'/api/finance/applications/{app_id}/history').json()['items']
        self.assertEqual((events[0]['actor_id'],events[0]['actor_name']),('admin-id','admin'))
        self.assertTrue(events[0]['snapshot']['submitted_on_behalf'])
        self.assertEqual(self.client.put(f'/api/finance/applications/{app_id}',data={'project':'他人不能修改'}).status_code,404)
        reviewed = self.client.post(f'/api/admin/finance/applications/{app_id}/review',json={'status':'rejected','note':'补充材料','version':1})
        self.assertEqual(reviewed.status_code,200,reviewed.text)
        updated = self.client.put(f'/api/finance/applications/{app_id}',headers={'x-test-user':'alice-id'},
            data={'project':'本人修改后重提','version':'1','payer_user_id':'admin-id'})
        self.assertEqual(updated.status_code,200,updated.text)
        self.assertEqual(updated.json()['item']['submitted_by_name'],'admin')
        self.assertEqual(self.store.finance_application(app_id)['user_id'],'alice-id')
        events = self.client.get(f'/api/finance/applications/{app_id}/history',headers={'x-test-user':'alice-id'}).json()['items']
        self.assertEqual(events[0]['actor_name'],'alice')
        self.assertTrue(all(event['snapshot']['submitted_on_behalf'] for event in events))
        from mailapi_shared.test_features import TestFeatureStore
        reopened = TestFeatureStore(Path(self.directory),None)
        self.assertEqual(reopened.finance_application(app_id)['submitted_by_id'],'admin-id')

    def test_proxy_permission_missing_account_and_payer_group_are_checked_before_saving_files(self):
        self.site.groups['group-id']['member_ids'] = ['admin-id']
        self.site.groups['payer-one'] = {'id':'payer-one','name':'用户组一','member_ids':['alice-id']}
        self.site.groups['payer-two'] = {'id':'payer-two','name':'用户组二','member_ids':['alice-id']}
        before = set(self.store.files.iterdir())
        for actor,payer,group,status in [('alice-id','admin-id','',403),('admin-id','missing','',404),
                                         ('admin-id','alice-id','',400),('admin-id','alice-id','group-id',400)]:
            with self.subTest(actor=actor,payer=payer,group=group):
                response = self.client.post('/api/finance/applications',headers={'x-test-user':actor},
                    data={'payer_user_id':payer,'expense_group_id':group,'project':'权限验证','invoice_fields':self.invoice_fields()},files=self.upload())
                self.assertEqual(response.status_code,status,response.text)
                self.assertEqual(set(self.store.files.iterdir()),before)
        self.site.users['manager-id'] = {'id':'manager-id','username':'manager','role':'manager','note':''}
        response = self.client.post('/api/finance/applications',headers={'x-test-user':'manager-id'},
            data={'payer_user_id':'alice-id','expense_group_id':'payer-two','project':'普通管理员代交','invoice_fields':self.invoice_fields()},files=self.upload())
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()['item']['expense_group_id'],'payer-two')
        self.assertEqual(response.json()['item']['submitted_by_name'],'manager')

    def test_existing_finance_rows_and_history_survive_submitter_metadata_migration(self):
        item = self.create_application().json()['item']
        database = Path(self.directory)/'test_features.sqlite3'
        with sqlite3.connect(database) as db:
            history = db.execute('SELECT snapshot_json FROM finance_versions').fetchall()
            db.execute('ALTER TABLE finance_applications DROP COLUMN submitted_by_id')
            db.execute('ALTER TABLE finance_applications DROP COLUMN submitted_by_name')
            db.execute('ALTER TABLE finance_applications DROP COLUMN application_note')
        from mailapi_shared.test_features import TestFeatureStore
        migrated = TestFeatureStore(Path(self.directory),None)
        restored = migrated.finance_application(item['id'])
        self.assertEqual(restored['amount_cents'],1250)
        self.assertEqual(restored['user_id'],'alice-id')
        self.assertEqual(restored['submitted_by_id'],'')
        self.assertEqual(restored['application_note'],'')
        self.assertEqual(len(restored['files']),1)
        with sqlite3.connect(database) as db:
            self.assertEqual(db.execute('SELECT snapshot_json FROM finance_versions').fetchall(),history)

    def test_group_attribution_is_validated_and_stays_separate_from_current_groups(self):
        self.site.groups["group-id"]["member_ids"] = ["alice-id"]
        one = self.create_application().json()["item"]
        self.assertEqual(one["expense_group_id"], "group-id")
        self.site.groups["second"] = {"id": "second", "name": "第二组", "member_ids": ["alice-id"]}
        self.assertEqual(self.create_application().status_code, 400)
        for selected in ("second", "not-a-member"):
            response = self.client.post("/api/finance/applications", headers={"x-test-user": "alice-id"},
                                        data={"project": "第二次申请", "amount": "20.00", "expense_group_id": selected,"invoice_fields":self.invoice_fields('20261122000000000002')}, files=self.upload(content=b'different invoice'))
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
                                    data={"project": "订阅重提", "amount": "18.00", "version": "1","invoice_fields":self.invoice_fields()}, files=self.upload("new.pdf", b"new invoice"))
        self.assertEqual(resubmitted.status_code, 200, resubmitted.text)
        self.assertEqual(resubmitted.json()["item"]["status"], "pending")
        self.assertEqual(resubmitted.json()["item"]["version"], 2)
        self.assertEqual(self.client.post(review_url, json={"status": "approved", "version": 1}).status_code, 409)
        stale = self.client.put(f"/api/finance/applications/{app_id}", headers={"x-test-user": "alice-id"},
                               data={"project": "过期修改", "amount": "1.00", "version": "1","invoice_fields":self.invoice_fields()}, files=self.upload("stale.pdf", b"stale"))
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

    def test_re_review_keeps_history_and_requires_the_latest_review_and_reason(self):
        item = self.create_application().json()['item']
        url = f"/api/admin/finance/applications/{item['id']}/re-review"
        self.assertEqual(self.client.post(url,json={'status':'rejected','note':'误操作','version':1,'event_id':'missing'}).status_code,409)
        self.client.post(f"/api/admin/finance/applications/{item['id']}/review",json={'status':'approved','version':1,'note':'首次审批'})
        before = self.client.get(f"/api/finance/applications/{item['id']}/history").json()['items'][0]
        self.assertTrue(before['is_current_review'])
        payload = {'status':'rejected','note':'误点通过，重新核对','version':1,'event_id':before['id']}
        self.assertEqual(self.client.post(url,headers={'x-test-user':'alice-id'},json=payload).status_code,403)
        for changed in ({'note':''},{'event_id':None},{'version':True},{'status':'approved'}):
            self.assertEqual(self.client.post(url,json=payload|changed).status_code,400)
        self.site.users['manager-id']={'id':'manager-id','username':'manager','role':'manager','note':''}
        response = self.client.post(url,headers={'x-test-user':'manager-id'},json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['item']['status'],'rejected')
        self.assertEqual(response.json()['item']['version'],1)
        events = self.client.get(f"/api/finance/applications/{item['id']}/history").json()['items']
        self.assertEqual([event['action'] for event in events],['rejected','approved','submitted'])
        self.assertEqual(events[0]['supersedes_event_id'],before['id'])
        self.assertEqual(events[0]['previous_status'],'approved')
        self.assertEqual(events[0]['actor_name'],'manager')
        self.assertTrue(events[0]['is_current_review'])
        self.assertFalse(events[1]['is_current_review'])
        self.assertEqual(events[1]['snapshot'],before['snapshot'])
        self.assertEqual(events[1]['note'],'首次审批')
        self.assertEqual(self.client.post('/api/admin/finance/download.zip',json={'event_ids':[before['id']]}).status_code,400)
        self.assertEqual(self.client.post(url,json=payload).status_code,409)
        restored = self.client.post(url,json={'status':'approved','note':'核对完成，恢复通过','version':1,'event_id':events[0]['id']})
        self.assertEqual(restored.status_code,200,restored.text)
        self.assertEqual(restored.json()['item']['status'],'approved')
        self.assertEqual(self.client.post(url,json=payload).status_code,409)
        current = self.client.get(f"/api/finance/applications/{item['id']}/history").json()['items'][0]
        self.assertEqual(self.client.post('/api/admin/finance/download.zip',json={'event_ids':[before['id'],current['id']]}).status_code,400)
        batch = self.client.post('/api/admin/finance/download.zip',json={'event_ids':[current['id']]})
        self.assertEqual(batch.status_code,200)
        with zipfile.ZipFile(io.BytesIO(batch.content)) as archive:
            self.assertEqual(json.loads(archive.read('审批清单.json'))[0]['record_id'],current['id'])
        self.assertEqual(self.client.get(f"/api/finance/applications/{item['id']}/download?kind=invoice&event_id={before['id']}").content,b'invoice')

    def test_old_review_cannot_override_resubmitted_material(self):
        item = self.create_application().json()['item']
        self.client.post(f"/api/admin/finance/applications/{item['id']}/review",json={'status':'rejected','note':'补材料','version':1})
        previous = self.client.get(f"/api/finance/applications/{item['id']}/history").json()['items'][0]
        self.client.put(f"/api/finance/applications/{item['id']}",headers={'x-test-user':'alice-id'},data={'project':'更新资料','amount':'18.00','version':'1'})
        response = self.client.post(f"/api/admin/finance/applications/{item['id']}/re-review",json={'status':'approved','note':'旧页面误操作','version':1,'event_id':previous['id']})
        self.assertEqual(response.status_code,409)
        events = self.client.get(f"/api/finance/applications/{item['id']}/history").json()['items']
        self.assertFalse(any(event['is_current_review'] for event in events))
        self.assertEqual(events[0]['snapshot']['version'],2)

    def test_invalid_amount_and_missing_archive_file_do_not_fail_silently(self):
        for amount in ("NaN", "Infinity", "0", "1000000.01"):
            fields = {**json.loads(self.invoice_fields()), 'total': amount}
            response = self.client.post("/api/finance/applications", data={"project": "项目", "invoice_fields": json.dumps(fields)}, files=self.upload())
            self.assertEqual(response.status_code, 400)
        item = self.create_application().json()["item"]
        self.client.post(f"/api/admin/finance/applications/{item['id']}/review", json={"status": "approved"})
        record = self.client.get("/api/admin/finance/history").json()["items"][0]
        for path in (Path(self.directory)/"finance_files").iterdir():
            path.unlink()
        self.assertEqual(self.client.post("/api/admin/finance/download.zip", json={"event_ids": [record["id"]]}).status_code, 410)


if __name__ == "__main__":
    unittest.main()
