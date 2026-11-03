"""Login, website-account and group HTTP endpoints."""
import json

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse

from site_accounts import SESSION_SECONDS, SiteAccountError


SESSION_COOKIE = "mailapi_session"


async def read_payload(request, fields):
    body = await request.body()
    if len(body) > 65536:
        raise SiteAccountError("请求内容过长", 413)
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SiteAccountError("请求必须是 JSON 对象") from error
    if not isinstance(payload, dict) or set(payload) - fields:
        raise SiteAccountError("请求包含无效字段")
    return payload


def create_account_router(static_dir, resource_catalog):
    router = APIRouter()

    def store(request):
        return request.app.state.site_accounts

    def user_id(request):
        return request.state.site_user["id"]

    def login_response(request, username, password, *, browser=False):
        result = store(request).login(username, password)
        if result is None:
            if browser:
                page = (static_dir / "login.html").read_text(encoding="utf-8")
                return HTMLResponse(page.replace("<!-- LOGIN_ERROR -->", "用户名或密码错误"), status_code=401)
            raise SiteAccountError("用户名或密码错误", 401)
        token, user = result
        response = RedirectResponse("/", status_code=303) if browser else JSONResponse({"user": user})
        response.set_cookie(SESSION_COOKIE, token, httponly=True, max_age=SESSION_SECONDS,
                            samesite="lax", secure=request.url.scheme == "https")
        return response

    @router.get("/login")
    def login_page(request: Request):
        if request.state.site_user:
            return RedirectResponse("/", status_code=303)
        page = (static_dir / "login.html").read_text(encoding="utf-8")
        return HTMLResponse(page.replace("<!-- LOGIN_ERROR -->", ""))

    @router.post("/login")
    def login_form(request: Request, username: str = Form(...), password: str = Form(...)):
        return login_response(request, username, password, browser=True)

    @router.post("/api/auth/login")
    async def login_api(request: Request):
        payload = await read_payload(request, {"username", "password"})
        return login_response(request, payload.get("username"), payload.get("password"))

    @router.post("/api/auth/logout")
    @router.post("/logout")
    def logout(request: Request):
        store(request).logout(request.cookies.get(SESSION_COOKIE, ""))
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE)
        return response

    @router.get("/api/auth/me")
    def me(request: Request):
        return {"user": request.state.site_user, "groups": store(request).user_groups(user_id(request))}

    @router.post("/api/auth/password")
    async def change_password(request: Request):
        payload = await read_payload(request, {"current_password", "new_password"})
        store(request).change_password(user_id(request), payload.get("current_password"), payload.get("new_password"))
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE)
        return response

    @router.get("/admin/access")
    def access_page():
        return FileResponse(static_dir / "access.html")

    @router.get("/api/admin/users")
    def list_users(request: Request):
        return {"items": store(request).list_users(user_id(request))}

    @router.post("/api/admin/users", status_code=201)
    async def create_user(request: Request):
        payload = await read_payload(request, {"username", "password", "role"})
        return {"item": store(request).create_user(user_id(request), payload.get("username"), payload.get("password"), payload.get("role", "user"))}

    @router.put("/api/admin/users/{target_id}")
    async def update_user(target_id: str, request: Request):
        payload = await read_payload(request, {"role", "password"})
        return {"item": store(request).update_user(user_id(request), target_id, payload)}

    @router.delete("/api/admin/users/{target_id}")
    def delete_user(target_id: str, request: Request):
        store(request).delete_user(user_id(request), target_id)
        return {"ok": True}

    @router.get("/api/admin/groups")
    def list_groups(request: Request):
        return {"items": store(request).list_groups(user_id(request))}

    @router.get("/api/admin/resources")
    def list_resources():
        return {"items": resource_catalog()}

    @router.post("/api/admin/groups", status_code=201)
    async def create_group(request: Request):
        payload = await read_payload(request, {"name", "member_ids", "resource_keys"})
        return {"item": store(request).save_group(user_id(request), None, payload, {item["key"] for item in resource_catalog()})}

    @router.put("/api/admin/groups/{group_id}")
    async def update_group(group_id: str, request: Request):
        payload = await read_payload(request, {"name", "member_ids", "resource_keys"})
        return {"item": store(request).save_group(user_id(request), group_id, payload, {item["key"] for item in resource_catalog()})}

    @router.delete("/api/admin/groups/{group_id}")
    def delete_group(group_id: str, request: Request):
        store(request).delete_group(user_id(request), group_id)
        return {"ok": True}

    return router
