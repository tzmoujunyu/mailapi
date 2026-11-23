"""Login, website-account and group HTTP endpoints."""
import json
from html import escape
import os
import re
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from .site_accounts import SESSION_SECONDS, SiteAccountError
from .web_security import LoginThrottle
from .login_protection import ChallengeUnavailable, ProtectedLoginThrottle


SESSION_COOKIE = "mailapi_session"


def themed_page(page, request):
    user = getattr(request.state, "site_user", None)
    theme = request.app.state.site_accounts.preferences(user["id"])["theme"] if user else "system"
    page = page.replace('data-theme="system"', f'data-theme="{theme}"', 1).replace(
        'data-theme-account=""', 'data-theme-account="signed-in"' if user else 'data-theme-account=""', 1,
    )
    if os.getenv("MAILAPI_MODE", "test").strip().lower() == "test":
        path = request.url.path
        page_name = "home" if path in {"/", "/preview"} else "admin" if path == "/admin" else "access" if path == "/admin/access" else "login" if path == "/login" else "features" if path == "/test-features" else ""
        if page_name in {"home", "admin", "access", "features"}:
            from .test_navigation import prepare_workspace_page
            page = prepare_workspace_page(page, page_name)
            page = page.replace('data-test-view="' + page_name + '"',
                                'data-test-view="' + page_name + '" data-test-account-id="' + escape(user["id"], quote=True)
                                + '" data-test-account-role="' + escape(user["role"], quote=True) + '"', 1)
            if path == "/preview":
                subject_id = request.query_params.get("user_id") or user["id"]
                page = page.replace('data-test-view="home"',
                                    'data-test-view="home" data-test-preview-user="' + escape(subject_id, quote=True) + '"', 1)
        language = request.app.state.site_accounts.interface_preferences(user["id"])["language"] if user else "zh-CN"
        page = page.replace('lang="zh-CN"', f'lang="{language}" data-interface-language="{language}"', 1)
        page = page.replace("<head>", '<head><script src="/static/test-i18n-en.js" defer></script><script src="/static/test-i18n.js" defer></script>', 1)
        if page_name:
            def add_body_classes(match):
                attrs = match.group(1) or ""
                classes = f"test-ui-page test-ui-{page_name}"
                class_match = re.search(r'class="([^"]*)"', attrs)
                if class_match:
                    attrs = attrs[:class_match.start(1)] + class_match.group(1) + " " + classes + attrs[class_match.end(1):]
                else:
                    attrs += f' class="{classes}"'
                return f"<body{attrs}>"

            page = re.sub(r"<body([^>]*)>", add_body_classes, page, count=1)
        if page_name in {"home", "admin", "access", "features"}:
            management_link = (
                '<a href="/admin/access" aria-label="账户" title="账户"><span aria-hidden="true">▦</span><span class="test-ui-nav-label">账户</span></a>'
                if user and user.get("role") in {"admin", "manager"} else ""
            )
            resource_link = (
                '<a href="/admin" aria-label="控制台" title="控制台"><span aria-hidden="true">⚙</span><span class="test-ui-nav-label">控制台</span></a>'
                if user and user.get("role") == "admin" else ""
            )
            sidebar = (
                '<aside class="test-ui-sidebar" aria-label="测试版导航">'
                '<div class="test-ui-logo" aria-label="MailAPI 测试版">M</div>'
                '<nav class="test-ui-nav">'
                '<a href="/test-features#view-overview" aria-label="主页" title="主页"><span aria-hidden="true">▦</span><span class="test-ui-nav-label">主页</span></a>'
                '<a href="/" aria-label="资源" title="资源"><span aria-hidden="true">◈</span><span class="test-ui-nav-label">资源</span></a>'
                + management_link + resource_link +
                '<a href="/test-features#view-api2api" aria-label="API" title="API"><span aria-hidden="true">⌁</span><span class="test-ui-nav-label">API</span></a>'
                '<a href="/test-features#view-finance" aria-label="财务" title="财务"><span aria-hidden="true">¥</span><span class="test-ui-nav-label">财务</span></a>'
                '</nav><div class="test-ui-sidebar-footer"><a href="/login" aria-label="重新登录" title="登录">↻</a></div></aside>'
            )
            body_end = page.find(">", page.find("<body"))
            if body_end >= 0:
                page = page[:body_end + 1] + sidebar + page[body_end + 1:]
        if page_name == "login":
            page = page.replace('<form id="login-form" method="post" action="/login">', '<form id="login-form" method="post" action="/login"><div class="test-ui-login-brand" aria-hidden="true" style="display:grid;width:50px;height:50px;place-items:center;margin-bottom:22px;border-radius:16px;background:#4f24ea;color:#fff;font-size:28px;font-weight:800;box-shadow:0 10px 24px #4f24ea33;">M</div>', 1)
        router_script = '<script src="/static/test-router.js" type="module"></script>' if page_name in {"home", "admin", "access", "features"} else ""
        page = page.replace(
            "</head>",
            '<link rel="stylesheet" href="/static/test-ui.css"><script src="/static/test-ui.js" defer></script><link rel="stylesheet" href="/static/test-header.css"><script src="/static/test-header.js" defer></script><script src="/static/test-notifications.js" defer></script>' + router_script + '</head>',
            1,
        )
    return page


async def read_payload(request, fields):
    body = await request.body()
    if len(body) > 65536:
        raise SiteAccountError("请求内容过长", 413)
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise SiteAccountError("请求必须是 JSON 对象") from error
    if not isinstance(payload, dict) or set(payload) - fields:
        raise SiteAccountError("请求包含无效字段")
    return payload


def create_account_router(static_dir, resource_catalog, *, on_admin_failure=None, login_verifier=None):
    router = APIRouter()
    throttle = ProtectedLoginThrottle() if login_verifier else LoginThrottle()

    def store(request):
        return request.app.state.site_accounts

    def user_id(request):
        user = request.state.site_user
        if not user:
            raise SiteAccountError("请先使用网站账户登录", 401)
        return user["id"]

    def challenge():
        return {"site_key": login_verifier.site_key, "testing": login_verifier.testing}

    def login_page_html(request, message="", required=False):
        page = (static_dir / "login.html").read_text(encoding="utf-8")
        assets = '<script src="/static/login-protection.js" defer></script>' if login_verifier else ""
        host = ""
        if login_verifier:
            notice = '<p class="challenge-demo">演示验证：仅用于隔离测试</p>' if login_verifier.testing else ""
            host = (
                f'<section id="login-challenge" data-sitekey="{escape(login_verifier.site_key, quote=True)}" '
                f'data-required="{int(required)}" aria-label="安全验证"' + ("" if required else " hidden") + '>'
                '<p>为保护账户，请先完成安全验证。</p>' + notice +
                '<div id="login-challenge-widget"></div><p id="login-challenge-status" role="status"></p>'
                '<button id="login-challenge-retry" type="button" hidden>重新加载验证</button>'
                '<noscript><p>此登录需要启用 JavaScript 完成安全验证。</p></noscript></section>'
            )
        return themed_page(page.replace("<!-- LOGIN_ERROR -->", escape(message)).replace(
            "<!-- LOGIN_PROTECTION -->", assets).replace("<!-- LOGIN_CHALLENGE -->", host), request)

    def login_error(request, browser, message, status, headers=None, *, required=False, code=None):
        if browser:
            return HTMLResponse(login_page_html(request, message, required), status_code=status, headers=headers)
        payload = {"detail": message}
        if required:
            payload["challenge"] = challenge()
        if code:
            payload["code"] = code
        return JSONResponse(payload, status_code=status, headers=headers)

    def login_response(request, username, password, totp_code, turnstile_token=None, *, browser=False):
        address = request.client.host if request.client else ""
        attempt = throttle.begin(address, username) if login_verifier else None
        retry_after = attempt.retry_after if attempt else throttle.check(address, username)
        if retry_after:
            return login_error(request, browser, "登录尝试过多，请稍后再试", 429, {"Retry-After": str(retry_after)})
        authenticated = None
        try:
            if attempt and attempt.challenge_required:
                if not turnstile_token:
                    return login_error(request, browser, "请先完成安全验证", 403, required=True, code="challenge_required")
                try:
                    valid = login_verifier.verify(turnstile_token, address)
                except ChallengeUnavailable:
                    return login_error(request, browser, "安全验证暂时不可用，请稍后重试", 503,
                                       {"Retry-After": "30"}, required=True, code="verification_unavailable")
                if not valid:
                    return login_error(request, browser, "安全验证未通过或已过期，请重新验证", 403,
                                       required=True, code="challenge_failed")
            result = store(request).login(username, password, totp_code, on_admin_failure=on_admin_failure)
            authenticated = result is not None
        finally:
            if attempt:
                throttle.finish(attempt, authenticated)
        if result is None:
            required = bool(login_verifier and throttle.challenge_required(address, username))
            return login_error(request, browser, "用户名、密码或动态验证码错误", 401, required=required)
        token, user = result
        landing = "/test-features#view-overview" if os.getenv("MAILAPI_MODE", "test").strip().lower() == "test" else "/"
        payload = {"user": user, **({"redirect": landing} if login_verifier else {})}
        response = RedirectResponse(landing, status_code=303) if browser else JSONResponse(payload)
        response.set_cookie(SESSION_COOKIE, token, httponly=True, max_age=SESSION_SECONDS,
                            samesite="lax", secure=request.url.scheme == "https")
        return response

    @router.get("/login")
    def login_page(request: Request):
        if request.state.site_user:
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(login_page_html(request))

    @router.post("/login")
    def login_form(
        request: Request,
        username: Optional[str] = Form(None),
        password: Optional[str] = Form(None),
        totp_code: Optional[str] = Form(None),
        turnstile_token: Optional[str] = Form(None, alias="cf-turnstile-response"),
    ):
        return login_response(request, username, password, totp_code, turnstile_token, browser=True)

    @router.post("/api/auth/login")
    async def login_api(request: Request):
        payload = await read_payload(request, {"username", "password", "totp_code", "turnstile_token"})
        return await run_in_threadpool(
            login_response,
            request,
            payload.get("username"),
            payload.get("password"),
            payload.get("totp_code"),
            payload.get("turnstile_token"),
        )

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

    @router.get("/api/auth/preferences")
    def preferences(request: Request):
        return store(request).preferences(user_id(request))

    @router.put("/api/auth/preferences")
    async def update_preferences(request: Request):
        payload = await read_payload(request, {"theme"})
        return await run_in_threadpool(store(request).set_theme, user_id(request), payload.get("theme"))

    @router.post("/api/auth/password")
    async def change_password(request: Request):
        payload = await read_payload(request, {"current_password", "new_password"})
        await run_in_threadpool(
            store(request).change_password, user_id(request), payload.get("current_password"), payload.get("new_password"),
        )
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE)
        return response

    @router.get("/admin/access")
    def access_page(request: Request):
        return HTMLResponse(themed_page((static_dir / "access.html").read_text(encoding="utf-8"), request))

    @router.get("/api/admin/users")
    def list_users(request: Request):
        return {"items": store(request).list_users(user_id(request))}

    @router.get("/api/admin/auth-artifact-versions")
    def list_auth_artifact_versions(request: Request):
        store(request).require_admin(user_id(request))
        return {"items": request.app.state.auth_artifact_versions.list_public()}

    @router.post("/api/admin/users", status_code=201)
    async def create_user(request: Request):
        payload = await read_payload(request, {"username", "password", "role", "note"})
        return {"item": await run_in_threadpool(
            store(request).create_user, user_id(request), payload.get("username"), payload.get("password"), payload.get("role", "user"),
            note=payload.get("note", ""),
        )}

    @router.post("/api/admin/users/batch", status_code=201)
    async def create_users_batch(request: Request):
        payload = await read_payload(request, {"usernames", "password", "role"})
        return {
            "items": await run_in_threadpool(
                store(request).create_users,
                user_id(request),
                payload.get("usernames"),
                payload.get("password"),
                payload.get("role", "user"),
                admin_only=True,
            )
        }

    @router.put("/api/admin/users/{target_id}")
    async def update_user(target_id: str, request: Request):
        payload = await read_payload(request, {"role", "password", "note"})
        return {"item": await run_in_threadpool(store(request).update_user, user_id(request), target_id, payload)}

    @router.post("/api/admin/users/initial-password-scan")
    async def scan_initial_password(request: Request):
        payload = await read_payload(request, {"password"})
        return await run_in_threadpool(
            store(request).scan_initial_password,
            user_id(request),
            payload.get("password"),
        )

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

    @router.put("/api/admin/resource-groups")
    async def assign_resource_groups(request: Request):
        payload = await read_payload(request, {"resource_key", "group_ids"})
        return {"items": store(request).assign_resource_groups(
            user_id(request), payload.get("resource_key"), payload.get("group_ids"),
            {item["key"] for item in resource_catalog()},
        )}

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
