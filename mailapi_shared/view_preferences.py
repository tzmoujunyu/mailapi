"""Small, account-owned UI preferences; never store search text or credentials."""
from contextlib import closing
from datetime import date, datetime
import json
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .accounts import read_payload
from .site_accounts import SiteAccountError

RESOURCE_STATUS = {'all', 'available', 'expiring', 'expired', 'low-quota', 'exhausted', 'reauth'}
FINANCE = {'status': {'all', 'pending', 'approved', 'rejected', 'cancelled'},
           'period': {'all', 'day', 'today', 'week', 'month', 'custom'},
           'from': 'date', 'to': 'date', 'min': 'amount', 'max': 'amount',
           'group': 'identifier', 'user': 'identifier', 'reviewer': 'identifier'}
SCOPES = {**{'finance-'+name: FINANCE for name in ('review', 'stats', 'history', 'mine')},
    'resources': {'gptStatus': RESOURCE_STATUS, 'gptSort': {'original', 'quota', 'expiry'},
                  'codingStatus': {'all', 'low-quota', 'exhausted'}, 'codingSort': {'original', 'quota'}},
    'api-logs': {'size': {20, 50, 100}, 'type': {'0', '1', '2', '3', '4', '5', '6', '7'},
                 'columns': 'columns', 'group': 'identifier', 'key_id': 'identifier',
                 'range': {'today', 'week', 'month', '7', '30', 'all', 'custom'},
                 'start': 'datetime', 'end': 'datetime', 'sensitive': 'bool'},
    'api-audit': {'size': {20, 50, 100}},
}
COLUMNS = {'time', 'key', 'model', 'stream', 'tokens', 'cost', 'timing', 'details'}


def validate(scope, settings):
    if scope not in SCOPES or not isinstance(settings, dict) or set(settings)-SCOPES[scope].keys():
        raise HTTPException(400, '页面偏好无效')
    for key, value in settings.items():
        rule = SCOPES[scope][key]
        valid = False
        if isinstance(rule, set):
            valid = type(value) in (str, int) and value in rule
        elif rule == 'bool':
            valid = type(value) is bool
        elif rule == 'columns':
            valid = isinstance(value, list) and 1 <= len(value) <= len(COLUMNS) and all(isinstance(v, str) and v in COLUMNS for v in value) and len(set(value)) == len(value) and 'time' in value
        elif isinstance(value, str) and len(value) <= 128:
            if rule == 'identifier':
                valid = not any(ord(c) < 32 for c in value)
            elif rule == 'amount':
                valid = not value or bool(re.fullmatch(r'\d{1,10}(?:\.\d{1,2})?', value))
            elif rule in {'date', 'datetime'}:
                try:
                    if value:
                        pattern = r'\d{4}-\d{2}-\d{2}' if rule == 'date' else r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?'
                        if not re.fullmatch(pattern, value):
                            raise ValueError
                        (date if rule == 'date' else datetime).fromisoformat(value)
                    valid = True
                except ValueError:
                    pass
        if not valid:
            raise HTTPException(400, '页面偏好无效')
    return settings


def install_view_preferences(app, store):
    with closing(store._connect()) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS view_preferences(
            user_id TEXT NOT NULL, scope TEXT NOT NULL, settings TEXT NOT NULL,
            PRIMARY KEY(user_id,scope))''')
    router = APIRouter()

    def identity(request, scope):
        user = getattr(request.state, 'site_user', None)
        if not user:
            raise HTTPException(401, '请先登录')
        if scope not in SCOPES:
            raise HTTPException(404, '未找到页面偏好')
        return user['id']

    @router.get('/api/view-preferences/{scope}')
    def get(scope: str, request: Request):
        user_id = identity(request, scope)
        with closing(store._connect()) as db:
            row = db.execute('SELECT settings FROM view_preferences WHERE user_id=? AND scope=?', (user_id, scope)).fetchone()
        return JSONResponse({'settings': json.loads(row['settings']) if row else {}}, headers={'Cache-Control': 'no-store, private'})

    @router.put('/api/view-preferences/{scope}')
    async def save(scope: str, request: Request):
        user_id = identity(request, scope)
        try:
            payload = await read_payload(request, {'settings'})
        except SiteAccountError as error:
            raise HTTPException(error.status_code, str(error)) from error
        settings = validate(scope, payload.get('settings'))
        with store._lock, closing(store._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT settings FROM view_preferences WHERE user_id=? AND scope=?', (user_id, scope)).fetchone()
            merged = (json.loads(row['settings']) if row else {}) | settings
            db.execute('INSERT INTO view_preferences VALUES(?,?,?) ON CONFLICT(user_id,scope) DO UPDATE SET settings=excluded.settings',
                       (user_id, scope, json.dumps(merged, ensure_ascii=False)))
        return {'settings': merged}

    app.include_router(router)
