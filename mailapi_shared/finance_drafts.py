"""Private, versioned working copies; drafts never reserve invoices or notify reviewers."""
from contextlib import closing
import json
import logging
import re

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from .site_accounts import ADMIN_ROLES


SCHEMA = """
CREATE TABLE IF NOT EXISTS finance_drafts (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, revision INTEGER NOT NULL,
    fields_json TEXT NOT NULL, files_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_finance_drafts_user ON finance_drafts(user_id, updated_at);
"""


def owned_draft(db, draft_id, user_id, revision=None):
    row = db.execute('SELECT * FROM finance_drafts WHERE id=? AND user_id=?', (draft_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, '未找到草稿，可能已提交或删除')
    if revision is not None and row['revision'] != revision:
        raise HTTPException(409, '草稿已在其他页面更新，请关闭后重新打开草稿')
    return {**dict(row), 'fields': json.loads(row['fields_json']), 'files': json.loads(row['files_json'])}


def consume_draft(db, draft, application_id):
    if not draft:
        return []
    item = owned_draft(db, *draft)
    if item['fields']['application_id'] != application_id:
        raise HTTPException(409, '草稿与当前申请不匹配')
    db.execute('DELETE FROM finance_drafts WHERE id=?', (item['id'],))
    return item['files']


def public_draft(item):
    return {key: item[key] for key in ('id', 'revision', 'fields', 'updated_at')} | {
        'files': [{k: v for k, v in file.items() if k not in {'stored_name', 'sha256'}} for file in item['files']]}


def remove_draft_files(store, files):
    for file in files:
        try:
            (store.files / file['stored_name']).unlink(missing_ok=True)
        except OSError:
            # A cleanup failure must not invalidate an already committed application/save.
            logging.getLogger(__name__).exception('Could not remove obsolete draft attachment')


def install_finance_drafts(router, store, current_user, save_uploads, now):
    with closing(store._connect()) as db, db:
        db.executescript(SCHEMA)

    def fields_for_save(request, raw):
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or len(raw) > 12000:
                raise ValueError()
            fields = {}
            for key, limit in {'project': 1000, 'application_note': 500, 'expense_group_id': 128,
                               'payer_user_id': 128, 'application_id': 128, 'version': 20}.items():
                text = value.get(key, '')
                if not isinstance(text, str) or len(text) > limit:
                    raise ValueError()
                fields[key] = text
            invoice = value.get('invoice_fields', {})
            if not isinstance(invoice, dict):
                raise ValueError()
            fields['invoice_fields'] = {}
            for key in ('number', 'code', 'date', 'total', 'check_code'):
                text = invoice.get(key, '')
                if not isinstance(text, str) or len(text) > 80:
                    raise ValueError()
                fields['invoice_fields'][key] = text
            fields['on_behalf'] = value.get('on_behalf') is True
        except (ValueError, TypeError):
            raise HTTPException(400, '草稿内容格式无效或过长') from None
        user = current_user(request)
        if fields['on_behalf'] or fields['payer_user_id']:
            if user.get('role') not in ADMIN_ROLES or fields['application_id']:
                raise HTTPException(403, '需要管理员权限')
            if fields['payer_user_id'] and not request.app.state.site_accounts.get_user(fields['payer_user_id']):
                raise HTTPException(400, '付款人已不存在，请重新选择')
        if fields['application_id']:
            app = store.finance_application(fields['application_id'])
            if not app or app['user_id'] != user['id']:
                raise HTTPException(404, '未找到报销申请')
            if app['status'] not in {'pending', 'rejected'} or str(app['version']) != fields['version']:
                raise HTTPException(409, '申请已变化，请重新查看申请后编辑；草稿仍保留')
        return fields

    @router.get('/api/finance/drafts')
    def list_drafts(request: Request):
        user = current_user(request)
        with closing(store._connect()) as db:
            rows = db.execute('SELECT id FROM finance_drafts WHERE user_id=? ORDER BY updated_at DESC,id', (user['id'],)).fetchall()
            return {'items': [public_draft(owned_draft(db, row['id'], user['id'])) for row in rows]}

    @router.get('/api/finance/drafts/{draft_id}')
    def get_draft(draft_id: str, request: Request):
        with closing(store._connect()) as db:
            return {'item': public_draft(owned_draft(db, draft_id, current_user(request)['id']))}

    @router.put('/api/finance/drafts/{draft_id}')
    async def save_draft(draft_id: str, request: Request, fields: str = Form(...), revision: int = Form(...),
                         invoice: UploadFile | None = File(None), payment_record: UploadFile | None = File(None)):
        user = current_user(request)
        if not re.fullmatch(r'[a-zA-Z0-9-]{16,64}', draft_id) or revision < 0:
            raise HTTPException(400, '草稿编号或版本无效')
        payload = fields_for_save(request, fields)
        # Check ownership/version before reading attachments, and again inside the write transaction.
        with closing(store._connect()) as db:
            if revision:
                owned_draft(db, draft_id, user['id'], revision)
            elif db.execute('SELECT 1 FROM finance_drafts WHERE id=?', (draft_id,)).fetchone():
                raise HTTPException(409, '草稿已存在，请重新打开草稿')
        saved = await save_uploads([(kind, file) for kind, file in [('invoice', invoice), ('payment', payment_record)] if file])
        obsolete = []
        try:
            with store._lock, closing(store._connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                previous = owned_draft(db, draft_id, user['id'], revision) if revision else None
                if not previous and db.execute('SELECT 1 FROM finance_drafts WHERE id=?', (draft_id,)).fetchone():
                    raise HTTPException(409, '草稿已存在，请重新打开草稿')
                files = {file['kind']: file for file in previous['files']} if previous else {}
                for file in saved:
                    if file['kind'] in files:
                        obsolete.append(files[file['kind']])
                    files[file['kind']] = file
                db.execute('INSERT INTO finance_drafts VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, fields_json=excluded.fields_json, files_json=excluded.files_json, updated_at=excluded.updated_at',
                           (draft_id, user['id'], revision + 1, json.dumps(payload, ensure_ascii=False), json.dumps(list(files.values())), now()))
                result = public_draft(owned_draft(db, draft_id, user['id']))
        except Exception:
            remove_draft_files(store, saved)
            raise
        remove_draft_files(store, obsolete)
        return {'item': result}

    @router.get('/api/finance/drafts/{draft_id}/files/{kind}')
    def download_draft_file(draft_id: str, kind: str, request: Request, revision: int):
        with closing(store._connect()) as db:
            item = owned_draft(db, draft_id, current_user(request)['id'], revision)
        file = next((file for file in item['files'] if file['kind'] == kind), None)
        if not file or not (store.files / file['stored_name']).is_file():
            raise HTTPException(404, '未找到草稿附件')
        return FileResponse(store.files / file['stored_name'], media_type=file['content_type'],
                            filename=file['original_name'], headers={'Cache-Control': 'private, no-store'})

    @router.delete('/api/finance/drafts/{draft_id}')
    def delete_draft(draft_id: str, request: Request, revision: int):
        with store._lock, closing(store._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            item = owned_draft(db, draft_id, current_user(request)['id'], revision)
            db.execute('DELETE FROM finance_drafts WHERE id=?', (draft_id,))
        remove_draft_files(store, item['files'])
        return {'ok': True}
