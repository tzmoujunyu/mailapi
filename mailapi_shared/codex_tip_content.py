"""Validated structured content and private WebP images for usage tips."""
import html
import io
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from html.parser import HTMLParser
from urllib.parse import urlsplit
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from .site_accounts import SiteAccountError


register_heif_opener()
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_NAME = re.compile(r"[0-9a-f]{32}(?:\.preview)?\.webp")
IMAGE_PREFIX = "/api/codex-tip-images/"
IMAGE_LOCK = threading.Lock()


class InlineText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.text = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag in {"b", "strong", "i", "em", "code"}:
            self.parts.append(f"<{tag}>")
            self.stack.append(tag)
        elif tag == "br":
            self.parts.append("<br>")
            self.text.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href", "")
            parsed = urlsplit(href)
            if parsed.scheme in {"http", "https"} and parsed.hostname and not any(ord(c) < 32 for c in href):
                self.parts.append(f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">')
                self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack:
                name = self.stack.pop()
                self.parts.append(f"</{name}>")
                if name == tag:
                    break

    def handle_data(self, text):
        self.parts.append(html.escape(text))
        self.text.append(text)


def clean_inline(value):
    if not isinstance(value, str) or len(value) > 32000:
        raise SiteAccountError("文本格式无效或内容过长")
    parser = InlineText()
    try:
        value.encode("utf-8")
        parser.feed(value)
        parser.close()
    except (ValueError, UnicodeError):
        raise SiteAccountError("文本包含无效内容") from None
    parser.parts.extend(f"</{tag}>" for tag in reversed(parser.stack))
    return "".join(parser.parts), "".join(parser.text)


def legacy_blocks(body):
    return [{"type": "paragraph", "data": {"text": html.escape(body).replace("\n", "<br>")}}] if body else []


def image_directory(tips_file):
    return Path(tips_file).parent / "codex_tip_images"


def image_file(directory, filename):
    if not IMAGE_NAME.fullmatch(filename):
        raise SiteAccountError("未找到图片", 404)
    path = directory / filename
    if path.is_symlink() or not path.is_file():
        raise SiteAccountError("未找到图片", 404)
    return path


def image_info(directory, url, prefix=IMAGE_PREFIX):
    if not isinstance(url, str) or not url.startswith(prefix):
        raise SiteAccountError("请上传图片后再保存")
    name = url.removeprefix(prefix)
    if ".preview." in name:
        raise SiteAccountError("请选择完整图片")
    path = image_file(directory, name)
    with Image.open(path) as image:
        return {"url": url, "width": image.width, "height": image.height,
                "preview": prefix + path.stem + ".preview.webp"}


def validate_blocks(blocks, directory, prefix=IMAGE_PREFIX):
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 100:
        raise SiteAccountError("正文须包含 1–100 个内容块")
    result, plain = [], []

    def inline(value):
        cleaned, text = clean_inline(value)
        plain.append(text)
        return cleaned

    def list_meta(value):
        if not isinstance(value, dict):
            raise SiteAccountError("列表编号格式无效")
        result = {}
        if "start" in value:
            if type(value["start"]) is not int or not 0 <= value["start"] <= 1_000_000:
                raise SiteAccountError("列表起始编号无效")
            result["start"] = value["start"]
        if "type" in value:
            if value["type"] not in ("1", "a", "A", "i", "I"):
                raise SiteAccountError("列表编号样式无效")
            result["type"] = value["type"]
        return result

    def list_items(items, depth=0):
        if not isinstance(items, list) or not items or depth > 3 or len(items) > 100:
            raise SiteAccountError("列表格式无效或层级过深")
        result = []
        for item in items:
            if not isinstance(item, dict) or "content" not in item:
                raise SiteAccountError("列表项格式无效")
            entry = {"content": inline(item["content"]), "meta": list_meta(item.get("meta", {})),
                     "items": list_items(item["items"], depth + 1) if item.get("items") else []}
            if "style" in item:
                if item["style"] not in ("ordered", "unordered"):
                    raise SiteAccountError("嵌套列表样式无效")
                entry["style"] = item["style"]
            result.append(entry)
        return result

    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("data"), dict):
            raise SiteAccountError("正文内容块格式无效")
        kind, data = block.get("type"), block["data"]
        if kind == "paragraph":
            content = {"text": inline(data.get("text", ""))}
        elif kind == "header" and data.get("level") in (2, 3):
            content = {"text": inline(data.get("text", "")), "level": data["level"]}
        elif kind == "list" and data.get("style") in ("ordered", "unordered"):
            content = {"style": data["style"], "meta": list_meta(data.get("meta", {})), "items": list_items(data.get("items"))}
        elif kind == "code" and isinstance(data.get("code"), str):
            content = {"code": data["code"]}
            plain.append(data["code"])
        elif kind == "image" and isinstance(data.get("file"), dict):
            content = {"file": image_info(directory, data["file"].get("url"), prefix), "caption": inline(data.get("caption", ""))}
        else:
            raise SiteAccountError("正文包含不支持的格式")
        result.append({"type": kind, "data": content})
    text = "\n".join(plain).strip()
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise SiteAccountError("正文包含无效字符") from None
    if len(text) > 8000 or (not text and not any(block["type"] == "image" for block in result)):
        raise SiteAccountError("正文不能为空，文字最多 8000 个字符")
    return result, text


def save_image(raw, directory, prefix=IMAGE_PREFIX):
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise SiteAccountError("图片不能为空，单张最多 8 MB", 413)
    with IMAGE_LOCK, warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP", "GIF", "HEIF", "AVIF"}:
                    raise ValueError
                if source.width * source.height > 24_000_000:
                    raise SiteAccountError("图片最多 2400 万像素")
                source.seek(0)
                alpha = "A" in source.getbands() or "transparency" in source.info
                image = ImageOps.exif_transpose(source).convert("RGBA" if alpha else "RGB")
                lossless = source.format in {"PNG", "GIF"}
                image.thumbnail((2560, 12000), Image.Resampling.LANCZOS)
                full = io.BytesIO()
                image.save(full, "WEBP", lossless=lossless, quality=85, method=4)
                width, height = image.size
                image.thumbnail((480, 1200), Image.Resampling.LANCZOS)
                preview = io.BytesIO()
                image.save(preview, "WEBP", quality=80, method=4)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise SiteAccountError("图片无法解析，请选择有效的 JPEG、PNG、WebP、GIF、HEIC 或 AVIF") from None
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = secrets.token_hex(16)
        outputs = {name + ".webp": full.getvalue(), name + ".preview.webp": preview.getvalue()}
        try:
            for filename, value in outputs.items():
                descriptor, temporary = tempfile.mkstemp(dir=directory)
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        output.write(value)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, directory / filename)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
        except OSError:
            for filename in outputs:
                (directory / filename).unlink(missing_ok=True)
            raise
        return {"url": prefix + name + ".webp", "preview": prefix + name + ".preview.webp",
                "width": width, "height": height}
