"""Loopback-only full-website/NewAPI benchmark with disposable synthetic data.

Use the existing local uv or server Conda environment. The pinned engine binary
is supplied explicitly; no production data, credentials or live upstream is used.
"""
import argparse
import asyncio
from base64 import b64encode
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

import httpx

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
MODEL = 'gpt-4o-mini'
CHECKSUMS = {
    'Linux': 'a42f4559790021ffec8a609182cf142b430f822cf420270105422c800c519157',
    'Darwin': '56d434ee244496d078825558562878a63f8c4af8798c3a1238c907e5915c150a',
}


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def worker(mode, listen):
    import uvicorn
    if mode == 'website':
        from mailapi_shared import application
        application.run()
        return
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse
    app = FastAPI()
    state = {'active': 0, 'peak': 0, 'rejected': 0, 'accepted': 0}

    @app.get('/metrics')
    def metrics():
        return state

    @app.post('/metrics/reset')
    def reset_metrics():
        assert state['active'] == 0, 'The previous scenario is still running'
        state.update(peak=0, rejected=0, accepted=0)
        return state

    @app.post('/v1/chat/completions')
    async def upstream(request: Request):
        body = await request.json()
        profile = body['messages'][0]['content'].split(':', 1)[0]
        duration = 30 if profile == 'long' else 1
        if profile == 'cap20' and state['active'] >= 20:
            state['rejected'] += 1
            return JSONResponse({'error': {'message': 'Synthetic upstream concurrency limit 20',
                                          'type': 'rate_limit_error'}}, status_code=429,
                                headers={'Retry-After': '1'})
        state['active'] += 1
        state['accepted'] += 1
        state['peak'] = max(state['peak'], state['active'])

        async def content():
            try:
                await asyncio.sleep(.2)
                chunks = 60 if profile == 'long' else 10
                for index in range(chunks):
                    data = {'id': 'synthetic-benchmark', 'object': 'chat.completion.chunk', 'model': MODEL,
                            'choices': [{'index': 0, 'delta': {'content': 'x' * 64}, 'finish_reason': None}]}
                    yield ('data: ' + json.dumps(data) + '\n\n').encode()
                    await asyncio.sleep(duration / chunks)
                data = {'id': 'synthetic-benchmark', 'model': MODEL, 'choices': [],
                        'usage': {'prompt_tokens': 100, 'completion_tokens': chunks * 16,
                                  'total_tokens': 100 + chunks * 16}}
                yield ('data: ' + json.dumps(data) + '\n\ndata: [DONE]\n\n').encode()
            finally:
                state['active'] -= 1
        return StreamingResponse(content(), media_type='text/event-stream')
    uvicorn.run(app, host='127.0.0.1', port=listen, log_level='error', access_log=False)


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered)-1, int((len(ordered)-1)*fraction))], 2)


def summary(values):
    return {'median': percentile(values, .5), 'p95': percentile(values, .95), 'max': percentile(values, 1)}


def proc_sample(pid):
    if platform.system() == 'Linux':
        directory = Path('/proc') / str(pid)
        fields = (directory/'stat').read_text().rsplit(')', 1)[1].split()
        ticks = os.sysconf('SC_CLK_TCK')
        cpu = (int(fields[11]) + int(fields[12])) / ticks
        rss = int(fields[21]) * os.sysconf('SC_PAGE_SIZE') / 1024**2
        io = dict(line.split(':', 1) for line in (directory/'io').read_text().splitlines())
        return cpu, rss, len(list((directory/'fd').iterdir())), int(io['write_bytes'])
    # Linux /proc is used for the server report. macOS supports a local smoke
    # run without adding a monitoring dependency to the project environment.
    raw = subprocess.check_output(['ps', '-p', str(pid), '-o', 'time=,rss='], text=True).split()
    minutes, seconds = raw[0].split(':')[-2:]
    return int(minutes)*60+float(seconds), int(raw[1])/1024, 0, 0


async def load(base, key, concurrency, profile, seconds, web, pids):
    rows, probes, resources = [], [], []
    stop = asyncio.Event()
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    before = {name: proc_sample(pid) for name, pid in pids.items()}
    start = time.perf_counter()
    deadline = start + seconds

    async def observe():
        while not stop.is_set():
            resources.append((time.perf_counter(), {name: proc_sample(pid) for name, pid in pids.items()}))
            try:
                await asyncio.wait_for(stop.wait(), timeout=.1)
            except TimeoutError:
                pass

    async def probe():
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            while not stop.is_set():
                now = time.perf_counter()
                try:
                    response = await client.get(web+'/login')
                    probes.append({'ms': (time.perf_counter()-now)*1000, 'status': response.status_code})
                except httpx.HTTPError as error:
                    probes.append({'ms': (time.perf_counter()-now)*1000, 'status': type(error).__name__})
                try:
                    await asyncio.wait_for(stop.wait(), timeout=.2)
                except TimeoutError:
                    pass

    async with httpx.AsyncClient(limits=limits, timeout=65, trust_env=False) as client:
        async def run():
            while True:
                began, first, last = time.perf_counter(), None, None
                gaps, content_bytes, done, status = [], 0, False, None
                payload = {'model': MODEL, 'messages': [{'role': 'user', 'content': profile+':'+'a'*8192}],
                           'stream': True, 'stream_options': {'include_usage': True}, 'max_tokens': 2048}
                try:
                    async with client.stream('POST', base+'/chat/completions', json=payload,
                                             headers={'Authorization': 'Bearer '+key}) as response:
                        status = response.status_code
                        async for line in response.aiter_lines():
                            if not line.startswith('data: '):
                                continue
                            value = line[6:]
                            if value == '[DONE]':
                                done = True
                                continue
                            data = json.loads(value)
                            text = ''.join(choice.get('delta', {}).get('content', '') for choice in data.get('choices', []))
                            if text:
                                now = time.perf_counter()
                                if first is None:
                                    first = now
                                if last is not None:
                                    gaps.append((now-last)*1000)
                                last = now
                                content_bytes += len(text)
                except (httpx.HTTPError, ValueError) as error:
                    status = type(error).__name__
                expected = (60 if profile == 'long' else 10)*64
                rows.append({'status': status, 'ok': status == 200 and done and content_bytes == expected,
                             'ttft_ms': (first-began)*1000 if first else None,
                             'total_ms': (time.perf_counter()-began)*1000, 'gap_p95_ms': percentile(gaps, .95)})
                if profile in {'long', 'cap20'} or time.perf_counter() >= deadline:
                    break
                if not rows[-1]['ok']:
                    await asyncio.sleep(.1)
        observers = [asyncio.create_task(observe()), asyncio.create_task(probe())]
        await asyncio.gather(*(run() for _ in range(concurrency)))
        stop.set()
        await asyncio.gather(*observers)
    elapsed = time.perf_counter()-start
    after = {name: proc_sample(pid) for name, pid in pids.items()}
    resource_summary = {}
    for name in pids:
        peaks = [sample[name] for _, sample in resources]
        peak_cpu = 0
        for (t1, s1), (t2, s2) in zip(resources, resources[1:]):
            peak_cpu = max(peak_cpu, (s2[name][0]-s1[name][0])/(t2-t1)*100)
        resource_summary[name] = {'cpu_avg_percent_one_core': round((after[name][0]-before[name][0])/elapsed*100, 2),
            'cpu_peak_percent_one_core': round(peak_cpu, 2), 'rss_peak_mib': round(max(x[1] for x in peaks), 2),
            'rss_end_mib': round(after[name][1], 2), 'fd_peak': max(x[2] for x in peaks),
            'disk_write_mib': round((after[name][3]-before[name][3])/1024**2, 2)}
    successful = [row for row in rows if row['ok']]
    return {'profile': profile, 'concurrency': concurrency, 'requests': len(rows), 'success': len(successful),
            'errors': len(rows)-len(successful), 'status_counts': dict(Counter(str(row['status']) for row in rows)),
            'elapsed_s': round(elapsed, 2), 'requests_per_second': round(len(successful)/elapsed, 2),
            'ttft_ms': summary([row['ttft_ms'] for row in successful]),
            'total_ms': summary([row['total_ms'] for row in successful]),
            'gap_p95_ms': summary([row['gap_p95_ms'] for row in successful]),
            'web_probe_ms': summary([row['ms'] for row in probes]),
            'web_probe_errors': sum(row['status'] != 200 for row in probes), 'resources': resource_summary}


def main(args):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from mailapi_shared.credential_crypto import CredentialKeyring
    from mailapi_shared.site_accounts import SiteAccountStore
    from mailapi_shared.site_database import write_site_database
    from mailapi_shared.resource_database import create_resource_database
    from mailapi_shared.test_features import TestFeatureStore
    binary = args.binary.resolve()
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == CHECKSUMS[platform.system()], 'Unverified NewAPI binary'
    assert args.output.resolve().parent.is_dir(), 'Create the report directory first'
    os.umask(0o077)
    children = []
    def interrupted(*unused):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    with tempfile.TemporaryDirectory(prefix='mailapi-relay-bench-') as temporary:
        root = Path(temporary)
        data, keyfile = root/'data', root/'key.json'
        keyfile.write_text(json.dumps({'namespace': 'relay-benchmark', 'active': 'one',
                                      'keys': {'one': b64encode(AESGCM.generate_key(bit_length=256)).decode()}}))
        ring = CredentialKeyring(keyfile, data_dirs=(data,))
        store = TestFeatureStore(data, ring)
        password = secrets.token_urlsafe(32)
        identity = SiteAccountStore(root/'fixture-accounts.json')
        with patch('mailapi_shared.site_accounts.password_is_pwned', return_value=False):
            identity.ensure_admin(password)
        write_site_database(data/'site_accounts.sqlite3', identity._data, keyring=ring)
        create_resource_database(data, keyring=ring, oauth_encrypted=True)
        ports = {name: port() for name in ('upstream', 'engine', 'website')}
        urls = {name: f'http://127.0.0.1:{value}' for name, value in ports.items()}
        env = {name: os.environ[name] for name in ('PATH', 'HOME', 'LANG', 'TMPDIR') if name in os.environ}

        def launch(name, command, extras):
            logfile = (root/(name+'.log')).open('wb')
            process = subprocess.Popen(command, cwd=REPO, env={**env, **extras}, stdout=logfile, stderr=logfile)
            logfile.close()
            children.append(process)
            return process

        def ready(name, path):
            for _ in range(200):
                if any(child.poll() is not None for child in children):
                    for name in ('website', 'engine', 'upstream'):
                        path = root/(name+'.log')
                        if path.exists():
                            print(name+': '+path.read_text(errors='replace')[-5000:], file=sys.stderr)
                    raise RuntimeError('Isolated child exited')
                try:
                    if httpx.get(urls[name]+path, timeout=1, trust_env=False).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            raise RuntimeError('Readiness timeout: '+name)

        try:
            upstream = launch('upstream', [sys.executable, __file__, '--worker', 'upstream', '--port', str(ports['upstream'])], {})
            ready('upstream', '/metrics')
            engine = launch('engine', [str(binary), '--log-dir', str(root/'logs')], {
                'HOST': '127.0.0.1', 'PORT': str(ports['engine']), 'SQLITE_PATH': str(root/'newapi.db'),
                'SESSION_SECRET': secrets.token_urlsafe(48), 'TRUSTED_PROXIES': 'none', 'GIN_MODE': 'release',
                **({'SQL_MAX_OPEN_CONNS': str(args.engine_sql_connections),
                    'SQL_MAX_IDLE_CONNS': str(args.engine_sql_connections)} if args.engine_sql_connections else {})})
            ready('engine', '/api/setup')
            with httpx.Client(base_url=urls['engine'], timeout=20, trust_env=False) as client:
                def call(method, path, **kwargs):
                    response = client.request(method, path, **kwargs)
                    result = response.json()
                    if response.status_code >= 400 or result.get('success') is False:
                        raise RuntimeError(f'Isolated setup failed: {method} {path} HTTP {response.status_code}')
                    return result.get('data')
                call('POST', '/api/setup', json={'username': 'benchmark', 'password': password, 'confirmPassword': password,
                                               'SelfUseModeEnabled': False, 'DemoSiteEnabled': False})
                login = call('POST', '/api/user/login', json={'username': 'benchmark', 'password': password})
                client.headers['Authorization'] = 'Bearer '+login['access_token']
                call('POST', '/api/channel/', json={'mode': 'single', 'channel': {'type': 1, 'name': 'loopback-only',
                    'key': 'synthetic-upstream-only', 'base_url': urls['upstream'], 'models': MODEL, 'group': 'default',
                    'setting': json.dumps({'pass_through_body_enabled': True})}})
            store.configure_api2api({'engine': 'newapi', 'base_url': urls['engine']+'/v1',
                'management_username': 'benchmark', 'management_password': password, 'initial_quota': 1_000_000_000,
                'enabled': True, 'model_allowlist': [MODEL]})
            website = launch('website', [sys.executable, __file__, '--worker', 'website'], {
                'MAILAPI_MODE': 'test', 'TEST_DATA_DIR': str(data), 'TEST_SNAPSHOT_FROM_PRODUCTION': '0',
                'TEST_SITE_ACCOUNTS_BACKEND': 'sqlite', 'TEST_RESOURCE_BACKEND': 'sqlite',
                'TEST_CREDENTIAL_KEY_FILE': str(keyfile), 'TEST_OAUTH_ENCRYPTION': '1', 'TEST_EXTERNAL_WATCHERS': '0',
                'TEST_HOST': '127.0.0.1', 'TEST_PORT': str(ports['website']), 'TEST_ADMIN_PASSWORD': password})
            ready('website', '/login')
            with httpx.Client(base_url=urls['website'], timeout=30, trust_env=False) as client:
                response = client.post('/api/auth/login', json={'username': 'admin', 'password': password})
                assert response.status_code == 200, 'Synthetic website login'
                response = client.post('/api/api2api/keys', json={'name': 'Disposable benchmark key'})
                assert response.status_code == 201, ('Synthetic website key creation', response.status_code)
                key = response.json()['item']['api_key']
            pids = {'website': website.pid, 'engine': engine.pid, 'upstream': upstream.pid, 'generator': os.getpid()}
            report = {'label': args.label, 'timestamp_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'environment': {'host': platform.node(), 'system': platform.platform(), 'python': platform.python_version(),
                    'logical_cpus': os.cpu_count(), 'engine': 'v1.0.0-rc.51',
                    'database': 'Website SQLite WAL; NewAPI SQLite release defaults; fresh disposable databases',
                    'engine_sql_connections': args.engine_sql_connections or 'release defaults',
                    'upstream': 'loopback synthetic SSE; first content 200ms; no external model calls',
                    'request_prompt_bytes': 8192, 'website': 'full code-receive-test application, default concurrency 128'},
                'stages': []}
            scenarios = [('website', c, profile) for profile in args.profiles for c in args.concurrency]
            if args.controls:
                scenarios = [('upstream', 100, 'short'), ('engine', 100, 'short')] + scenarios
            for target, count, profile in scenarios:
                httpx.post(urls['upstream']+'/metrics/reset', trust_env=False).raise_for_status()
                before_count = store.usage_summary()['requests']
                result = asyncio.run(load(urls[target]+('/api2api/v1' if target == 'website' else '/v1'), key,
                                          count, profile, args.seconds, urls['website'], pids))
                result['target'] = target
                result['website_usage_rows'] = store.usage_summary()['requests']-before_count
                result['upstream'] = httpx.get(urls['upstream']+'/metrics', trust_env=False).json()
                report['stages'].append(result)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
                print(json.dumps({'target': target, 'concurrency': count, 'profile': profile,
                    'success': result['success'], 'errors': result['errors'], 'ttft_p95_ms': result['ttft_ms']['p95'],
                    'web_p95_ms': result['web_probe_ms']['p95'], 'status': result['status_counts']}), flush=True)
                time.sleep(.5)
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--label', default='baseline')
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--concurrency', type=int, nargs='+', default=[30, 50, 100])
    parser.add_argument('--profiles', nargs='+', choices=['short', 'long', 'cap20'], default=['short', 'long'])
    parser.add_argument('--controls', action='store_true')
    parser.add_argument('--engine-sql-connections', type=int, default=0)
    parser.add_argument('--worker', choices=['upstream', 'website'])
    parser.add_argument('--port', type=int)
    arguments = parser.parse_args()
    if arguments.worker:
        worker(arguments.worker, arguments.port)
    else:
        if not arguments.binary or not arguments.output or any(not 1 <= c <= 100 for c in arguments.concurrency) or not 0 <= arguments.engine_sql_connections <= 1000:
            parser.error('Provide --binary, --output and concurrency between 1 and 100')
        main(arguments)
