import asyncio
from base64 import b64encode
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


class EngineFixture:
    """The external NewAPI HTTP boundary, using its rc.51 request contracts."""
    def __init__(self):
        self.calls = []
        self.users = {'manager': {'id': 1, 'password': 'management-secret'}}
        self.tokens = {}
        self.fail_delete = False

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
            self.tokens[token_id] = {'id': token_id, 'user_id': owner, 'name': body['name'], 'status': 1,
                                     'key': f'sk-engine-issued-{token_id:032d}', 'used_quota': 7, 'remain_quota': 93}
            return self.response()
        if path == '/api/token/search':
            return self.response({'items': [x for x in self.tokens.values() if x['user_id'] == owner and x['name'] == request.url.params['keyword']]})
        if path == '/api/token/':
            return self.response({'items': [x for x in self.tokens.values() if x['user_id'] == owner], 'total': len(self.tokens)})
        if path.endswith('/key'):
            token_id = int(path.split('/')[-2])
            assert self.tokens[token_id]['user_id'] == owner
            return self.response({'key': self.tokens[token_id]['key']})
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
            return self.response(['gpt-4o-mini'])
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
