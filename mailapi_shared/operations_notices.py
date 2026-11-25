"""Durable, deduplicated operational notices with current permission checks."""
from contextlib import closing
from datetime import datetime, timezone
import asyncio
import math
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .accounts import read_payload
from .site_accounts import ADMIN_ROLES


class OperationsNotices:
    def __init__(self, store):
        self.store = store
        with closing(store._connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS operational_conditions(
                    key TEXT PRIMARY KEY, active INTEGER NOT NULL, event_id INTEGER);
                CREATE TABLE IF NOT EXISTS operational_notices(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source_key TEXT NOT NULL,
                    kind TEXT NOT NULL, resource_key TEXT NOT NULL, user_id TEXT NOT NULL,
                    label TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operational_notice_reads(
                    user_id TEXT NOT NULL, notice_id INTEGER NOT NULL REFERENCES operational_notices(id) ON DELETE CASCADE,
                    PRIMARY KEY(user_id,notice_id));
            ''')

    def observe(self, rows, *, reconcile=()):
        rows = list(rows)
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with self.store._lock, closing(self.store._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            if reconcile:
                current = {row['key'] for row in rows}
                obsolete = [(row['key'],) for row in db.execute('SELECT key FROM operational_conditions')
                            if row['key'].startswith(reconcile) and row['key'] not in current]
                db.executemany('UPDATE operational_conditions SET active=0 WHERE key=?', obsolete)
            for row in rows:
                if row['active'] is None:
                    continue
                previous = db.execute('SELECT * FROM operational_conditions WHERE key=?', (row['key'],)).fetchone()
                event_id = previous['event_id'] if previous else None
                if row['active'] and (not previous or not previous['active']):
                    event_id = db.execute('''INSERT INTO operational_notices(source_key,kind,resource_key,user_id,label,created_at)
                        VALUES(?,?,?,?,?,?)''', (row['key'],row['kind'],row.get('resource_key',''),row.get('user_id',''),row['label'],now)).lastrowid
                db.execute('''INSERT INTO operational_conditions VALUES(?,?,?) ON CONFLICT(key)
                    DO UPDATE SET active=excluded.active,event_id=excluded.event_id''', (row['key'],int(row['active']),event_id))

    def visible(self, user, resource_allowed, user_exists=lambda value: True):
        admin = user.get('role') in ADMIN_ROLES
        with closing(self.store._connect()) as db:
            rows = db.execute('''SELECT n.*, r.notice_id IS NULL AS unread FROM operational_notices n
                JOIN operational_conditions c ON c.event_id=n.id AND c.active=1
                LEFT JOIN operational_notice_reads r ON r.notice_id=n.id AND r.user_id=? ORDER BY n.id DESC''', (user['id'],)).fetchall()
        resources, owners = {}, {}
        def permitted(row):
            if row['kind'] == 'backup':
                return admin
            if row['kind'] == 'api_quota':
                owner = row['user_id']
                if owner not in owners:
                    owners[owner] = bool(owner and user_exists(owner))
                return owners[owner] and (admin or owner == user['id'])
            if row['kind'] not in {'expired', 'quota', 'authorization'} or not row['resource_key']:
                return False
            key = row['resource_key']
            if key not in resources:
                resources[key] = resource_allowed(user, key)
            return resources[key]
        return [dict(row) | {'unread': bool(row['unread'])} for row in rows if permitted(row)]

    def read(self, user, ids):
        with closing(self.store._connect()) as db, db:
            db.executemany('INSERT OR IGNORE INTO operational_notice_reads VALUES(?,?)', ((user['id'], value) for value in ids))


def install_operations_notices(app, store, gateway):
    notices = OperationsNotices(store)
    app.state.operations_notices = notices
    lock = asyncio.Lock()
    local_lock = asyncio.Lock()
    last_check = last_local = 0

    async def refresh_local():
        nonlocal last_local
        if local_lock.locked() or time.monotonic()-last_local < 60:
            return
        async with local_lock:
            for name, prefixes in [('resource_health_provider', ('gpt:', 'coding-plan:')), ('backup_notice_provider', ())]:
                provider = getattr(app.state, name, None)
                if provider:
                    try:
                        rows = await run_in_threadpool(provider)
                        await run_in_threadpool(notices.observe, rows, reconcile=prefixes)
                    except Exception:
                        # Failed snapshots cannot prove that a monitored object recovered or disappeared.
                        pass
            last_local = time.monotonic()

    def gateway_snapshot():
        config = store.api2api_config_public()
        if config.get('engine') != 'newapi' or not config.get('enabled'):
            return '', []
        accounts = getattr(app.state, 'site_accounts', None)
        if accounts is None:
            return None
        origin = config['base_url'][:-3]
        with closing(store._connect()) as db:
            ids = [row[0] for row in db.execute('SELECT user_id FROM newapi_users WHERE origin=? AND initialized=1', (origin,))]
        return origin, [owner for uid in ids if (owner := accounts.get_user(uid))]

    async def refresh(stopped):
        nonlocal last_check
        if lock.locked() or time.monotonic()-last_check < 60:
            return
        async with lock:
            snapshot = await run_in_threadpool(gateway_snapshot)
            if snapshot is not None:
                origin, owners = snapshot
                await run_in_threadpool(notices.observe, [{'key':'api:'+origin+':'+owner['id'], 'active':None} for owner in owners], reconcile=('api:',))
                semaphore = asyncio.Semaphore(2)
                async def inspect(owner):
                    async with semaphore:
                        if stopped.is_set():
                            return
                        try:
                            user_id = owner['id']
                            user = await run_in_threadpool(store.gateway_user,origin,user_id,create=False)
                            if not user or not user['initialized']:
                                return
                            account = await gateway.request(user,'GET','/api/user/self')
                            quota = account.get('quota')
                            if type(quota) in (int,float) and math.isfinite(quota):
                                await run_in_threadpool(notices.observe,[{'key':'api:'+origin+':'+user_id,
                                    'kind':'api_quota','user_id':user_id,'label':owner['username'],
                                    'active':quota<=0}])
                        except Exception:
                            # A timeout never means quota exhaustion or recovery.
                            return
                await asyncio.gather(*(inspect(owner) for owner in owners))
            last_check = time.monotonic()

    def identity(request):
        user = getattr(request.state, 'site_user', None)
        if not user:
            raise HTTPException(401,'请先登录')
        return user

    def visible(user):
        allowed = getattr(app.state, 'operation_resource_allowed', lambda user, key: False)
        accounts = getattr(app.state,'site_accounts',None)
        return notices.visible(user,allowed,lambda uid: bool(accounts.get_user(uid)) if accounts else uid==user['id'])

    router = APIRouter()

    @router.get('/api/operations/notifications')
    async def listing(request: Request, page: int=Query(1,ge=1), page_size: int=Query(20,ge=1,le=100)):
        user = identity(request)
        await refresh_local()
        rows = await run_in_threadpool(visible,user)
        result = {'items':rows[(page-1)*page_size:page*page_size], 'total':len(rows),
                  'unread_count':sum(row['unread'] for row in rows), 'max_id':max((row['id'] for row in rows), default=0)}
        if user['role'] in ADMIN_ROLES:
            status_provider = getattr(app.state,'backup_status_provider',None)
            try:
                result['backup_status'] = await run_in_threadpool(status_provider) if status_provider else {'configured':False}
            except Exception:
                result['backup_status'] = {'available':False}
        return JSONResponse(result,headers={'Cache-Control':'no-store, private'})

    @router.post('/api/operations/notifications/read')
    async def mark_read(request: Request):
        user = identity(request)
        payload = await read_payload(request, {'ids','through_id'})
        ids, through = payload.get('ids'),payload.get('through_id')
        if ids is not None:
            if through is not None or not isinstance(ids,list) or not 1<=len(ids)<=100 or any(type(value) is not int or value<1 for value in ids):
                raise HTTPException(400,'通知编号无效')
        elif type(through) is not int or through<0:
            raise HTTPException(400,'通知编号无效')
        allowed = [row['id'] for row in await run_in_threadpool(visible,user)
                   if row['id'] in ids] if ids is not None else [row['id'] for row in await run_in_threadpool(visible,user) if row['id']<=through]
        await run_in_threadpool(notices.read,user,allowed)
        return {'ok':True}

    async def loop(stopped):
        while not stopped.is_set():
            try:
                await refresh_local()
                if not stopped.is_set():
                    await refresh(stopped)
            except Exception:
                pass  # Existing request/resource logging owns failures; retry next minute.
            try:
                await asyncio.wait_for(stopped.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass

    @app.on_event('startup')
    async def start():
        app.state.operations_stop_event = asyncio.Event()
        app.state.operations_task = asyncio.create_task(loop(app.state.operations_stop_event))

    @app.on_event('shutdown')
    async def stop():
        task = getattr(app.state,'operations_task',None)
        if task:
            app.state.operations_stop_event.set()
            # Cancellation can abandon a running database worker. Drain in-flight work first.
            await task

    app.include_router(router)
