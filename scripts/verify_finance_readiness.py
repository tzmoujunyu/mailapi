"""Validate private test snapshots, uploaded invoices and synthetic finance loads.

Real data stays in the explicitly selected private directory. JSON summaries
contain counts and timings, never invoice fields, credentials or user identities.
The HTTP test actor header exists only in this isolated validation application.
"""
import argparse
from contextlib import ExitStack, closing
import csv
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATABASES = ('data/site_accounts.sqlite3', 'data/resources.sqlite3', 'data/test_features.sqlite3', 'newapi.db')


def progress(message):
    print(message, flush=True)


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    path.chmod(0o600)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_db(path):
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)


def database_manifest(path):
    with closing(read_db(path)) as db:
        assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        checksum = hashlib.sha256()
        for line in db.iterdump():
            checksum.update(line.encode())
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {name: db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in tables}
        return {'logical_sha256': checksum.hexdigest(), 'tables': counts}


def snapshot(source, destination):
    source, destination = source.resolve(), destination.absolute()
    if (not source.name.startswith('mailapi-newapi-preview-') or not source.is_dir()
            or destination.exists() or destination.is_relative_to(ROOT)
            or source.is_relative_to(destination) or destination.is_relative_to(source)):
        raise ValueError('Use an existing disposable preview and a new private directory outside the project')
    if any(path.is_symlink() for path in source.rglob('*')):
        raise ValueError('Snapshot source contains a symlink')
    destination.mkdir(mode=0o700)
    backup = destination / 'backup'
    for attempt in range(3):
        if backup.exists():
            shutil.rmtree(backup)
        backup.mkdir(mode=0o700)
        with ExitStack() as stack:
            readers = [stack.enter_context(closing(read_db(source / name))) for name in DATABASES]
            before = [db.execute('PRAGMA data_version').fetchone()[0] for db in readers]
            for name, db in zip(DATABASES, readers):
                target = backup / name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with closing(sqlite3.connect(target)) as writer:
                    db.backup(writer, pages=256, sleep=.05)
                target.chmod(0o600)
            files = list((source / 'data').rglob('*')) + [source / 'credential-key.json']
            copied = {}
            for path in files:
                relative = path.relative_to(source)
                if (not path.is_file() or relative.as_posix() in DATABASES
                        or path.name.endswith(('-wal', '-shm', '-journal'))
                        or any(part in {'logs', 'finance_previews', 'finance_exports'} for part in relative.parts)):
                    continue
                target = backup / relative
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                target.chmod(0o600)
                copied[relative.as_posix()] = digest(target)
                assert copied[relative.as_posix()] == digest(path)
            after = [db.execute('PRAGMA data_version').fetchone()[0] for db in readers]
        if before == after:
            break
        progress('Source changed while snapshotting; retrying a quiet snapshot')
    else:
        raise RuntimeError('Could not obtain a quiet multi-database snapshot; no complete backup was declared')
    manifests = {name: database_manifest(backup / name) for name in DATABASES}
    assert all(database_manifest(source / name) == value for name, value in manifests.items())
    save_json(destination / 'private-manifest.json', {'databases': manifests, 'files': copied})
    restored = destination / 'restored'
    shutil.copytree(backup, restored)
    assert all(database_manifest(restored / name) == value for name, value in manifests.items())
    assert all(digest(restored / name) == value for name, value in copied.items())
    assert (restored / 'credential-key.json').stat().st_mode & 0o777 == 0o600
    # Deliberately damaged copies must not pass the same completeness check.
    sample = next(name for name in copied if name.startswith('data/finance_files/'))
    broken = destination / 'damaged-attachment'
    broken.write_bytes((restored / sample).read_bytes() + b'corruption')
    assert digest(broken) != copied[sample]
    broken.unlink()
    summary = {'database_integrity_and_restore_match': True, 'database_count': len(manifests),
               'copied_file_count': len(copied), 'attachment_corruption_detected': True,
               'table_counts': {name: value['tables'] for name, value in manifests.items()}}
    save_json(destination / 'snapshot-summary.json', summary)
    progress('Consistent backup and separate restore verified (4 databases, private files and matching key)')


class SyntheticAccounts:
    users = {'admin-id': {'id': 'admin-id', 'username': 'admin', 'role': 'admin', 'note': ''},
             'alice-id': {'id': 'alice-id', 'username': 'alice', 'role': 'user', 'note': ''}}

    def get_user(self, user_id, **unused):
        return dict(self.users[user_id]) if user_id in self.users else None

    def user_groups(self, user_id):
        return []


def validation_app(data, *, ring=None, site=None):
    from fastapi import FastAPI, Request
    from mailapi_shared.test_features import install_test_features
    from mailapi_shared.web_security import BodyLimitMiddleware, SecurityHeadersMiddleware
    app = FastAPI()
    app.state.site_accounts = site or SyntheticAccounts()

    @app.middleware('http')
    async def actor(request: Request, call_next):
        request.state.site_user = app.state.site_accounts.get_user(request.headers.get('x-validation-user', 'admin-id'), include_note=True)
        return await call_next(request)

    @app.get('/validation-health')
    async def health():
        return {'ok': True}

    @app.get('/validation-resources')
    async def metrics():
        usage = resource.getrusage(resource.RUSAGE_SELF)
        scale = 1024**2 if platform.system() == 'Darwin' else 1024
        return {'peak_rss_mib': round(usage.ru_maxrss / scale, 2), 'cpu_seconds': usage.ru_utime + usage.ru_stime}

    store = install_test_features(app, ROOT / 'mailapi_shared/static', data, ring)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    return app, store


def functional(directory):
    from fastapi.testclient import TestClient
    from openpyxl import load_workbook
    from unittest.mock import patch
    from mailapi_shared.credential_crypto import CredentialKeyring
    from mailapi_shared.finance_invoices import recognize_file
    from mailapi_shared.site_database import open_site_accounts
    workspace = Path(tempfile.mkdtemp(prefix='workflow-', dir=directory))
    shutil.copytree(directory / 'restored', workspace, dirs_exist_ok=True)
    ring = CredentialKeyring(workspace / 'credential-key.json', data_dirs=(workspace / 'data',))
    site = open_site_accounts(workspace / 'data', backend='sqlite', keyring=ring)
    app, store = validation_app(workspace / 'data', ring=ring, site=site)
    admin = next(user['id'] for user in site._data['users'].values() if user['role'] == 'admin')
    headers = {'x-validation-user': admin}
    items = store.list_finance_applications()
    history = store.finance_history()
    files = {file['id']: file for item in items + [event['snapshot'] for event in history] for file in item['files']}
    projects = {file['id']: item['project'] for item in items + [event['snapshot'] for event in history] for file in item['files']}
    missing = set()
    for file in files.values():
        path = store.files / file['stored_name']
        if not path.is_file():
            assert projects[file['id']].startswith('[演示]'), 'A real attachment is missing'
            missing.add(file['id'])
        else:
            assert path.stat().st_size == file['size'] and digest(path) == file['sha256']
    config = store.api2api_config()
    assert config.get('engine') == 'newapi' and store.api2api_config_public().get('configured')
    result = {'applications': len(items), 'events': len(history), 'unique_current_and_historical_files': len(files),
              'present_attachment_hashes_match': True, 'preexisting_missing_demo_attachments': len(missing),
              'gateway_configuration_decrypts': True}
    legacy_files, legacy_rows, invalid_pdfs = {}, 0, 0
    real_invoice_digests = {file['sha256'] for item in items if item['invoice']
                           for file in item['files'] if file['kind'] == 'invoice'}
    import pypdfium2 as pdfium
    for item in items:
        if item['project'].startswith('[演示]'):
            continue
        for file in item['files']:
            path = store.files / file['stored_name']
            if not path.is_file() or file['content_type'] != 'application/pdf':
                continue
            try:
                with pdfium.PdfDocument(path) as document:
                    assert len(document) > 0
            except (pdfium.PdfiumError, OSError, ValueError):
                invalid_pdfs += 1
                continue
            if file['kind'] == 'invoice' and not item['invoice']:
                legacy_rows += 1
                legacy_files.setdefault(file['sha256'], path)
                real_invoice_digests.add(file['sha256'])
    legacy_results = []
    for path in legacy_files.values():
        began = time.perf_counter()
        observed = recognize_file(path)
        legacy_results.append({'method': observed['method'], 'seconds': round(time.perf_counter() - began, 3),
                               'recognized_fields': sorted(observed['fields']), 'multiple': observed['multiple']})
    with closing(store._connect()) as db:
        duplicate_groups = [{'active_applications': row[0], 'distinct_users': row[1],
                             'contains_parseable_real_invoice': row[2] in real_invoice_digests} for row in db.execute('''
            SELECT count(*),count(DISTINCT a.user_id),f.sha256 FROM finance_files f
            JOIN finance_applications a ON a.id=f.application_id
            WHERE f.kind='invoice' AND a.status IN ('pending','approved') GROUP BY f.sha256 HAVING count(*)>1''')]
    result['legacy_invoice_checks'] = {'real_invoices_without_confirmed_fields': legacy_rows,
        'unique_files_recognized': legacy_results, 'preexisting_active_duplicate_file_groups': duplicate_groups,
        'invalid_non_demo_pdf_attachments': invalid_pdfs}
    with TestClient(app) as client:
        response = client.get('/api/admin/finance/applications', headers=headers)
        assert response.status_code == 200 and len(response.json()['items']) == len(items)
        checked_downloads = set()
        for event in history:
            for file in event['snapshot']['files']:
                if file['id'] in checked_downloads:
                    continue
                response = client.get(f"/api/finance/applications/{event['application_id']}/download",
                    params={'kind': file['kind'], 'event_id': event['id']}, headers=headers)
                if file['id'] in missing:
                    assert response.status_code == 410
                else:
                    assert response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == file['sha256']
                checked_downloads.add(file['id'])
        result['historical_downloads_verified'] = len(checked_downloads - missing)
        result['missing_attachment_downloads_return_410'] = len(checked_downloads & missing)
        expected = {item['id']: item['amount_cents'] for item in items}
        for kind in ('csv', 'xlsx'):
            response = client.post('/api/finance/reports/export', headers=headers,
                json={'report': 'applications', 'format': kind, 'scope': 'all', 'filters': {}})
            assert response.status_code == 200 and int(response.headers['x-export-count']) == len(items)
            if kind == 'csv':
                rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
            else:
                with closing(load_workbook(io.BytesIO(response.content), read_only=True)) as workbook:
                    rows = list(workbook.active.values)
            assert len(rows) == len(items) + 1
            assert {row[0]: int(Decimal(str(row[8])) * 100) for row in rows[1:]} == expected
        result['csv_and_xlsx_amounts_match'] = True
        response = client.post('/api/finance/reports/export', headers=headers,
            json={'report': 'groups', 'format': 'csv', 'scope': 'all', 'filters': {}})
        assert response.status_code == 200
        groups = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))[1:]
        active = [item for item in items if item['status'] != 'cancelled']
        assert sum(int(row[2]) for row in groups) == len(active)
        assert sum(int(Decimal(row[3]) * 100) for row in groups) == sum(item['amount_cents'] for item in active)
        result['group_totals_match_without_cancelled_or_double_counting'] = True
        reviews = client.get('/api/admin/finance/history', headers=headers).json()['items']
        response = client.post('/api/finance/reports/export', headers=headers,
            json={'report': 'history', 'format': 'csv', 'scope': 'all', 'filters': {}})
        exported = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))[1:]
        assert {row[0] for row in exported} == {event['id'] for event in reviews}
        assert sum(row[7] == '是' for row in exported) == sum(event['is_current_review'] for event in reviews)
        result['review_export_includes_history_and_marks_current_results'] = True
        approved = [event for event in reviews if event['action'] == 'approved' and event['is_current_review']
                    and all(file['id'] not in missing for file in event['snapshot']['files'])]
        if approved:
            response = client.post('/api/admin/finance/download.zip', headers=headers, json={'event_ids': [event['id'] for event in approved]})
            assert response.status_code == 200
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                assert archive.testzip() is None
                assert len(archive.namelist()) == 1 + sum(len(event['snapshot']['files']) for event in approved)
        result['current_approved_archive_verified'] = bool(approved)
        incomplete = next((event for event in reviews if event['action'] == 'approved' and event['is_current_review']
                           and any(file['id'] in missing for file in event['snapshot']['files'])), None)
        if incomplete:
            response = client.post('/api/admin/finance/download.zip', headers=headers, json={'event_ids': [incomplete['id']]})
            assert response.status_code == 410
        result['incomplete_archive_rejected'] = bool(incomplete)
        candidates = [item for item in items if item['invoice']]
        recognition = []
        with patch('mailapi_shared.finance_invoices.recognize_file', wraps=recognize_file) as recognizer:
            for index, item in enumerate(candidates, 1):
                file = next(file for file in item['files'] if file['kind'] == 'invoice')
                owner = {'x-validation-user': item['user_id']}
                content = (store.files / file['stored_name']).read_bytes()
                started = time.perf_counter()
                response = client.post('/api/finance/invoices/analyze', headers=owner,
                    files={'invoice': ('invoice' + Path(file['stored_name']).suffix, content, file['content_type'])})
                assert response.status_code == 200
                observed = response.json()['recognition']
                fields = item['invoice']['fields']
                recognition.append({'alias': f'invoice-{index}', 'method': observed['method'],
                    'seconds': round(time.perf_counter() - started, 3),
                    'recognized_field_count': len(observed['fields']),
                    'confirmed_field_matches': {key: observed['fields'].get(key) == value for key, value in fields.items() if value}})
                before = recognizer.call_count
                if item['status'] in {'pending', 'approved'}:
                    duplicate = client.post('/api/finance/applications', headers=owner,
                        data={'project': 'Validation duplicate', 'amount': item['amount'],
                              'expense_group_id': item['expense_group_id'], 'invoice_fields': json.dumps(fields)},
                        files={'invoice': ('invoice' + Path(file['stored_name']).suffix, content, file['content_type'])})
                    assert duplicate.status_code == 409 and recognizer.call_count == before
                progress(f'Real invoice {index}: recognition, reuse and live duplicate check verified on the copy')
                for kind in ('invoice', 'payment'):
                    if not any(file['kind'] == kind for file in item['files']):
                        continue
                    preview = client.get(f"/api/finance/applications/{item['id']}/attachment-preview", params={'kind': kind}, headers=owner)
                    assert preview.status_code == 200
                    for page in preview.json()['pages']:
                        assert client.get(page['thumbnail'], headers=owner).status_code == 200
                        assert client.get(page['src'], headers=owner).status_code == 200
        result['real_invoice_recognition'] = recognition
        previews = {'available': 0, 'demo_fallback': 0, 'other_fallback': 0}
        for item in items:
            for file in item['files']:
                if file['id'] in missing:
                    continue
                response = client.get(f"/api/finance/applications/{item['id']}/attachment-preview",
                    params={'kind': file['kind']}, headers=headers)
                if response.status_code == 422:
                    previews['demo_fallback' if item['project'].startswith('[演示]') else 'other_fallback'] += 1
                else:
                    assert response.status_code == 200 and response.json()['pages']
                    previews['available'] += 1
        result['current_attachment_previews'] = previews
        if candidates:
            item = next(item for item in candidates if item['status'] in {'pending', 'approved'})
            app_id, old_version = item['id'], item['version']
            owner = {'x-validation-user': item['user_id']}
            route = f'/api/admin/finance/applications/{app_id}'
            if item['status'] == 'pending':
                response = client.post(route + '/review', headers=headers,
                    json={'status': 'rejected', 'note': 'Isolated validation', 'version': old_version})
            else:
                latest = next(event for event in store.finance_history(app_id) if event['is_current_review'])
                response = client.post(route + '/re-review', headers=headers,
                    json={'status': 'rejected', 'note': 'Isolated validation', 'version': old_version, 'event_id': latest['id']})
            assert response.status_code == 200
            response = client.put(f'/api/finance/applications/{app_id}', headers=owner,
                data={'project': item['project'], 'amount': item['amount'], 'version': old_version,
                      'invoice_fields': json.dumps(item['invoice']['fields'])})
            assert response.status_code == 200
            updated = response.json()['item']
            assert updated['status'] == 'pending' and updated['version'] == old_version + 1
            response = client.post(route + '/review', headers=headers,
                json={'status': 'approved', 'note': '', 'version': updated['version']})
            assert response.status_code == 200
            prior = next(event for event in store.finance_history(app_id) if event['is_current_review'])
            response = client.post(route + '/re-review', headers=headers,
                json={'status': 'rejected', 'note': 'Isolated correction', 'version': updated['version'], 'event_id': prior['id']})
            assert response.status_code == 200
            stale = client.post(route + '/re-review', headers=headers,
                json={'status': 'rejected', 'note': 'Stale validation request', 'version': updated['version'], 'event_id': prior['id']})
            assert stale.status_code == 409
            events = client.get(f'/api/finance/applications/{app_id}/history', headers=owner).json()['items']
            assert any(event['version'] == old_version for event in events)
            assert any(event['version'] == updated['version'] for event in events)
            other = next(user['id'] for user in site._data['users'].values()
                         if user['role'] == 'user' and user['id'] != item['user_id'])
            assert client.get(f'/api/finance/applications/{app_id}/history',
                              headers={'x-validation-user': other}).status_code == 404
            assert client.post('/api/finance/reports/export', headers={'x-validation-user': other},
                json={'report': 'applications', 'format': 'csv', 'scope': 'all', 'filters': {}}).status_code == 403
            result['real_invoice_full_workflow'] = {'reject_resubmit_approve_re_review': True,
                'old_and_new_versions_preserved': True, 'stale_review_rejected': True, 'other_user_denied': True}
        assert not list((workspace / 'data/finance_exports').iterdir())
    save_json(directory / 'functional-summary.json', result)
    shutil.rmtree(workspace)
    progress('Restored records, original/historical downloads, amounts, exports and real invoice checks verified')


def seed_load(directory, count, *, attachments=False):
    from mailapi_shared.test_features import TestFeatureStore
    store = TestFeatureStore(directory, None)
    (directory / '.synthetic-finance-load').write_text('synthetic accounts and opaque test attachments only')
    now = '2026-11-23T00:00:00Z'
    payloads = {}
    if attachments:
        for kind in ('invoice', 'payment'):
            path = store.files / (kind + '.pdf')
            path.write_bytes(os.urandom(128 * 1024))
            payloads[kind] = {'stored_name': path.name, 'original_name': 'synthetic.pdf',
                              'content_type': 'application/pdf', 'size': path.stat().st_size, 'sha256': digest(path)}
    with closing(store._connect()) as db, db:
        for index in range(count):
            app_id = f'load-{index:06d}'
            state = 'approved' if attachments else 'pending'
            files = [{'id': app_id + '-' + kind, 'kind': kind, **file} for kind, file in payloads.items()]
            item = {'id': app_id, 'user_id': 'alice-id', 'username': 'alice', 'user_note': '', 'groups': [],
                    'project': app_id, 'amount_cents': 1250, 'amount': '12.50', 'status': state, 'version': 1,
                    'expense_group_id': '', 'expense_group_name': '未分组', 'files': files, 'invoice': None,
                    'created_at': now, 'updated_at': now, 'review_note': '', 'reviewer_name': 'admin'}
            db.execute('''INSERT INTO finance_applications
                (id,user_id,username,user_note,groups_json,project,amount_cents,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)''', (app_id, 'alice-id', 'alice', '', '[]', app_id, 1250, state, now, now))
            db.execute('INSERT INTO finance_versions VALUES(?,?,?)', (app_id, 1, json.dumps(item)))
            if attachments:
                db.execute('''INSERT INTO finance_events(id,application_id,version,action,actor_id,actor_name,note,created_at)
                              VALUES(?,?,1,'approved','admin-id','admin','',?)''', (app_id + '-review', app_id, now))
                for file in files:
                    store._insert_finance_file(db, app_id, file['kind'], file)


def worker(directory, port):
    import uvicorn
    if not (directory / '.synthetic-finance-load').is_file():
        raise ValueError('The validation HTTP server only accepts freshly seeded synthetic data')
    app, unused = validation_app(directory)
    uvicorn.run(app, host='127.0.0.1', port=port, access_log=False, log_level='error',
                proxy_headers=False, limit_concurrency=128)


def benchmark(directory):
    import httpx
    output = []
    save_json(directory.parent / 'benchmark-environment.json', {'system': platform.system(),
        'architecture': platform.machine(), 'logical_cpus': os.cpu_count(),
        'python': platform.python_version(), 'attachment_bytes_per_file': 128 * 1024,
        'attachments_per_application': 2, 'sequential_cases': True, 'real_data_used_for_load': False})
    for mode, sizes in (('list', (1000, 10000, 50000)), ('zip', (10, 100, 500))):
        for count in sizes:
            case = directory / f'{mode}-{count}'
            case.mkdir(mode=0o700, parents=True)
            seed_load(case / 'data', count, attachments=mode == 'zip')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            with (case / 'server.log').open('wb') as log:
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'worker',
                    '--directory', str(case / 'data'), '--port', str(port)], cwd=ROOT, stdout=log, stderr=log)
                base = f'http://127.0.0.1:{port}'
                stopped = threading.Event()
                probes = []

                def probe():
                    with httpx.Client(trust_env=False, timeout=3) as client:
                        while not stopped.is_set():
                            began = time.perf_counter()
                            try:
                                response = client.get(base + '/validation-health')
                                probes.append({'ms': (time.perf_counter() - began) * 1000, 'status': response.status_code})
                            except httpx.HTTPError:
                                probes.append({'ms': (time.perf_counter() - began) * 1000, 'status': 'timeout'})
                            stopped.wait(.1)

                thread = None
                try:
                    with httpx.Client(trust_env=False, timeout=120) as client:
                        for unused in range(200):
                            if process.poll() is not None:
                                raise RuntimeError('Isolated benchmark server stopped')
                            try:
                                if client.get(base + '/validation-health', timeout=1).status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            time.sleep(.05)
                        else:
                            raise RuntimeError('Isolated benchmark server readiness timeout')
                        before = client.get(base + '/validation-resources').json()
                        thread = threading.Thread(target=probe, daemon=True)
                        thread.start()
                        began = time.perf_counter()
                        if mode == 'list':
                            response = client.get(base + '/api/admin/finance/applications')
                            elapsed = time.perf_counter() - began
                            assert response.status_code == 200 and len(response.json()['items']) == count
                            size = len(response.content)
                            chunks = None
                        else:
                            size, chunks = 0, 0
                            with client.stream('POST', base + '/api/admin/finance/download.zip',
                                    json={'event_ids': [f'load-{index:06d}-review' for index in range(count)]}) as response:
                                assert response.status_code == 200
                                with (case / 'download.zip').open('wb') as archive:
                                    for chunk in response.iter_raw():
                                        archive.write(chunk)
                                        size += len(chunk)
                                        chunks += 1
                            elapsed = time.perf_counter() - began
                            with zipfile.ZipFile(case / 'download.zip') as archive:
                                assert archive.testzip() is None and len(archive.namelist()) == 2 * count + 1
                        after = client.get(base + '/validation-resources').json()
                        stopped.set()
                        thread.join(timeout=5)
                        latencies = sorted(item['ms'] for item in probes)
                        result = {'mode': mode, 'rows': count, 'seconds': round(elapsed, 3),
                            'response_mib': round(size / 1024**2, 3), 'baseline_peak_rss_mib': before['peak_rss_mib'],
                            'peak_rss_mib': after['peak_rss_mib'], 'cpu_seconds': round(after['cpu_seconds'] - before['cpu_seconds'], 3),
                            'health_samples': len(latencies),
                            'health_max_ms': round(max(latencies), 2) if latencies else None,
                            'health_p95_ms': round(latencies[int((len(latencies)-1)*.95)], 2) if len(latencies) >= 20 else None,
                            'health_errors': sum(item['status'] != 200 for item in probes), 'http_chunks': chunks}
                        output.append(result)
                        save_json(directory / 'performance-summary.json', output)
                        progress(json.dumps(result))
                finally:
                    stopped.set()
                    if thread:
                        thread.join(timeout=5)
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            shutil.rmtree(case)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('snapshot', 'functional', 'benchmark', 'worker'))
    parser.add_argument('--source', type=Path)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int)
    args = parser.parse_args()
    os.umask(0o077)
    if args.mode == 'snapshot':
        if not args.source:
            parser.error('snapshot requires --source')
        snapshot(args.source, args.directory)
    elif args.mode == 'functional':
        functional(args.directory.resolve())
    elif args.mode == 'benchmark':
        benchmark(args.directory.resolve())
    else:
        if not args.port:
            parser.error('worker requires --port')
        worker(args.directory.resolve(), args.port)


if __name__ == '__main__':
    main()
