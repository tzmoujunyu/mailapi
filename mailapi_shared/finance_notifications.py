"""Role-scoped finance notifications and overdue reminders in the test database."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .site_accounts import ADMIN_ROLES
from .accounts import read_payload


def record_event(db, item, event_id, action, actor_name, note, when, *, re_review=False):
    if action not in {'submitted','resubmitted','approved','rejected'}:
        return
    audience = 'admins' if action in {'submitted','resubmitted'} else 'user'
    payload = {'project':item['project'],'amount':item['amount'],'result':action,'actor_name':actor_name,'note':note,'re_review':re_review}
    db.execute('''INSERT INTO finance_notifications(application_id,event_id,kind,audience,user_id,payload_json,version,created_at,dedupe_key)
        VALUES(?,?,?,?,?,?,?,?,?)''',(item['id'],event_id,'submitted' if audience=='admins' else 'reviewed',audience,
        '' if audience=='admins' else item['user_id'],json.dumps(payload,ensure_ascii=False),item['version'],when,'event:'+event_id))


SCOPE = "((n.audience='admins' AND ?=1) OR (n.audience='user' AND n.user_id=?))"
ACTIVE = """(n.kind<>'overdue' OR (a.status='pending' AND n.version=a.version AND n.id=(
    SELECT MAX(last.id) FROM finance_notifications last WHERE last.application_id=n.application_id AND last.kind='overdue')))"""


def overdue_reminders(db, now):
    when = now.isoformat(timespec='seconds').replace('+00:00','Z')
    cutoff = (now-timedelta(days=7)).isoformat(timespec='seconds').replace('+00:00','Z')
    rows = db.execute('''SELECT a.* FROM finance_applications a WHERE a.status='pending' AND
        COALESCE((SELECT e.created_at FROM finance_events e WHERE e.application_id=a.id
        AND e.action IN ('submitted','resubmitted','legacy') ORDER BY e.rowid DESC LIMIT 1),a.created_at)<=?''',(cutoff,)).fetchall()
    for item in rows:
        last = db.execute("SELECT id,created_at FROM finance_notifications WHERE application_id=? AND kind='overdue' ORDER BY id DESC LIMIT 1",(item['id'],)).fetchone()
        payload = json.dumps({'project':item['project'],'amount':f"{item['amount_cents']/100:.2f}",'result':'pending'},ensure_ascii=False)
        if last and datetime.fromisoformat(last['created_at'].replace('Z','+00:00')) > now-timedelta(days=1):
            db.execute('UPDATE finance_notifications SET version=?,payload_json=? WHERE id=?',(item['version'],payload,last['id']))
            continue
        db.execute('''INSERT OR IGNORE INTO finance_notifications(application_id,kind,audience,user_id,payload_json,version,created_at,dedupe_key)
            VALUES(?,'overdue','admins','',?,?,?,?)''',(item['id'],payload,item['version'],when,'overdue:'+item['id']+':'+now.date().isoformat()))


def notification_page(store, user, page=1, size=20, *, now=None):
    admin = user.get('role') in ADMIN_ROLES
    with store._lock, closing(store._connect()) as db, db:
        db.execute('BEGIN IMMEDIATE' if admin else 'BEGIN')
        if admin:
            overdue_reminders(db,now or datetime.now(timezone.utc))
        args = (user['id'],int(admin),user['id'])
        source = f'''FROM finance_notifications n JOIN finance_applications a ON a.id=n.application_id
            LEFT JOIN finance_notification_reads r ON r.notification_id=n.id AND r.user_id=? WHERE {SCOPE} AND {ACTIVE}'''
        count = db.execute('SELECT COUNT(*) AS total,COALESCE(SUM(r.notification_id IS NULL),0) AS unread,COALESCE(MAX(n.id),0) AS max_id '+source,args).fetchone()
        rows = db.execute('SELECT n.*,a.status AS current_status,a.version AS current_version,r.notification_id IS NULL AS unread '+source+
            ' ORDER BY n.id DESC LIMIT ? OFFSET ?',(*args,size,(page-1)*size)).fetchall()
    items = [{key:row[key] for key in ('id','application_id','kind','version','created_at','current_status','current_version')}
             | {'unread':bool(row['unread']),'payload':json.loads(row['payload_json'])} for row in rows]
    return {'items':items,'total':count['total'],'unread_count':count['unread'],'max_id':count['max_id'],
            'has_more':page*size<count['total'],'page':page}


def install_finance_notifications(app, store):
    router = APIRouter()

    def user(request):
        value = getattr(request.state,'site_user',None)
        if not value:
            raise HTTPException(401,'请先登录')
        return value

    @router.get('/api/finance/notifications')
    def listing(request: Request, page: int=Query(1,ge=1), page_size: int=Query(20,ge=1,le=100)):
        return JSONResponse(notification_page(store,user(request),page,page_size),headers={'Cache-Control':'no-store, private'})

    @router.post('/api/finance/notifications/read')
    async def mark_read(request: Request):
        actor=user(request);payload=await read_payload(request,{'ids','through_id'})
        ids, through = payload.get('ids'), payload.get('through_id')
        if ids is not None and through is not None:
            raise HTTPException(400,'通知已读请求无效')
        if ids is not None:
            if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(type(value) is not int or not 1<=value<2**63 for value in ids):
                raise HTTPException(400,'通知编号无效')
            extra='n.id IN ('+','.join('?' for _ in ids)+')'; values=ids
        elif type(through) is int and 0<=through<2**63:
            extra='n.id<=?';values=[through]
        else:
            raise HTTPException(400,'通知已读请求无效')
        with store._lock, closing(store._connect()) as db, db:
            db.execute(f'''INSERT OR IGNORE INTO finance_notification_reads(user_id,notification_id)
                SELECT ?,n.id FROM finance_notifications n JOIN finance_applications a ON a.id=n.application_id
                WHERE {SCOPE} AND {ACTIVE} AND {extra}''',
                (actor['id'],int(actor.get('role') in ADMIN_ROLES),actor['id'],*values))
        return {'ok':True}

    app.include_router(router)
