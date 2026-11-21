"""Verify a pinned NewAPI release against a disposable local upstream.

Run with the local uv environment and --binary pointing at the official macOS
arm64 v1.0.0-rc.51 release. No website or production data is read or changed.
"""
import argparse
from base64 import b64encode
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from fastapi import FastAPI, Request
import uvicorn
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mailapi_shared.credential_crypto import CredentialKeyring
from mailapi_shared.test_features import install_test_features

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binary', type=Path, required=True)
binary = parser.parse_args().binary.resolve()
expected_sha256 = '56d434ee244496d078825558562878a63f8c4af8798c3a1238c907e5915c150a'
if hashlib.sha256(binary.read_bytes()).hexdigest() != expected_sha256:
    parser.error('Expected the official macOS arm64 v1.0.0-rc.51 release checksum')
seen = []
stream_received = threading.Event()
stream_forwarded = []


class Upstream(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        body = json.loads(raw)
        seen.append(body)
        usage = {'prompt_tokens': 3, 'completion_tokens': 5, 'total_tokens': 8}
        if body.get('stream'):
            first = b'data: {"id":"fixture","object":"chat.completion.chunk","model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":"fixture-response"},"finish_reason":null}]}\n\n'
            first += b'data: {"id":"fixture","object":"chat.completion.chunk","model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":"."},"finish_reason":null}]}\n\n'
            last = ('data: '+json.dumps({'id':'fixture','object':'chat.completion.chunk','model':'gpt-4o-mini','choices':[],'usage':usage})+'\n\ndata: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(first)+len(last)))
            self.end_headers()
            self.wfile.write(first)
            self.wfile.flush()
            stream_forwarded.append(stream_received.wait(8))
            self.wfile.write(last)
            self.wfile.flush()
        else:
            response = json.dumps({'id':'fixture','object':'chat.completion','model':'gpt-4o-mini','choices':[{'index':0,'message':{'role':'assistant','content':'fixture-response'},'finish_reason':'stop'}],'usage':usage}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(response)))
            self.end_headers()
            self.wfile.write(response)


def call(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    data = response.json()
    if response.status_code >= 400 or data.get('success') is False:
        raise RuntimeError(f'{method} {path}: HTTP {response.status_code}: {data.get("message", "request failed")}')
    return data


with tempfile.TemporaryDirectory(prefix='mailapi-engine-probe-') as directory:
    work = Path(directory)
    log = (work/'engine.log').open('wb')
    upstream = ThreadingHTTPServer(('127.0.0.1',0),Upstream)
    thread = threading.Thread(target=upstream.serve_forever,daemon=True)
    thread.start()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0))
        port = reservation.getsockname()[1]
    env = {name:os.environ[name] for name in ('PATH','HOME','TMPDIR','LANG') if name in os.environ}
    env.update({'HOST':'127.0.0.1','PORT':str(port),'SQLITE_PATH':str(work/'newapi.db'),'SESSION_SECRET':secrets.token_urlsafe(48),'TRUSTED_PROXIES':'none','GIN_MODE':'release','DEFAULT_LANGUAGE':'en'})
    process = subprocess.Popen([str(binary),'--log-dir',str(work/'logs')],cwd=work,env=env,stdout=log,stderr=log)
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=20,trust_env=False) as client:
            for attempt in range(100):
                if process.poll() is not None:
                    raise RuntimeError('Engine exited before readiness')
                try:
                    readiness = client.get('/api/setup')
                    if readiness.status_code==200:
                        assert readiness.json()['data']['status'] is False, 'Refuse an initialized gateway'
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else: raise RuntimeError('Engine readiness timeout')
            password = secrets.token_urlsafe(32)
            call(client,'POST','/api/setup',json={'username':'mailapitest','password':password,'confirmPassword':password,'SelfUseModeEnabled':False,'DemoSiteEnabled':False})
            login = call(client,'POST','/api/user/login',json={'username':'mailapitest','password':password})
            client.headers['Authorization']='Bearer '+login['data']['access_token']
            call(client,'POST','/api/channel/',json={'mode':'single','channel':{'type':1,'name':'isolated-fixture','key':'fixture-upstream-only','base_url':f'http://127.0.0.1:{upstream.server_port}','models':'gpt-4o-mini','group':'default','setting':json.dumps({'pass_through_body_enabled':True})}})
            call(client,'POST','/api/token/',json={'name':'probe-key','expired_time':-1,'unlimited_quota':True,'remain_quota':0,'model_limits_enabled':True,'model_limits':'gpt-4o-mini','group':'default'})
            tokens = call(client,'GET','/api/token/')['data']['items']
            key = call(client,'POST',f'/api/token/{tokens[0]["id"]}/key')['data']['key']
            headers={'Authorization':'Bearer '+(key if key.startswith('sk-') else 'sk-'+key)}
            payload={'model':'gpt-4o-mini','messages':[{'role':'user','content':'fixture question'}],'temperature':0.2,'top_p':0.9,'max_tokens':32}
            response=client.post('/v1/chat/completions',json=payload,headers=headers)
            assert response.status_code==200, ('relay status',response.status_code,response.json().get('error',{}).get('message'))
            assert response.json()['choices'][0]['message']['content']=='fixture-response'
            assert seen[-1]==payload, 'Relay changed request payload'
            with client.stream('POST','/v1/chat/completions',json={**payload,'stream':True,'stream_options':{'include_usage':True}},headers=headers) as response:
                assert response.status_code==200, ('stream status',response.status_code)
                chunks=[]
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    if b'fixture-response' in b''.join(chunks): stream_received.set()
                assert b'[DONE]' in b''.join(chunks)
            assert stream_forwarded==[True], 'Stream did not reach the client before completion'
            key_file = work/'website-key.json'
            key_file.write_text(json.dumps({'namespace':'engine-check','active':'one','keys':{'one':b64encode(AESGCM.generate_key(bit_length=256)).decode()}}))
            key_file.chmod(0o600)
            website_data = work/'website-data'
            website = FastAPI()
            users = {name:{'id':name,'username':name,'role':'admin' if name=='admin' else 'user','status':'active'} for name in ('alice-test','bob-test','admin')}
            class FixtureAccounts:
                def get_user(self, user_id, **kwargs):
                    return users.get(user_id)
            website.state.site_accounts = FixtureAccounts()
            @website.middleware('http')
            async def fixture_session(request: Request, call_next):
                # This fixture-only identity shortcut is never installed in the website.
                request.state.site_user = users.get(request.headers.get('x-test-user','alice-test'))
                return await call_next(request)
            store = install_test_features(website,work,website_data,CredentialKeyring(key_file,data_dirs=(website_data,)))
            store.configure_api2api({'engine':'newapi','base_url':f'http://127.0.0.1:{port}/v1','management_username':'mailapitest',
                                     'management_password':password,'initial_quota':1_000_000,'enabled':True,'model_allowlist':['gpt-4o-mini']})
            web_socket = socket.socket()
            web_socket.bind(('127.0.0.1',0))
            web_port = web_socket.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(website,log_level='error',access_log=False,proxy_headers=False))
            web_thread = threading.Thread(target=lambda:server.run(sockets=[web_socket]),daemon=True)
            web_thread.start()
            try:
                for _ in range(100):
                    if server.started: break
                    time.sleep(.05)
                assert server.started, 'Website readiness timeout'
                with httpx.Client(base_url=f'http://127.0.0.1:{web_port}',timeout=20,trust_env=False) as web:
                    alice = web.post('/api/api2api/keys',json={'name':'Alice development'}).json()['item']
                    bob = web.post('/api/api2api/keys',json={'name':'Bob development'},headers={'x-test-user':'bob-test'}).json()['item']
                    assert [row['id'] for row in web.get('/api/api2api/keys').json()['items']]==[alice['id']]
                    assert web.delete('/api/api2api/keys/'+bob['id']).status_code==404
                    bearer = {'Authorization':'Bearer '+alice['api_key']}
                    result = web.post('/api2api/v1/chat/completions',json=payload,headers=bearer)
                    assert result.status_code==200, ('website relay status',result.status_code)
                    assert result.json()['choices'][0]['message']['content']=='fixture-response'
                    assert seen[-1]==payload
                    stream_received.clear()
                    with web.stream('POST','/api2api/v1/chat/completions',json={**payload,'stream':True,'stream_options':{'include_usage':True}},headers=bearer) as result:
                        assert result.status_code==200
                        chunks=[]
                        for chunk in result.iter_bytes():
                            chunks.append(chunk)
                            if b'fixture-response' in b''.join(chunks): stream_received.set()
                        assert b'[DONE]' in b''.join(chunks)
                    assert stream_forwarded==[True,True], 'Website buffered the stream'
                    assert web.get('/api/api2api/usage').json()['total_tokens']==16
                    catalog = web.post('/api/api2api/models')
                    assert catalog.status_code==200 and 'gpt-4o-mini' in [row['id'] for row in catalog.json()['items']], 'Model catalog'
                    model=catalog.json()['items'][0]
                    assert catalog.json()['pricing_available'] and model['prices']['input']==.15 and model['prices']['output']==.6, ('Native model pricing',model['prices'])
                    assert model['groups'] and model['endpoints'], 'Native model groups and endpoints'
                    detail=web.get('/api/api2api/models/performance?model=gpt-4o-mini')
                    assert detail.status_code==200 and detail.json()['id']=='gpt-4o-mini', ('Native model performance',detail.status_code)
                    own = store.gateway_user(f'http://127.0.0.1:{port}','alice-test',create=False)
                    other = store.gateway_user(f'http://127.0.0.1:{port}','bob-test',create=False)
                    token = store.gateway_key(alice['id'],'alice-test')
                    # Old disposable records exercise retained-history access beyond
                    # the native self-service lookback and the first 100 rows.
                    with sqlite3.connect(work/'newapi.db') as db:
                        for owner, count in ((own,145),(other,1)):
                            for index in range(count):
                                db.execute('''INSERT INTO logs (user_id,username,created_at,type,token_id,token_name,model_name,quota,prompt_tokens,
                                    completion_tokens,use_time,is_stream,"group",request_id,other,content) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                                    (owner['remote_id'],owner['username'],1600000000+index,2,token['remote_id'],'mw_'+alice['id'],
                                     'probe-history',10,3,5,2,True,'default',f'probe-{index}',json.dumps({'frt':100,'admin_info':{'key':'secret-diagnostic'}}),'private-upstream-secret'))
                    history = '/api/api2api/logs?model_name=probe-history'
                    first,second = web.get(history).json(),web.get(history+'&page=2').json()
                    assert first.get('total')==145, ('Native retained history',first.get('detail'),first.get('total'))
                    assert len(first['items'])==100 and len(second['items'])==45
                    assert not ({row['id'] for row in first['items']} & {row['id'] for row in second['items']})
                    assert first['display']['unit']=='currency' and first['stats']['quota']==1450
                    assert 'secret-diagnostic' not in json.dumps(first) and 'private-upstream-secret' not in json.dumps(first)
                    exported = web.get('/api/api2api/logs/export?model_name=probe-history&format=jsonl')
                    assert exported.status_code==200, ('Native export',exported.status_code,exported.text[:150])
                    assert len(exported.text.splitlines())==145 and exported.headers['x-export-count']=='145'
                    for _ in range(20):
                        if not list((website_data/'api_log_exports').glob('*')):
                            break
                        time.sleep(.05)
                    assert not list((website_data/'api_log_exports').glob('*')), 'Temporary export was not cleaned'
                    assert web.get('/api/api2api/logs?username='+other['username']).status_code==400
                    assert web.get('/api/admin/api2api/audit').status_code==403
                    editable = web.get('/api/api2api/keys').json()['items'][0]
                    edited = web.put('/api/api2api/keys/'+alice['id']+'/limits',json={
                        'models':['gpt-4o-mini'],'expired_at':int(time.time())+86400,'revision':editable['limits_revision']})
                    assert edited.status_code==200, ('Native key edit',edited.status_code,edited.json().get('detail'))
                    for allowed, expected in (('192.0.2.42',403),('127.0.0.1',200),('',200)):
                        editable=web.get('/api/api2api/keys').json()['items'][0]
                        result=web.put('/api/api2api/keys/'+alice['id']+'/limits',json={
                            'models':['gpt-4o-mini'],'expired_at':editable['expired_at'],'allowed_ips':allowed,'revision':editable['limits_revision']})
                        assert result.status_code==200, ('IP policy edit',result.status_code)
                        count=len(seen)
                        result=web.post('/api2api/v1/chat/completions',json=payload,headers={**bearer,'X-Forwarded-For':'192.0.2.42','X-Real-IP':'192.0.2.42'})
                        assert result.status_code==expected, ('Caller IP enforcement',result.status_code,expected)
                        assert len(seen)==count+(expected==200), 'Denied IP reached upstream'
                    assert web.post('/api/api2api/keys/'+alice['id']+'/reveal').json()['api_key']==alice['api_key']
                    copied = web.post('/api/api2api/keys/'+alice['id']+'/reveal')
                    assert copied.status_code==200 and copied.json()['api_key']==alice['api_key']
                    assert 'no-store' in copied.headers['cache-control']
                    assert web.put('/api/api2api/keys/'+alice['id']+'/enabled',json={'enabled':False}).status_code==200
                    assert web.post('/api2api/v1/chat/completions',json=payload,headers=bearer).status_code==401
                    assert client.post('/v1/chat/completions',json=payload,headers=bearer).status_code>=400
                    assert web.put('/api/api2api/keys/'+alice['id']+'/enabled',json={'enabled':True}).status_code==200
                    assert web.post('/api2api/v1/chat/completions',json=payload,headers=bearer).status_code==200
                    info = web.get('/api/api2api/engine')
                    assert info.status_code==200 and info.json()['linked'], ('engine info',info.status_code)
                    channels = web.get('/api/admin/api2api/channels',headers={'x-test-user':'admin'})
                    assert channels.status_code==200 and channels.json()['items'], ('channels',channels.status_code)
                    assert 'fixture-upstream-only' not in channels.text
                    assert web.delete('/api/api2api/keys/'+alice['id']).status_code==200
                    assert web.post('/api2api/v1/chat/completions',json=payload,headers=bearer).status_code==401
                    assert client.post('/v1/chat/completions',json=payload,headers=bearer).status_code==401
                    assert web.post('/api2api/v1/chat/completions',json=payload,headers={'Authorization':'Bearer '+bob['api_key']}).status_code==200
                    events = web.get('/api/admin/api2api/audit',headers={'x-test-user':'admin'}).json()
                    assert {row['action'] for row in events['items']} >= {'created','updated','revealed','disabled','enabled','revoked'}
                    assert alice['api_key'] not in json.dumps(events) and password not in json.dumps(events)
                    disk = b''.join(p.read_bytes() for p in website_data.iterdir() if p.is_file())
                    assert alice['api_key'].encode() not in disk
                    assert password.encode() not in disk
            finally:
                server.should_exit=True
                web_thread.join(timeout=10)
                web_socket.close()

            print(json.dumps({'engine':'v1.0.0-rc.51','real_engine_relay':True,'request_parameters_preserved':True,
                              'streaming_before_completion':True,'website_http_native_keys':True,
                              'pause_resume_and_copy':True,'retained_logs_145_rows':True,'full_export_cleanup':True,'model_catalog':True,'key_limit_edit':True,'caller_ip_enforced':True,'spoofed_ip_headers_rejected':True,'admin_only_audit':True,'separate_engine_identities':True,'revocation_enforced':True,
                              'upstream':'local deterministic fixture','requests':len(seen)}),flush=True)
    finally:
        stream_received.set()
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill();process.wait()
        upstream.shutdown()
        upstream.server_close()
        log.close()
