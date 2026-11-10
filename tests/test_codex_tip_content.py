import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from mailapi_shared.codex_tip_content import clean_inline, image_file, legacy_blocks, save_image, validate_blocks
from mailapi_shared.site_accounts import SiteAccountError


class CodexTipContentTests(unittest.TestCase):
    def test_legacy_text_stays_literal_and_rich_text_has_a_small_allowlist(self):
        old = '第一行 <script>例子</script>\n第二行'
        self.assertEqual(legacy_blocks(old)[0]["data"]["text"], '第一行 &lt;script&gt;例子&lt;/script&gt;<br>第二行')
        formatted, text = clean_inline('<b onclick="x()">加粗</b> <i>斜体</i><a href="javascript:alert(1)">链接</a><img src=x onerror=x()>')
        self.assertEqual(formatted, '<b>加粗</b> <i>斜体</i>链接')
        self.assertEqual(text, '加粗 斜体链接')

    def test_images_convert_to_private_webp_without_metadata_and_keep_alpha(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = Image.new('RGBA', (1200, 600), (20, 30, 40, 128))
            buffer = io.BytesIO()
            exif = Image.Exif()
            exif[270] = 'private metadata'
            source.save(buffer, 'PNG', exif=exif)
            file = save_image(buffer.getvalue(), root)
            self.assertEqual((file['width'], file['height']), (1200, 600))
            for url in (file['url'], file['preview']):
                path = image_file(root, url.rsplit('/', 1)[-1])
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                with Image.open(path) as output:
                    self.assertEqual(output.format, 'WEBP')
                    self.assertFalse(output.getexif())
                    self.assertIn('A', output.getbands())
            with Image.open(root / file['url'].rsplit('/', 1)[-1]) as output:
                self.assertEqual(output.getpixel((20, 20)), source.getpixel((20, 20)))
            blocks, plain = validate_blocks([{'type': 'image', 'data': {'file': {**file, 'width': 1}, 'caption': '<b>说明</b>'}}], root)
            self.assertEqual(blocks[0]['data']['file']['width'], 1200)
            self.assertEqual(plain, '说明')
            with self.assertRaises(SiteAccountError):
                image_file(root, '../anything.webp')
            with self.assertRaises(SiteAccountError):
                save_image(b'<svg onload="bad"/>', root)

    def test_blocks_validate_links_types_and_local_image_references(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blocks = [
                {'type': 'header', 'data': {'text': '步骤', 'level': 2}},
                {'type': 'list', 'data': {'style': 'unordered', 'items': [{'content': '<b>配置</b>', 'items': []}]}},
                {'type': 'code', 'data': {'code': 'x = "<example>"'}},
            ]
            result, body = validate_blocks(blocks, root)
            self.assertIn('步骤\n配置\nx = "<example>"', body)
            self.assertEqual(result[2], blocks[2])
            for invalid in ([], [{'type': 'raw', 'data': {'html': '<script>x</script>'}}],
                            [{'type': 'header', 'data': {'text': 'x', 'level': []}}],
                            [{'type': 'image', 'data': {'file': {'url': 'https://external.example/x.webp'}}}],
                            [{'type': 'list', 'data': {'style': 'ordered', 'items': [None]}}]):
                with self.subTest(invalid=invalid), self.assertRaises(SiteAccountError):
                    validate_blocks(invalid, root)
