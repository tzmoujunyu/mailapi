"""Verify a pinned NewAPI release against a disposable local upstream.

Run with the local uv environment and --binary pointing at the official macOS
arm64 v1.0.0-rc.51 release. No website or production data is read or changed.
"""
import argparse
import asyncio
from base64 import b64encode
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
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
from mailapi_shared.newapi_gateway import NewAPIGateway
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
            server = uvicorn.Server(uvicorn.Config(website,log_level='error',access_log=False))
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
                    info = web.get('/api/api2api/engine')
                    assert info.status_code==200 and info.json()['linked'], ('engine info',info.status_code)
                    channels = web.get('/api/admin/api2api/channels',headers={'x-test-user':'admin'})
                    assert channels.status_code==200 and channels.json()['items'], ('channels',channels.status_code)
                    assert 'fixture-upstream-only' not in channels.text
                    assert web.delete('/api/api2api/keys/'+alice['id']).status_code==200
                    assert web.post('/api2api/v1/chat/completions',json=payload,headers=bearer).status_code==401
                    assert client.post('/v1/chat/completions',json=payload,headers=bearer).status_code==401
                    assert web.post('/api2api/v1/chat/completions',json=payload,headers={'Authorization':'Bearer '+bob['api_key']}).status_code==200
                    disk = b''.join(p.read_bytes() for p in website_data.iterdir() if p.is_file())
                    assert alice['api_key'].encode() not in disk
                    assert password.encode() not in disk
            finally:
                server.should_exit=True
                web_thread.join(timeout=10)
                web_socket.close()

            print(json.dumps({'engine':'v1.0.0-rc.51','real_engine_relay':True,'request_parameters_preserved':True,
                              'streaming_before_completion':True,'website_http_native_keys':True,
                              'separate_engine_identities':True,'revocation_enforced':True,
                              'upstream':'local deterministic fixture','requests':len(seen)}),flush=True)
    finally:
        stream_received.set()
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill();process.wait()
        upstream.shutdown()
        upstream.server_close()
        log.close()
