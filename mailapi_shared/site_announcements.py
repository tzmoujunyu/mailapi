"""Independent website announcements, mounted only in the isolated test application."""
from contextlib import closing
from datetime import datetime, timezone
import secrets
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .accounts import read_payload
from .announcement_content import public_content, validate_content
from .site_accounts import SiteAccountError


SCHEMA = """
CREATE TABLE IF NOT EXISTS site_announcements (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
    pinned INTEGER NOT NULL, revision INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, author_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS site_announcement_reads (
    user_id TEXT NOT NULL, announcement_id TEXT NOT NULL REFERENCES site_announcements(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL, PRIMARY KEY(user_id, announcement_id)
);
"""


def install_site_announcements(app, store):
    with closing(store._connect()) as db, db:
        db.executescript(SCHEMA)
        if 'blocks_json' not in {row[1] for row in db.execute('PRAGMA table_info(site_announcements)')}:
            db.execute('ALTER TABLE site_announcements ADD COLUMN blocks_json TEXT')
    router = APIRouter()

    def identity(request, *, admin=False):
        user = getattr(request.state, "site_user", None)
        if not user:
            raise HTTPException(401, "请先登录")
        if admin and user["role"] != "admin":
            raise HTTPException(403, "仅 admin 可以管理网站公告")
        return user

    def listing(user_id):
        with closing(store._connect()) as db:
            rows = db.execute("""
                SELECT a.*, COALESCE(r.revision,0) < a.revision AS unread
                FROM site_announcements a LEFT JOIN site_announcement_reads r
                ON r.announcement_id=a.id AND r.user_id=?
                ORDER BY a.pinned DESC, a.updated_at DESC, a.id DESC
            """, (user_id,)).fetchall()
        return [{**{key: value for key, value in public_content(row).items() if key != "author_id"}, "pinned": bool(row["pinned"]), "unread": bool(row["unread"])} for row in rows]

    @router.get("/api/site-announcements")
    def list_announcements(request: Request):
        user = identity(request)
        return JSONResponse({"items": listing(user["id"]), "can_manage": user["role"] == "admin"},
                            headers={"Cache-Control": "no-store, private"})

    async def save(request, announcement_id=None):
        user = identity(request, admin=True)
        payload = await read_payload(request, {"title", "body", "pinned", "revision", "blocks", "doc_version"})
        title, body, pinned = payload.get("title"), payload.get("body"), payload.get("pinned", False)
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
            raise HTTPException(400, "公告标题须为 1–120 个字符")
        try:
            body, blocks = validate_content(store, 'site', body, payload.get('blocks'))
        except SiteAccountError as error:
            raise HTTPException(error.status_code, str(error)) from error
        if not isinstance(pinned, bool):
            raise HTTPException(400, "置顶设置无效")
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with closing(store._connect()) as db, db:
            if announcement_id:
                revision = payload.get("revision")
                if type(revision) is not int or revision < 1:
                    raise HTTPException(400, "公告版本无效")
                changed = db.execute("""
                    UPDATE site_announcements SET title=?,blocks_json=CASE WHEN ? IS NULL AND body=? THEN blocks_json ELSE ? END,
                    body=?,pinned=?,updated_at=?,revision=revision+1
                    WHERE id=? AND revision=?
                """, (title.strip(), blocks, body, blocks, body, int(pinned), now, announcement_id, revision))
                if changed.rowcount != 1:
                    raise HTTPException(409, "公告已被修改或删除，请刷新后重试")
            else:
                announcement_id = secrets.token_hex(12)
                db.execute("INSERT INTO site_announcements(id,title,body,pinned,revision,created_at,updated_at,author_id,blocks_json) VALUES (?,?,?,?,?,?,?,?,?)",
                           (announcement_id, title.strip(), body, int(pinned), 1, now, now, user["id"], blocks))
        return {"ok": True}

    @router.post("/api/admin/site-announcements", status_code=201)
    async def create(request: Request):
        return await save(request)

    @router.put("/api/admin/site-announcements/{announcement_id}")
    async def update(announcement_id: str, request: Request):
        return await save(request, announcement_id)

    @router.delete("/api/admin/site-announcements/{announcement_id}")
    def delete(announcement_id: str, request: Request):
        identity(request, admin=True)
        with closing(store._connect()) as db, db:
            if db.execute("DELETE FROM site_announcements WHERE id=?", (announcement_id,)).rowcount != 1:
                raise HTTPException(404, "未找到网站公告")
        return {"ok": True}

    @router.post("/api/site-announcements/read")
    async def mark_read(request: Request):
        user = identity(request)
        payload = await read_payload(request, {"items"})
        items = payload.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise HTTPException(400, "已读列表无效")
        with closing(store._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            for item in items:
                if (not isinstance(item, dict) or set(item) != {"id", "revision"}
                        or not isinstance(item["id"], str) or type(item["revision"]) is not int or item["revision"] < 1):
                    raise HTTPException(400, "公告版本无效")
                row = db.execute("SELECT revision FROM site_announcements WHERE id=?", (item["id"],)).fetchone()
                if not row:
                    continue
                if item["revision"] > row["revision"]:
                    raise HTTPException(400, "公告版本无效")
                db.execute("""
                    INSERT INTO site_announcement_reads VALUES (?,?,?)
                    ON CONFLICT(user_id,announcement_id) DO UPDATE SET revision=MAX(revision,excluded.revision)
                """, (user["id"], item["id"], item["revision"]))
        return {"ok": True}

    app.include_router(router)
