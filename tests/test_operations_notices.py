from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from mailapi_shared.test_features import install_test_features
from mailapi_shared.resource_health import health_observations


class OperationsNoticeTests(unittest.TestCase):
    def setUp(self):
        self.directory=self.enterContext(tempfile.TemporaryDirectory())
        app=FastAPI()
        @app.middleware('http')
        async def user(request: Request, call_next):
            role=request.headers.get('x-role')
            request.state.site_user={'id':request.headers.get('x-user','alice'),'role':role} if role else None
            return await call_next(request)
        self.store=install_test_features(app,Path('mailapi_shared/static'),self.directory,None)
        self.app=app
        self.allowed={'alice'}
        app.state.operation_resource_allowed=lambda user,key: key=='gpt:test' and (user['role']=='admin' or user['id'] in self.allowed)
        self.notices=app.state.operations_notices
        self.client=self.enterContext(TestClient(app))
        async def stop_polling():
            app.state.operations_stop_event.set()
            await app.state.operations_task
        self.client.portal.call(stop_polling)
        self.user={'x-role':'user','x-user':'alice'}
        self.row={'key':'gpt:test:auth','resource_key':'gpt:test','kind':'authorization','label':'test','active':True}

    def items(self, headers=None):
        return self.client.get('/api/operations/notifications',headers=headers or self.user).json()

    def test_dedup_recovery_and_current_permission(self):
        self.notices.observe([self.row]);self.notices.observe([self.row])
        data=self.items();self.assertEqual(data['total'],1);self.assertEqual(data['unread_count'],1)
        first=data['items'][0]['id']
        self.client.post('/api/operations/notifications/read',headers=self.user,json={'ids':[first]})
        self.assertEqual(self.items()['unread_count'],0)
        self.notices.observe([self.row|{'active':None}]);self.assertEqual(self.items()['total'],1)
        self.notices.observe([self.row|{'active':False}]);self.assertEqual(self.items()['total'],0)
        self.notices.observe([self.row]);self.assertGreater(self.items()['items'][0]['id'],first)
        self.allowed.clear();self.assertEqual(self.items()['total'],0)
        self.assertEqual(self.items({'x-role':'admin'})['total'],1)

    def test_api_scope_and_read_authorization(self):
        self.notices.observe([{'key':'api:alice','user_id':'alice','kind':'api_quota','label':'alice','active':True},
                              {'key':'api:bob','user_id':'bob','kind':'api_quota','label':'bob','active':True}])
        first=self.items()['items'][0]['id']
        self.assertEqual(self.items()['total'],1)
        self.client.post('/api/operations/notifications/read',headers={'x-role':'user','x-user':'bob'},json={'ids':[first]})
        self.assertEqual(self.items()['unread_count'],1)
        self.assertEqual(self.client.get('/api/operations/notifications').status_code,401)

    def test_health_uses_only_confirmed_conditions(self):
        row={'key':'test','email':'test','listed':True,'subscription_expires_on':'2000-01-01',
             'codex':[{'id':'1','reauth_required':True,'usage_status':'unavailable','status_reason':'network_error'}]}
        observations=health_observations([row])
        self.assertTrue(observations[0]['active']);self.assertTrue(observations[1]['active']);self.assertIsNone(observations[2]['active'])
        row['codex'][0].update(reauth_required=False,status_reason='usage_limit_exhausted')
        self.assertTrue(health_observations([row])[2]['active'])
        plans=[{'id':'p','name':'plan','quota':{'windows':[{'remaining_percent':0}], 'error':'network'}}]
        self.assertIsNone(health_observations([],plans)[0]['active'])

    def test_admin_api_notices_require_an_existing_owner_and_known_resource_scope(self):
        owners={'alice','bob'}
        self.app.state.site_accounts=SimpleNamespace(get_user=lambda uid: {'id':uid} if uid in owners else None)
        self.notices.observe([{'key':'api:'+uid,'user_id':uid,'kind':'api_quota','label':uid,'active':True} for uid in ['alice','bob','deleted']])
        self.notices.observe([self.row|{'key':'unknown:auth','resource_key':'gpt:unknown'},
                              self.row|{'key':'unknown:kind','kind':'unknown','user_id':'alice'},
                              {'key':'backup:failed','kind':'backup','label':'备份失败','active':True}])
        self.assertEqual(self.items()['total'],1)
        data=self.items({'x-role':'admin','x-user':'admin'})
        self.assertEqual({row['kind'] for row in data['items']},{'api_quota','backup'})
        self.assertEqual(len(data['items']),3)
        owners.remove('bob')
        self.assertEqual(self.items({'x-role':'admin','x-user':'admin'})['total'],2)
        self.assertNotIn('backup_status',self.items())

    def test_complete_snapshots_remove_deleted_sources_but_partial_unknown_results_preserve_them(self):
        deleted=self.row|{'key':'gpt:test:deleted:auth'}
        quota={'key':'api:https://old:alice','kind':'api_quota','user_id':'alice','label':'alice','active':True}
        self.notices.observe([self.row,deleted,quota])
        self.notices.observe([self.row|{'active':None}],reconcile=('gpt:','coding-plan:'))
        self.assertEqual({row['source_key'] for row in self.items()['items']},{self.row['key'],quota['key']})
        self.notices.observe([{'key':'api:https://new:alice','active':None}],reconcile=('api:',))
        self.assertEqual(self.items()['total'],1)

    def test_network_failures_nonfinite_and_partial_windows_do_not_report_recovery(self):
        account={'key':'test','codex':[{'id':'x','usage_status':'available','status_reason':'usage_refresh_timeout'}]}
        self.assertIsNone(health_observations([account])[2]['active'])
        account['codex'][0].update(status_reason='usage_ok',error='network unavailable')
        self.assertIsNone(health_observations([account])[2]['active'])
        for remaining in [float('nan'),float('inf'),None,True]:
            plans=[{'id':'p','name':'plan','quota':{'windows':[{'remaining_percent':20},{'remaining_percent':remaining}]}}]
            self.assertIsNone(health_observations([],plans)[0]['active'])
        plans[0]['quota']['windows']=[{'remaining_percent':0},{}]
        self.assertTrue(health_observations([],plans)[0]['active'])
        plans[0]['quota']['windows']=[{'remaining_percent':20},{'remaining_percent':10}]
        self.assertFalse(health_observations([],plans)[0]['active'])

    def test_reads_only_mark_authorized_current_notices_and_validate_payload(self):
        self.notices.observe([self.row]);notice=self.items()['items'][0]['id']
        for payload in [{'ids':[True]},{'ids':[]},{'ids':[notice],'through_id':notice},{'through_id':-1}]:
            self.assertEqual(self.client.post('/api/operations/notifications/read',headers=self.user,json=payload).status_code,400)
        self.allowed.clear()
        self.client.post('/api/operations/notifications/read',headers=self.user,json={'through_id':notice})
        self.allowed.add('alice');self.assertEqual(self.items()['unread_count'],1)
        self.client.post('/api/operations/notifications/read',headers=self.user,json={'through_id':notice})
        self.assertEqual(self.items()['unread_count'],0)
