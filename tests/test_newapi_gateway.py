import asyncio
import csv
from base64 import b64encode
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import httpx
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from mailapi_shared.credential_crypto import CredentialKeyring
from mailapi_shared.newapi_gateway import NewAPIGateway, NewAPIError
from mailapi_shared.test_features import TestFeatureStore, install_test_features, _StreamUsage
from mailapi_shared.web_security import ProxyIngress


class EngineFixture:
    """The external NewAPI HTTP boundary, using its rc.51 request contracts."""
    def __init__(self):
        self.calls = []
        self.users = {'manager': {'id': 1, 'password': 'management-secret'}}
        self.tokens = {}
        self.fail_delete = False
        self.fail_toggle = False
        self.logs = []
        self.log_queries = []
        self.corrupt_log_owner = False
        self.model_names = ['gpt-4o-mini']
        self.pricing_failure = False

    def response(self, data=None, status=200, success=True):
        return httpx.Response(status, json={'success': success, 'data': data, 'message': ''})

    async def __call__(self, request):
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, path, body))
        if path == '/api/user/login':
            user = self.users.get(body['username'])
            if not user or user['password'] != body['password']:
                return self.response(status=401, success=False)
            response = self.response({'access_token': f'auth-{user["id"]}', 'access_expires_at': 4102444800, 'user': {'id': user['id']}})
            response.headers['set-cookie'] = f'new_api_refresh=refresh-{user["id"]}; HttpOnly; Path=/'
            return response
        if path == '/api/user/auth/refresh':
            owner = int(request.headers['cookie'].rsplit('-',1)[-1])
            return self.response({'access_token':f'auth-{owner}','access_expires_at':4102444800,'user':{'id':owner}})
        assert request.headers.get('cookie','') == '', 'Do not forward another linked user session cookie'
        owner = int(request.headers.get('authorization', 'Bearer auth-0').rsplit('-', 1)[-1])
        if path == '/api/user/' and request.method == 'POST':
            assert owner == 1
            assert body['role'] == 1
            self.users[body['username']] = {'id': len(self.users)+1, 'password': body['password']}
            return self.response()
        if path == '/api/user/manage':
            assert owner == 1
            return self.response()
        if path == '/api/token/' and request.method == 'POST':
            token_id = len(self.tokens)+1
            self.tokens[token_id] = {**body,'id': token_id, 'user_id': owner, 'name': body['name'], 'status': 1,
                                     'key': f'sk-engine-issued-{token_id:032d}', 'used_quota': 7,
                                     'remain_quota':body['remain_quota'] if not body['unlimited_quota'] else 93}
            return self.response()
        if path == '/api/token/search':
            return self.response({'items': [x for x in self.tokens.values() if x['user_id'] == owner and x['name'] == request.url.params['keyword']]})
        if path == '/api/token/' and request.method == 'PUT':
            assert self.tokens[body['id']]['user_id']==owner
            if self.fail_toggle:
                return httpx.Response(503)
            if request.url.params.get('status_only')=='true':
                self.tokens[body['id']]['status']=body['status']
            else:
                assert 'key' not in body and 'status' not in body
                self.tokens[body['id']].update(body)
            return self.response()
        if path == '/api/token/':
            return self.response({'items': [x for x in self.tokens.values() if x['user_id'] == owner], 'total': len(self.tokens)})
        if path.endswith('/key'):
            token_id = int(path.split('/')[-2])
            assert self.tokens[token_id]['user_id'] == owner
            return self.response({'key': self.tokens[token_id]['key']})
        if request.method=='GET' and path.startswith('/api/token/') and path.rsplit('/',1)[-1].isdigit():
            token_id=int(path.rsplit('/',1)[-1])
            assert self.tokens[token_id]['user_id']==owner
            return self.response(dict(self.tokens[token_id]))
        if request.method == 'DELETE' and path.startswith('/api/token/'):
            if self.fail_delete:
                return httpx.Response(503)
            token_id = int(path.split('/')[-1])
            assert self.tokens[token_id]['user_id'] == owner
            self.tokens.pop(token_id, None)
            return self.response()
        if path == '/api/user/self':
            return self.response({'id': owner, 'quota': 93, 'used_quota': 7, 'request_count': 1})
        if path == '/api/user/models':
            return self.response(self.model_names)
        if path == '/api/user/self/groups':
            return self.response({'default':'Default'})
        if path == '/api/status':
            return self.response({'quota_per_unit':500000,'quota_display_type':'USD'})
        if path == '/api/pricing':
            assert owner!=1
            if self.pricing_failure:
                return httpx.Response(503)
            return httpx.Response(200,json={'success':True,'data':[
                {'model_name':'gpt-4o-mini','vendor_id':1,'description':'Fixture model','tags':'text,tools',
                 'quota_type':0,'model_ratio':.075,'completion_ratio':4,'cache_ratio':.5,
                 'enable_groups':['default','private-group'],'supported_endpoint_types':['openai','gemini'],'private_key':'do-not-return'},
                {'model_name':'secret-model','vendor_id':2,'description':'hidden model','enable_groups':['private-group']}],
                'vendors':[{'id':1,'name':'OpenAI'},{'id':2,'name':'hidden-vendor'}],
                'group_ratio':{'default':1,'private-group':9},
                'supported_endpoint':{'openai':{'path':'/v1/chat/completions','method':'POST'},'gemini':{'path':'/v1beta/models/{model}:generateContent','method':'POST'}}})
        if path == '/api/perf-metrics/summary':
            assert owner!=1
            return self.response({'models':[{'model_name':'gpt-4o-mini','avg_latency_ms':1200,'avg_tps':40,'success_rate':99.5},
                                           {'model_name':'secret-model','avg_latency_ms':100,'success_rate':99}]})
        if path == '/api/perf-metrics':
            assert owner!=1
            return self.response({'model_name':request.url.params['model'],'summary':{'success_rate':99.5,'avg_latency_ms':1200,'avg_tps':40},
                'series':[{'ts':1600000000,'success_rate':99,'avg_latency_ms':1200}],
                'groups':[{'group':'default','avg_tps':40,'success_rate':99.5},{'group':'private-group','avg_tps':99}],
                'credential':'do-not-return'})
        if path in {'/api/log/','/api/log/stat'}:
            assert owner==1
            params=dict(request.url.params)
            self.log_queries.append(params)
            rows=[row for row in self.logs if row['username']==params['username']]
            if path.endswith('/stat'):
                return self.response({'quota':sum(row['quota'] for row in rows),'rpm':0,'tpm':0})
            page,size=int(params['p']),int(params['page_size'])
            items=[dict(row) for row in rows[(page-1)*size:page*size]]
            if self.corrupt_log_owner and items:
                items[0]['user_id']=99999
            return self.response({'items':items,'total':len(rows)})
        if path == '/api/data/self':
            return self.response([])
        if path == '/api/channel/':
            assert owner == 1
            return self.response({'items':[{'id':1,'name':'Fixture','key':'never-return-this-secret','models':'gpt-4o-mini'}]})
        raise AssertionError((request.method, path))


class NewAPIGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        key = root/'key.json'
        key.write_text(json.dumps({'namespace':'newapi-test','active':'one','keys':{'one':b64encode(AESGCM.generate_key(bit_length=256)).decode()}}))
        key.chmod(0o600)
        self.store = TestFeatureStore(root/'data', CredentialKeyring(key, data_dirs=(root/'data',)))
        self.store.configure_api2api({'engine':'newapi','base_url':'http://127.0.0.1:3001/v1',
                                     'management_username':'manager','management_password':'management-secret',
                                     'initial_quota':100,'enabled':True})
        self.engine = EngineFixture()
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.engine), trust_env=False)
        self.addAsyncCleanup(self.client.aclose)
        self.gateway = NewAPIGateway(self.store, client=self.client)

    async def test_native_keys_are_issued_by_newapi_and_secrets_are_not_returned_in_lists(self):
        item = await self.gateway.create_key('alice', 'Local development')
        self.assertTrue(item['api_key'].startswith('sk-engine-issued-'))
        self.assertEqual(self.store.authenticate_api_key(item['api_key'])['user_id'], 'alice')
        self.assertEqual(self.store.list_api_keys('bob'), [])
        listed = await self.gateway.list_keys('alice')
        self.assertEqual(listed[0]['used_quota'], 7)
        serialized = json.dumps(listed)
        self.assertNotIn(item['api_key'], serialized)
        self.assertNotIn('management-secret', serialized)
        disk = b''.join(p.read_bytes() for p in self.store.root.iterdir() if p.is_file())
        self.assertNotIn(b'management-secret', disk)
        self.assertNotIn(item['api_key'].encode(), disk)

    async def test_each_website_user_has_a_separate_non_admin_engine_identity(self):
        alice, bob = await asyncio.gather(self.gateway.create_key('alice', 'A'), self.gateway.create_key('bob', 'B'))
        self.assertNotEqual(self.engine.tokens[1]['user_id'], self.engine.tokens[2]['user_id'])
        self.assertEqual([x['id'] for x in await self.gateway.list_keys('alice')], [alice['id']])
        with self.assertRaises(NewAPIError) as error:
            await self.gateway.revoke_key(alice['id'], 'bob')
        self.assertEqual(error.exception.status_code, 404)
        self.assertTrue(self.store.authenticate_api_key(alice['api_key']))
        self.assertTrue(self.store.authenticate_api_key(bob['api_key']))

    async def test_revocation_blocks_the_website_even_if_engine_is_unavailable_and_can_retry(self):
        item = await self.gateway.create_key('alice', 'A')
        self.engine.fail_delete = True
        with self.assertRaises(NewAPIError):
            await self.gateway.revoke_key(item['id'], 'alice')
        self.assertIsNone(self.store.authenticate_api_key(item['api_key']))
        self.assertTrue((await self.gateway.list_keys('alice'))[0]['revoke_pending'])
        self.engine.fail_delete = False
        await self.gateway.revoke_key(item['id'], 'alice')
        self.assertFalse(self.engine.tokens)

    async def test_connection_changes_do_not_reuse_another_engines_credentials(self):
        with self.assertRaises(ValueError):
            self.store.configure_api2api({'engine':'newapi','base_url':'http://127.0.0.1:3999/v1',
                                         'management_username':'manager','management_password':'','enabled':True})
        with self.assertRaises(ValueError):
            self.store.configure_api2api({'engine':'newapi','base_url':'http://public.example/v1',
                                         'management_username':'manager','management_password':'secret','enabled':True})

    async def test_pause_resume_and_on_demand_copy_keep_the_same_key_without_plaintext_storage(self):
        item=await self.gateway.create_key('alice','A')
        self.assertEqual(await self.gateway.reveal_key(item['id'],'alice'),item['api_key'])
        with self.assertRaises(NewAPIError) as error:
            await self.gateway.reveal_key(item['id'],'bob')
        self.assertEqual(error.exception.status_code,404)
        await self.gateway.set_key_enabled(item['id'],'alice',False)
        self.assertIsNone(self.store.authenticate_api_key(item['api_key']))
        paused=(await self.gateway.list_keys('alice'))[0]
        self.assertFalse(paused['revoked'])
        self.assertFalse(paused['enabled'])
        self.assertTrue(paused['can_copy'] and paused['can_toggle'])
        self.assertFalse(paused['revoke_pending'])
        self.assertEqual(await self.gateway.reveal_key(item['id'],'alice'),item['api_key'])
        await self.gateway.set_key_enabled(item['id'],'alice',True)
        self.assertEqual(self.store.authenticate_api_key(item['api_key'])['user_id'],'alice')
        self.assertEqual(self.engine.tokens[1]['status'],1)
        disk=b''.join(path.read_bytes() for path in self.store.root.iterdir() if path.is_file())
        self.assertNotIn(item['api_key'].encode(),disk)
        await self.gateway.revoke_key(item['id'],'alice')
        for operation in (self.gateway.reveal_key(item['id'],'alice'),self.gateway.set_key_enabled(item['id'],'alice',True)):
            with self.assertRaises(NewAPIError) as error:
                await operation
            self.assertEqual(error.exception.status_code,409)

    async def test_failed_gateway_toggle_blocks_locally_and_can_be_retried(self):
        item=await self.gateway.create_key('alice','A')
        self.engine.fail_toggle=True
        for enabled in (False,True):
            with self.assertRaises(NewAPIError):
                await self.gateway.set_key_enabled(item['id'],'alice',enabled)
            self.assertIsNone(self.store.authenticate_api_key(item['api_key']))
        paused=(await self.gateway.list_keys('alice'))[0]
        self.assertTrue(paused['pause_pending'])
        self.assertFalse(paused['revoke_pending'])
        self.engine.fail_toggle=False
        await self.gateway.set_key_enabled(item['id'],'alice',True)
        self.assertIsNotNone(self.store.authenticate_api_key(item['api_key']))

    async def test_revoke_cannot_be_undone_by_a_concurrent_enable(self):
        item=await self.gateway.create_key('alice','A')
        await asyncio.gather(self.gateway.revoke_key(item['id'],'alice'),self.gateway.set_key_enabled(item['id'],'alice',True),return_exceptions=True)
        self.assertTrue(self.store.gateway_key(item['id'])['revoked'])
        self.assertIsNone(self.store.authenticate_api_key(item['api_key']))
        self.assertFalse(self.engine.tokens)

    async def test_refresh_keeps_distinct_users_and_does_not_send_shared_client_cookies(self):
        await self.gateway.create_key('alice','A')
        await self.gateway.create_key('bob','B')
        user = await self.gateway.identity('alice')
        renewed = await self.gateway.session(user,renew=True)
        self.assertEqual(renewed['user_id'],self.store.gateway_user(user['origin'],'alice')['remote_id'])
        self.assertEqual((await self.gateway.list_keys('alice'))[0]['name'],'A')
        self.assertTrue(any(path=='/api/user/auth/refresh' for _,path,_ in self.engine.calls))


class GatewayHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(self.temp)
        key = root/'key.json'
        key.write_text(json.dumps({'namespace':'http-test','active':'one','keys':{'one':b64encode(AESGCM.generate_key(bit_length=256)).decode()}}))
        key.chmod(0o600)
        self.users = {name:{'id':name,'username':name,'role':'admin' if name=='admin' else 'user','status':'active'} for name in ('admin','alice','bob')}
        app = FastAPI()
        self.app = app
        class Site:
            def get_user(_, user_id, **kwargs):
                return self.users.get(user_id)
        app.state.site_accounts = Site()
        @app.middleware('http')
        async def session(request: Request, call_next):
            request.state.site_user = self.users.get(request.headers.get('x-test-user','alice'))
            return await call_next(request)
        self.store = install_test_features(app,root,root/'data',CredentialKeyring(key,data_dirs=(root/'data',)))
        self.store.configure_api2api({'engine':'newapi','base_url':'http://127.0.0.1:3001/v1','management_username':'manager',
                                     'management_password':'management-secret','initial_quota':100,'enabled':True})
        self.engine = EngineFixture()
        app.state.newapi_gateway.client = httpx.AsyncClient(transport=httpx.MockTransport(self.engine))
        self.client = self.enterContext(TestClient(app))

    def create(self, user='alice'):
        response = self.client.post('/api/api2api/keys',headers={'x-test-user':user},json={'name':'Development'})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()['item']

    def test_key_audit_is_admin_only_and_contains_no_credentials(self):
        item=self.create()
        path='/api/api2api/keys/'+item['id']
        self.assertEqual(self.client.post(path+'/reveal').status_code,200)
        self.assertEqual(self.client.put(path+'/enabled',json={'enabled':False}).status_code,200)
        self.assertEqual(self.client.delete(path).status_code,200)
        self.assertEqual(self.client.get('/api/admin/api2api/audit').status_code,403)
        response=self.client.get('/api/admin/api2api/audit',headers={'x-test-user':'admin'})
        self.assertEqual(response.status_code,200,response.text)
        rows=response.json()['items']
        self.assertEqual([row['action'] for row in rows],['revoked','disabled','revealed','created'])
        self.assertTrue(all(row['actor_id']=='alice' and row['outcome']=='succeeded' and row['owner_id']=='alice' for row in rows))
        self.assertNotIn(item['api_key'],response.text)
        self.assertNotIn('management-secret',response.text)
        self.assertNotIn('key_hash',response.text)
        self.assertEqual(rows[-1]['key_id'],item['id'])

    def test_creation_applies_all_settings_before_the_key_is_returned(self):
        payload={'name':'Configured creation','models':['gpt-4o-mini'],'expired_at':4102444800,
                 'unlimited_quota':False,'remaining_quota':120,'allow_user_quota_query':False,
                 'allowed_ips':'192.0.2.5\n198.51.100.5/24'}
        response=self.client.post('/api/api2api/keys',json=payload)
        self.assertEqual(response.status_code,201,response.text)
        self.assertIn('no-store',response.headers['cache-control'])
        item=response.json()['item'];token=self.engine.tokens[1]
        self.assertEqual(token['expired_time'],4102444800)
        self.assertEqual(token['remain_quota'],120)
        self.assertFalse(token['unlimited_quota']);self.assertFalse(token['allow_user_quota_query'])
        self.assertTrue(token['model_limits_enabled']);self.assertEqual(token['model_limits'],'gpt-4o-mini')
        self.assertEqual(token['group'],'default');self.assertEqual(token.get('allow_ips',''),'')
        self.assertEqual(self.store.gateway_key(item['id'],'alice')['caller_ip_allowlist'],'192.0.2.5\n198.51.100.0/24')
        calls=[(method,body) for method,path,body in self.engine.calls if path=='/api/token/']
        self.assertEqual([method for method,body in calls if method in {'POST','PUT'}],['POST'])
        self.assertEqual(self.client.post('/api/api2api/keys/'+item['id']+'/reveal',headers={'x-test-user':'bob'}).status_code,404)
        listed=self.client.get('/api/api2api/keys').json()['items'][0]
        self.assertNotIn(item['api_key'],json.dumps(listed));self.assertEqual(listed['remaining_quota'],120)

    def test_invalid_creation_settings_cannot_issue_or_register_a_token(self):
        invalid=[{'models':['unavailable-model']},{'models':'gpt-4o-mini'},{'expired_at':0},{'expired_at':True},
                 {'unlimited_quota':'false'},{'remaining_quota':-1},{'remaining_quota':True},
                 {'allow_user_quota_query':1},{'allowed_ips':'example.com'},{'group':'other'},
                 {'name':' '},{'name':'x'*81},{'expected_remaining_quota':0}]
        for settings in invalid:
            response=self.client.post('/api/api2api/keys',json={'name':'Invalid creation',**settings})
            self.assertEqual(response.status_code,400,(settings,response.text))
        self.assertEqual(self.engine.tokens,{})
        self.assertEqual(self.store.list_api_keys('alice'),[])
        self.assertFalse(any(method=='POST' and path=='/api/token/' for method,path,body in self.engine.calls))

    def test_usage_log_pagination_reads_all_retained_rows_and_enforces_exact_owner(self):
        item=self.create()
        self.create('bob')
        alice=self.store.gateway_user('http://127.0.0.1:3001','alice',create=False)
        bob=self.store.gateway_user('http://127.0.0.1:3001','bob',create=False)
        for i in range(145,0,-1):
            self.engine.logs.append({'id':i,'user_id':alice['remote_id'],'username':alice['username'],
                'created_at':1600000000+i,'type':2,'model_name':'gpt-4o-mini','quota':10,'token_id':1,
                'prompt_tokens':12,'completion_tokens':24,'use_time':2,'is_stream':True,'group':'default',
                'request_id':'request-'+str(i),'other':json.dumps({'frt':100,'model_ratio':1,'admin_info':{'api_key':'upstream-secret'}}),
                'content':'Bearer private-upstream-secret'})
        self.engine.logs.append({'id':146,'user_id':bob['remote_id'],'username':bob['username'],'quota':999})
        first=self.client.get('/api/api2api/logs?page_size=100').json()
        second=self.client.get('/api/api2api/logs?page=2&page_size=100').json()
        self.assertEqual(first['total'],145)
        self.assertEqual(len(first['items']),100)
        self.assertEqual(len(second['items']),45)
        self.assertFalse({row['id'] for row in first['items']} & {row['id'] for row in second['items']})
        self.assertEqual(first['items'][0]['token_name'],item['name'])
        self.assertEqual(first['stats']['quota'],1450)
        self.assertEqual(first['display']['divisor'],500000)
        self.assertNotIn('upstream-secret',json.dumps(first))
        self.assertNotIn('content',first['items'][0])
        self.assertTrue(all(query['username']==alice['username'] for query in self.engine.log_queries))
        self.assertEqual(self.client.get('/api/api2api/logs?username='+bob['username']).status_code,400)
        self.assertEqual(self.client.get('/api/api2api/logs?page=0').status_code,400)
        self.engine.corrupt_log_owner=True
        self.assertEqual(self.client.get('/api/api2api/logs').status_code,502)

    def test_model_catalog_is_available_before_issuing_a_personal_key(self):
        response=self.client.post('/api/api2api/models')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual([row['id'] for row in response.json()['items']],['gpt-4o-mini'])
        self.assertEqual(self.store.list_api_keys('alice'),[])
        alice=self.store.gateway_user('http://127.0.0.1:3001','alice',create=False)
        self.client.post('/api/api2api/models',headers={'x-test-user':'bob'})
        bob=self.store.gateway_user('http://127.0.0.1:3001','bob',create=False)
        self.assertNotEqual(alice['remote_id'],bob['remote_id'])

    def test_catalog_uses_user_permissions_and_public_pricing_without_secrets_or_hidden_groups(self):
        self.engine.model_names=['gpt-4o-mini','secret-model']
        self.store.configure_api2api({**self.store.api2api_config_public(),'model_allowlist':['gpt-4o-mini']})
        response=self.client.post('/api/api2api/models')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertEqual(len(data['items']),1)
        row=data['items'][0]
        self.assertEqual(row['vendor'],'OpenAI')
        self.assertEqual(row['prices'],{'input':.15,'output':.6,'cache_read':.075})
        self.assertEqual(row['groups'],[{'id':'default','ratio':1}])
        self.assertEqual(row['endpoints'],[{'id':'openai','path':'/v1/chat/completions','method':'POST'}])
        self.assertEqual(row['performance']['success_rate'],99.5)
        for value in ('secret-model','hidden-vendor','private-group','do-not-return','management-secret','auth-'):
            self.assertNotIn(value,response.text)
        self.assertEqual(self.store.list_api_keys('alice'),[])
        self.engine.pricing_failure=True
        fallback=self.client.post('/api/api2api/models').json()
        self.assertFalse(fallback['pricing_available'])
        self.assertEqual(fallback['items'][0]['id'],'gpt-4o-mini')
        self.assertEqual(fallback['items'][0]['prices'],{})

    def test_model_performance_checks_access_and_only_exposes_allowed_group_metrics(self):
        self.client.post('/api/api2api/models')
        response=self.client.get('/api/api2api/models/performance?model=gpt-4o-mini')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual([row['id'] for row in response.json()['groups']],['default'])
        self.assertNotIn('credential',response.text)
        self.assertNotIn('private-group',response.text)
        self.assertEqual(self.client.get('/api/api2api/models/performance?model=secret-model').status_code,404)
        self.assertEqual(self.client.get('/api/api2api/models/performance?model=').status_code,422)

    def test_usage_export_contains_all_owned_metadata_and_cleans_success_and_failed_files(self):
        self.create()
        owner=self.store.gateway_user('http://127.0.0.1:3001','alice',create=False)
        for index in range(145,0,-1):
            self.engine.logs.append({'id':index,'user_id':owner['remote_id'],'username':owner['username'],
                'created_at':1600000000,'type':2,'model_name':'=HYPERLINK("unsafe")','quota':10,
                'request_id':str(index),'content':'never-export-content','other':'{"admin_info":{"key":"secret"}}'})
        response=self.client.get('/api/api2api/logs/export?format=csv')
        self.assertEqual(response.status_code,200,response.text)
        rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows),145)
        self.assertEqual(rows[0]['model_name'],'\'=HYPERLINK("unsafe")')
        self.assertEqual(rows[0]['charged_amount'],'0.00002')
        self.assertEqual(response.headers['x-export-count'],'145')
        self.assertNotIn('never-export-content',response.text)
        self.assertEqual(list((self.store.root/'api_log_exports').glob('*')),[])
        response=self.client.get('/api/api2api/logs/export?format=jsonl')
        self.assertEqual(len(response.text.splitlines()),145)
        self.assertEqual(json.loads(response.text.splitlines()[0])['model_name'],'=HYPERLINK("unsafe")')
        self.engine.logs[100]['id']=self.engine.logs[99]['id']
        failed=self.client.get('/api/api2api/logs/export?format=jsonl')
        self.assertEqual(failed.status_code,409,failed.text)
        self.assertEqual(list((self.store.root/'api_log_exports').glob('*')),[])
        self.assertEqual(self.client.get('/api/api2api/logs/export?format=jsonl&username=bob').status_code,400)

    def test_key_limit_edit_preserves_key_state_quota_and_rejects_stale_or_foreign_writes(self):
        item=self.create()
        path='/api/api2api/keys/'+item['id']
        row=self.client.get('/api/api2api/keys').json()['items'][0]
        self.client.put(path+'/enabled',json={'enabled':False})
        payload={'models':['gpt-4o-mini'],'expired_at':4102444800,'revision':row['limits_revision']}
        response=self.client.put(path+'/limits',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        token=self.engine.tokens[1]
        self.assertEqual(token['status'],2)
        self.assertEqual(token['remain_quota'],93)
        self.assertEqual(token['name'],'mw_'+item['id'])
        self.assertEqual(token['model_limits'],'gpt-4o-mini')
        self.assertEqual(self.client.post(path+'/reveal').json()['api_key'],item['api_key'])
        self.assertEqual(self.client.put(path+'/limits',json=payload).status_code,409)
        self.assertEqual(self.client.put(path+'/limits',headers={'x-test-user':'bob'},json=payload).status_code,404)
        self.assertEqual(self.client.put(path+'/limits',json={**payload,'remain_quota':99999}).status_code,400)
        config=self.store.api2api_config_public()
        self.store.configure_api2api({**config,'model_allowlist':['gpt-4o-mini']})
        row=self.client.get('/api/api2api/keys').json()['items'][0]
        self.assertEqual(self.client.put(path+'/limits',json={'models':[],'expired_at':-1,'revision':row['limits_revision']}).status_code,200)
        self.assertTrue(self.engine.tokens[1]['model_limits_enabled'])
        self.assertEqual(self.engine.tokens[1]['model_limits'],'gpt-4o-mini')

    def test_ip_edit_preserves_gateway_rules_and_rejects_invalid_stale_and_foreign_changes(self):
        item=self.create();path='/api/api2api/keys/'+item['id']
        self.engine.tokens[1]['allow_ips']='127.0.0.1'
        current=self.client.get('/api/api2api/keys').json()['items'][0]
        self.assertEqual(current['allowed_ips'],'')
        self.assertEqual(current['gateway_allowed_ips'],'127.0.0.1')
        payload={'models':[],'expired_at':-1,'revision':current['limits_revision'],'allowed_ips':'192.0.2.5\n198.51.100.5/24'}
        response=self.client.put(path+'/limits',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.engine.tokens[1]['allow_ips'],'127.0.0.1')
        row=self.client.get('/api/api2api/keys').json()['items'][0]
        self.assertEqual(row['allowed_ips'],'192.0.2.5\n198.51.100.0/24')
        self.assertNotEqual(current['limits_revision'],row['limits_revision'])
        self.assertEqual(self.client.put(path+'/limits',json=payload).status_code,409)
        self.assertEqual(self.client.put(path+'/limits',headers={'x-test-user':'bob'},json={**payload,'revision':row['limits_revision']}).status_code,404)
        self.assertEqual(self.client.put(path+'/limits',json={**payload,'revision':row['limits_revision'],'allowed_ips':'example.com'}).status_code,400)
        self.engine.fail_toggle=True
        self.assertEqual(self.client.put(path+'/limits',json={**payload,'revision':row['limits_revision'],'allowed_ips':''}).status_code,502)
        self.assertEqual(self.store.gateway_key(item['id'])['caller_ip_allowlist'],row['allowed_ips'])

    def test_key_editor_updates_own_name_quota_and_query_permission_without_changing_state_or_group(self):
        item=self.create();path='/api/api2api/keys/'+item['id']
        self.client.put(path+'/enabled',json={'enabled':False})
        row=self.client.get('/api/api2api/keys').json()['items'][0]
        payload={'name':'Laptop renamed','models':[],'expired_at':-1,'revision':row['limits_revision'],
                 'remaining_quota':55,'expected_remaining_quota':93,'unlimited_quota':False,'allow_user_quota_query':False}
        response=self.client.put(path+'/limits',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        token=self.engine.tokens[1]
        self.assertEqual(token['name'],'mw_'+item['id'])
        self.assertEqual(token['status'],2)
        self.assertEqual(token['group'],'default')
        self.assertEqual(token['remain_quota'],55)
        self.assertFalse(token['unlimited_quota'])
        self.assertFalse(token['allow_user_quota_query'])
        updated=self.client.get('/api/api2api/keys').json()['items'][0]
        self.assertEqual(updated['name'],'Laptop renamed')
        self.assertEqual(updated['remaining_quota'],55)
        self.assertFalse(updated['allow_user_quota_query'])
        self.assertNotEqual(updated['limits_revision'],row['limits_revision'])
        self.assertEqual(self.client.put(path+'/limits',json=payload).status_code,409)
        self.assertEqual(self.client.put(path+'/limits',headers={'x-test-user':'bob'},json={**payload,'revision':updated['limits_revision']}).status_code,404)

    def test_key_editor_preserves_live_balance_unless_explicitly_changed_and_rejects_invalid_settings(self):
        item=self.create();path='/api/api2api/keys/'+item['id']
        row=self.client.get('/api/api2api/keys').json()['items'][0]
        payload={'name':'Renamed','models':[],'expired_at':-1,'revision':row['limits_revision']}
        self.engine.tokens[1]['remain_quota']=81
        response=self.client.put(path+'/limits',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.engine.tokens[1]['remain_quota'],81)
        row=self.client.get('/api/api2api/keys').json()['items'][0]
        payload['revision']=row['limits_revision']
        self.assertEqual(self.client.put(path+'/limits',json={**payload,'remaining_quota':100,'expected_remaining_quota':93}).status_code,409)
        for invalid in ({'name':' '},{'name':'x'*81},{'remaining_quota':-1,'expected_remaining_quota':81},
                        {'remaining_quota':True,'expected_remaining_quota':81},{'unlimited_quota':'false'},
                        {'allow_user_quota_query':1},{'group':'other'}):
            self.assertEqual(self.client.put(path+'/limits',json={**payload,**invalid}).status_code,400,invalid)
        self.assertEqual(self.engine.tokens[1]['remain_quota'],81)
        self.assertEqual(self.store.gateway_key(item['id'])['name'],'Renamed')

    def test_relay_uses_verified_client_ip_not_spoofed_headers_and_skips_blocked_upstream_calls(self):
        item=self.create()
        self.store.set_api_key_ips(item['id'],'alice','192.0.2.42',expected='')
        seen=[]
        async def upstream(request):
            seen.append(request)
            return httpx.Response(200,json={'choices':[]})
        relay=httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        headers={'Authorization':'Bearer '+item['api_key']}
        body={'model':'gpt-4o-mini','messages':[]}
        def caller(address,app=None):
            client=TestClient(app or self.app,client=(address,12345))
            self.addCleanup(client.close)
            return client
        with patch('mailapi_shared.test_features.httpx.AsyncClient',return_value=relay):
            blocked=caller('198.51.100.5').post('/api2api/v1/chat/completions',json=body,headers={**headers,'X-Forwarded-For':'192.0.2.42','X-Real-IP':'192.0.2.42','Forwarded':'for=192.0.2.42'})
            self.assertEqual(blocked.status_code,403,blocked.text)
            self.assertEqual(blocked.json()['error']['code'],'ip_not_allowed')
            self.assertEqual(seen,[])
            self.assertEqual(caller('192.0.2.42').post('/api2api/v1/chat/completions',json=body,headers=headers).status_code,200)
            self.assertEqual(caller('::ffff:192.0.2.42').get('/api2api/v1/models',headers=headers).status_code,200)
            ingress=ProxyIngress(self.app,'https://mail.example',['10.0.0.10'])
            forwarded={**headers,'Host':'mail.example','X-Forwarded-Proto':'https','X-Forwarded-For':'198.51.100.99, 192.0.2.42'}
            self.assertEqual(caller('10.0.0.10',ingress).post('/api2api/v1/chat/completions',json=body,headers=forwarded).status_code,200)
            self.assertEqual(caller('10.0.0.10',ingress).post('/api2api/v1/chat/completions',json=body,headers={**forwarded,'X-Forwarded-For':'192.0.2.42, 198.51.100.99'}).status_code,403)
            self.assertEqual(caller('10.0.0.11',ingress).post('/api2api/v1/chat/completions',json=body,headers=forwarded).status_code,403)
            self.assertEqual(caller('127.0.0.1',ingress).post('/api2api/v1/chat/completions',json=body,headers=forwarded).status_code,403)
            self.assertEqual(len(seen),3)
            self.assertTrue(all('x-forwarded-for' not in request.headers and 'x-real-ip' not in request.headers for request in seen))

    def test_audit_tracks_denied_owner_access_and_uncertain_remote_revocation(self):
        item=self.create()
        path='/api/api2api/keys/'+item['id']
        self.assertEqual(self.client.post(path+'/reveal',headers={'x-test-user':'bob'}).status_code,404)
        self.engine.fail_delete=True
        self.assertEqual(self.client.delete(path).status_code,502)
        admin={'x-test-user':'admin'}
        data=self.client.get('/api/admin/api2api/audit',headers=admin).json()
        self.assertEqual(data['items'][0]['outcome'],'uncertain')
        self.assertEqual(data['items'][1]['actor_id'],'bob')
        self.assertEqual(data['items'][1]['outcome'],'failed')
        self.assertEqual(data['items'][1]['owner_id'],'')
        self.assertEqual(self.client.get('/api/admin/api2api/audit?q=Development&action=created',headers=admin).json()['total'],1)
        self.assertEqual(self.client.get('/api/admin/api2api/audit?page_size=1',headers=admin).status_code,400)
        self.assertEqual(self.client.get('/api/admin/api2api/audit?start=2026-11-01',headers=admin).status_code,400)

    def test_http_configuration_is_native_only_and_omitted_engine_keeps_native_keys(self):
        item=self.create()
        response=self.client.put('/api/admin/api2api/config',headers={'x-test-user':'admin'},
                                 json={'engine':'legacy','base_url':'https://upstream.invalid/v1','upstream_key':'legacy-secret'})
        self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(self.store.api2api_config_public()['engine'],'newapi')
        self.assertIsNotNone(self.store.authenticate_api_key(item['api_key']))
        payload={'base_url':'http://127.0.0.1:3001/v1','management_username':'manager','enabled':True}
        response=self.client.put('/api/admin/api2api/config',headers={'x-test-user':'admin'},json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['engine'],'newapi')
        self.assertEqual(self.client.put('/api/admin/api2api/config',json=payload).status_code,403)
        self.assertIsNotNone(self.store.authenticate_api_key(item['api_key']))

    def test_old_shared_configuration_keeps_history_but_cannot_issue_or_relay_keys(self):
        self.store.configure_api2api({'engine':'legacy','base_url':'https://old-upstream.invalid/v1','upstream_key':'old-secret','enabled':True})
        item=self.store.create_api_key('alice','Historical')
        count=len(self.store.list_api_keys('alice'))
        self.assertEqual(self.client.post('/api/api2api/keys',json={'name':'Blocked'}).status_code,503)
        self.assertEqual(len(self.store.list_api_keys('alice')),count)
        row=self.client.get('/api/api2api/keys').json()['items'][0]
        self.assertFalse(row['enabled'] or row['can_toggle'] or row['can_copy'])
        self.assertEqual(self.client.put('/api/api2api/keys/'+item['id']+'/enabled',json={'enabled':True}).status_code,503)
        response=self.client.post('/api2api/v1/chat/completions',headers={'Authorization':'Bearer '+item['api_key']},json={'model':'a'})
        self.assertEqual(response.status_code,503)
        self.assertFalse(self.engine.calls)
        self.assertEqual(self.client.get('/api/api2api/engine').json()['engine'],'newapi')

    def test_owner_scoping_validation_and_admin_boundaries(self):
        alice, bob = self.create(), self.create('bob')
        self.assertEqual([row['id'] for row in self.client.get('/api/api2api/keys').json()['items']],[alice['id']])
        self.assertEqual(self.client.delete('/api/api2api/keys/'+bob['id']).status_code,404)
        for route in ('config','channels','usage','keys'):
            self.assertEqual(self.client.get('/api/admin/api2api/'+route).status_code,403)
        for payload in ([],{'name':''},{'name':'x'*81}):
            self.assertEqual(self.client.post('/api/api2api/keys',json=payload).status_code,400)
        channels = self.client.get('/api/admin/api2api/channels',headers={'x-test-user':'admin'})
        self.assertEqual(channels.status_code,200,channels.text)
        self.assertNotIn('never-return',channels.text)
        self.assertNotIn('management-secret',self.client.get('/api/admin/api2api/config',headers={'x-test-user':'admin'}).text)
        self.assertTrue(self.client.get('/api/api2api/engine').json()['linked'])

    def test_key_copy_and_enabled_routes_require_ownership_and_do_not_cache_secrets(self):
        item=self.create()
        base='/api/api2api/keys/'+item['id']
        copied=self.client.post(base+'/reveal')
        self.assertEqual(copied.status_code,200,copied.text)
        self.assertEqual(copied.json()['api_key'],item['api_key'])
        self.assertIn('no-store',copied.headers['cache-control'])
        self.assertEqual(self.client.post(base+'/reveal',headers={'x-test-user':'bob'}).status_code,404)
        self.assertEqual(self.client.put(base+'/enabled',headers={'x-test-user':'bob'},json={'enabled':False}).status_code,404)
        self.assertEqual(self.client.put(base+'/enabled',json={'enabled':1}).status_code,400)
        self.assertEqual(self.client.put(base+'/enabled',json={'enabled':False}).status_code,200)
        self.assertIsNone(self.store.authenticate_api_key(item['api_key']))
        self.assertEqual(self.client.put(base+'/enabled',json={'enabled':True}).status_code,200)
        self.assertIsNotNone(self.store.authenticate_api_key(item['api_key']))
        self.client.delete(base)
        self.assertEqual(self.client.put(base+'/enabled',json={'enabled':True}).status_code,409)
        self.assertEqual(self.client.post(base+'/reveal').status_code,409)

    def test_native_relay_preserves_bytes_avoids_management_and_checks_current_owner(self):
        key = self.create()
        before = len(self.engine.calls)
        seen = []
        async def upstream(request):
            seen.append(request)
            return httpx.Response(200,json={'choices':[],'usage':{'prompt_tokens':2,'completion_tokens':3,'total_tokens':5}})
        relay = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        body = b'{ "model":"gpt-4o-mini", "temperature":0.23, "messages":[] }'
        headers = {'Authorization':'Bearer '+key['api_key'],'Content-Type':'application/json','Cookie':'website-session=secret','OpenAI-Beta':'responses=v1'}
        with patch('mailapi_shared.test_features.httpx.AsyncClient',return_value=relay):
            response = self.client.post('/api2api/v1/chat/completions?test=1',content=body,headers=headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(seen[0].content,body)
        self.assertEqual(seen[0].headers['authorization'],headers['Authorization'])
        self.assertNotIn('cookie',seen[0].headers)
        self.assertEqual(seen[0].headers['openai-beta'],'responses=v1')
        self.assertEqual(len(self.engine.calls),before)
        self.assertEqual(self.client.get('/api/api2api/usage').json()['total_tokens'],5)
        self.users['alice']['status']='disabled'
        self.assertEqual(self.client.post('/api2api/v1/chat/completions',content=body,headers=headers).status_code,401)
        self.users.pop('alice')
        self.assertEqual(self.client.post('/api2api/v1/chat/completions',content=body,headers=headers).status_code,401)
        self.assertEqual(len(seen),1)

    def test_usage_events_survive_fragmentation_and_later_usage_free_events(self):
        collector = _StreamUsage()
        payload = b'data: {"type":"response.completed","response":{"usage":{"input_tokens":3,"output_tokens":4,"total_tokens":7}}}\n\ndata: {"type":"response.done"}\n\ndata: [DONE]\n'
        for start in range(0,len(payload),13):
            collector.feed(payload[start:start+13])
        self.assertEqual(collector.usage,(3,4,7))

    def test_broken_stream_is_recorded_as_interrupted_even_after_http_200(self):
        key = self.create()
        class BrokenStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"choices":[]}\n\n'
                raise httpx.ReadError('fixture disconnect')
        async def upstream(request):
            return httpx.Response(200,headers={'Content-Type':'text/event-stream'},stream=BrokenStream())
        relay = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        with patch('mailapi_shared.test_features.httpx.AsyncClient',return_value=relay):
            with self.assertRaises(httpx.ReadError):
                self.client.post('/api2api/v1/chat/completions',json={'model':'gpt-4o-mini','stream':True},headers={'Authorization':'Bearer '+key['api_key']})
        row = self.client.get('/api/api2api/usage').json()['items'][0]
        self.assertEqual(row['error'],'ReadError')
        self.assertEqual(row['status_code'],200)
