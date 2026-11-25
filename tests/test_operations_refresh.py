import asyncio
from contextlib import closing
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from mailapi_shared.operations_notices import install_operations_notices
from mailapi_shared.test_features import TestFeatureStore


class OperationsRefreshTests(unittest.TestCase):
    async def wait_until(self, predicate):
        deadline=time.monotonic()+2
        while not predicate() and time.monotonic()<deadline:
            await asyncio.sleep(.005)
        self.assertTrue(predicate())

    def fixture(self, *, blocked=False, quota=0, failed_snapshot=False):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        store = TestFeatureStore(directory, None)
        origin = 'https://gateway.example'
        owners = {uid: {'id':uid, 'username':uid} for uid in ['alice','bob','unlinked','uninitialized']}
        with closing(store._connect()) as db, db:
            for host, uid, initialized in [(origin,'alice',1),(origin,'bob',1),(origin,'deleted',1),(origin,'uninitialized',0),('https://old','alice',1)]:
                db.execute('INSERT INTO newapi_users(origin,user_id,username,credential,initialized) VALUES(?,?,?,?,?)',(host,uid,uid,'test-only',initialized))
        store.api2api_config_public = lambda: {'engine':'newapi','enabled':True,'base_url':origin+'/v1'}
        credentials=[]
        def user(host, uid, *, create=True):
            credentials.append((host,uid,create))
            self.assertFalse(create)
            return {'origin':host,'user_id':uid,'initialized':1}
        store.gateway_user=user
        app=FastAPI();started=threading.Event();release=threading.Event();calls=[]
        @app.middleware('http')
        async def identity(request: Request, call_next):
            request.state.site_user={'id':request.headers.get('x-user','alice'),'role':request.headers.get('x-role','user')}
            return await call_next(request)
        async def request(credential, method, path):
            calls.append((credential['user_id'],method,path));started.set()
            while blocked and not release.is_set():
                await asyncio.sleep(.01)
            if isinstance(quota,Exception):
                raise quota
            return {'quota':quota}
        app.state.site_accounts=SimpleNamespace(get_user=lambda uid:owners.get(uid))
        resource={'key':'gpt:live:quota','resource_key':'gpt:live','kind':'quota','label':'live','active':True}
        def snapshot():
            if failed_snapshot:
                raise OSError('snapshot unavailable')
            return [resource]
        app.state.resource_health_provider=snapshot
        app.state.operation_resource_allowed=lambda user,key:key=='gpt:live'
        app.state.backup_notice_provider=lambda:[{'key':'backup:failed','kind':'backup','label':'备份任务失败','active':True}]
        install_operations_notices(app,store,SimpleNamespace(request=request))
        return app,store,origin,owners,started,calls,credentials,release

    def test_listing_local_notices_does_not_wait_for_blocked_upstream_and_never_provisions_users(self):
        app,store,origin,owners,started,calls,credentials,release=self.fixture(blocked=True)
        with TestClient(app) as client:
            try:
                self.assertTrue(started.wait(2))
                before=time.monotonic();response=client.get('/api/operations/notifications')
                self.assertLess(time.monotonic()-before,0.75)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json()['total'],1)
                self.assertEqual({item[0] for item in calls},{'alice','bob'})
                self.assertTrue(all(item[1:] == ('GET','/api/user/self') for item in calls))
                self.assertTrue(all(not item[2] for item in credentials))
            finally:
                release.set()
        with closing(store._connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM newapi_users').fetchone()[0],5)

    def test_current_mapping_reconciles_old_origins_and_deleted_owners_before_observing_quota(self):
        app,store,origin,owners,started,calls,credentials,release=self.fixture()
        app.state.operations_notices.observe([{'key':'api:https://old:alice','kind':'api_quota','user_id':'alice','label':'old','active':True},
                                            {'key':'api:'+origin+':deleted','kind':'api_quota','user_id':'deleted','label':'deleted','active':True}])
        with TestClient(app) as client:
            for _ in range(50):
                data=client.get('/api/operations/notifications',headers={'x-role':'admin','x-user':'admin'}).json()
                if len([item for item in data['items'] if item['kind']=='api_quota'])==2:
                    break
                time.sleep(.01)
            self.assertEqual({item['user_id'] for item in data['items'] if item['kind']=='api_quota'},{'alice','bob'})
            self.assertFalse(any('https://old' in item['source_key'] for item in data['items']))
            self.assertEqual(len(calls),2)
            owners.pop('bob')
            data=client.get('/api/operations/notifications',headers={'x-role':'admin','x-user':'admin'}).json()
            self.assertFalse(any(item['user_id']=='bob' for item in data['items']))

    def test_collection_failures_and_nonfinite_quota_keep_confirmed_alerts_and_backup_access_private(self):
        for quota in [float('nan'),float('inf'),RuntimeError('timeout')]:
            with self.subTest(quota=type(quota).__name__):
                app,store,origin,owners,started,calls,credentials,release=self.fixture(quota=quota,failed_snapshot=True)
                app.state.operations_notices.observe([{'key':'api:'+origin+':alice','kind':'api_quota','user_id':'alice','label':'alice','active':True},
                    {'key':'gpt:live:auth','resource_key':'gpt:live','kind':'authorization','label':'live','active':True}])
                def broken_status():
                    raise OSError('private file path must never leak')
                app.state.backup_status_provider=broken_status
                with TestClient(app) as client:
                    self.assertTrue(started.wait(2))
                    data=client.get('/api/operations/notifications').json()
                    self.assertEqual({item['kind'] for item in data['items']},{'api_quota','authorization'})
                    self.assertNotIn('backup_status',data)
                    data=client.get('/api/operations/notifications',headers={'x-role':'admin','x-user':'admin'}).json()
                    self.assertEqual(data['backup_status'],{'available':False})
                    self.assertTrue(any(item['kind']=='backup' for item in data['items']))
                    self.assertNotIn('private file path',str(data))

    def test_shutdown_drains_database_worker_before_returning_and_prevents_late_writes(self):
        app,store,origin,owners,started,calls,credentials,release=self.fixture()
        notices=app.state.operations_notices
        observe=notices.observe
        writing=threading.Event();finished=threading.Event();closed=threading.Event()
        writes=[];late_writes=[]
        def delayed_observe(rows, **kwargs):
            if not writing.is_set():
                writing.set()
                release.wait(2)
            try:
                observe(rows,**kwargs)
                writes.append(1)
                if closed.is_set():
                    late_writes.append(1)
            finally:
                finished.set()
        notices.observe=delayed_observe
        async def check():
            context=app.router.lifespan_context(app)
            await context.__aenter__()
            shutdown=None
            try:
                await self.wait_until(writing.is_set)
                shutdown=asyncio.create_task(context.__aexit__(None,None,None))
                await asyncio.sleep(.03)
                self.assertFalse(shutdown.done(), 'shutdown returned while the database worker was still running')
            finally:
                release.set()
                if shutdown is not None:
                    await asyncio.wait_for(shutdown,2)
                else:
                    await context.__aexit__(None,None,None)
                await self.wait_until(finished.is_set)
            closed.set()
            writes_at_close=len(writes)
            await asyncio.sleep(.03)
            self.assertEqual(len(writes),writes_at_close)
            self.assertEqual(late_writes,[])
            self.assertTrue(app.state.operations_task.done())
            self.assertEqual(calls,[], 'shutdown must not start a new gateway refresh')
        asyncio.run(check())
        with closing(store._connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operational_notices').fetchone()[0],2)

    def test_shutdown_finishes_inflight_gateway_requests_without_starting_queued_owners(self):
        app,store,origin,owners,started,calls,credentials,release=self.fixture(blocked=True)
        with closing(store._connect()) as db, db:
            db.execute('INSERT INTO newapi_users(origin,user_id,username,credential,initialized) VALUES(?,?,?,?,?)',
                       (origin,'unlinked','unlinked','test-only',1))
        async def check():
            context=app.router.lifespan_context(app)
            await context.__aenter__()
            shutdown=None
            try:
                await self.wait_until(lambda:len(calls)==2)
                shutdown=asyncio.create_task(context.__aexit__(None,None,None))
                await asyncio.sleep(.03)
                self.assertFalse(shutdown.done())
            finally:
                release.set()
                if shutdown is not None:
                    await asyncio.wait_for(shutdown,2)
                else:
                    await context.__aexit__(None,None,None)
            self.assertEqual(len(calls),2)
            self.assertEqual(len(credentials),2)
            self.assertTrue(app.state.operations_task.done())
        asyncio.run(check())
