"""Shared announcement content validation and authenticated image storage."""
import json

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from .codex_tip_content import MAX_IMAGE_BYTES, image_file, save_image, validate_blocks
from .site_accounts import ADMIN_ROLES, SiteAccountError


def content_directory(store, scope):
    return store.root / 'announcement_images' / scope


def validate_content(store, scope, body, blocks=None):
    if blocks is not None:
        clean, text = validate_blocks(blocks, content_directory(store, scope), f'/api/content-images/{scope}/')
        return text, json.dumps(clean, ensure_ascii=False)
    if not isinstance(body, str) or not 1 <= len(body.strip()) <= 8000:
        raise SiteAccountError('公告内容须为 1–8000 个字符')
    return body.strip(), None


def public_content(row):
    item = dict(row)
    blocks = item.pop('blocks_json', None)
    if blocks is not None:
        item.update(blocks=json.loads(blocks), doc_version=1)
    return item


def install_announcement_images(app, store):
    router = APIRouter()

    def authorize(request, scope, upload=False):
        if scope not in {'site', 'finance'}:
            raise HTTPException(404, '未找到图片')
        user = getattr(request.state, 'site_user', None)
        if not user:
            raise HTTPException(401, '请先登录')
        if upload and (user['role'] != 'admin' if scope == 'site' else user['role'] not in ADMIN_ROLES):
            raise HTTPException(403, '无权编辑此公告')

    @router.post('/api/announcement-images/{scope}', status_code=201)
    async def upload(scope: str, request: Request, image: UploadFile = File(...)):
        authorize(request, scope, True)
        try:
            raw = await image.read(MAX_IMAGE_BYTES + 1)
            file = await run_in_threadpool(save_image, raw, content_directory(store, scope), f'/api/content-images/{scope}/')
            return {'success': 1, 'file': file}
        except SiteAccountError as error:
            raise HTTPException(error.status_code, str(error)) from error
        finally:
            await image.close()

    @router.get('/api/content-images/{scope}/{filename}')
    def download(scope: str, filename: str, request: Request):
        authorize(request, scope)
        try:
            return FileResponse(image_file(content_directory(store, scope), filename), media_type='image/webp',
                                headers={'Cache-Control': 'private, no-cache'})
        except SiteAccountError as error:
            raise HTTPException(error.status_code, str(error)) from error

    app.include_router(router)
