"""Private, cached finance attachment images. Rendering does not run recognition."""
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def attachment_preview(source, cache_root, key):
    directory = cache_root / key
    manifest = directory / 'manifest.json'
    if not manifest.is_file():
        cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.render-', dir=cache_root) as temporary:
            subprocess.run([sys.executable,'-m','mailapi_shared.finance_previews',str(source),temporary],
                cwd=Path(__file__).resolve().parent.parent, check=True, timeout=20, capture_output=True)
            try:
                os.rename(temporary,directory)
            except OSError:
                if not manifest.is_file():
                    raise
    return directory, json.loads(manifest.read_text(encoding='utf-8'))


def _render(source, directory):
    from PIL import Image, ImageOps
    pages = []

    def save(image):
        image.thumbnail((2400,2400))
        page = len(pages)+1
        image.save(directory/f'{page}.png')
        os.chmod(directory/f'{page}.png',0o600)
        pages.append({'width':image.width,'height':image.height})
        image.thumbnail((360,240))
        image.save(directory/f'{page}.jpg',quality=85)
        os.chmod(directory/f'{page}.jpg',0o600)

    if source.suffix.lower() == '.pdf':
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(source) as document:
            if not 1 <= len(document) <= 5:
                raise ValueError('Preview supports up to five pages')
            for index in range(len(document)):
                with closing(document[index]) as page:
                    scale = min(3,2400/max(page.get_size()))
                    with closing(page.render(scale=scale)) as bitmap:
                        with bitmap.to_pil().convert('RGB') as image:
                            save(image)
    else:
        with Image.open(source) as original:
            if original.width*original.height > 30000000:
                raise ValueError('Image is too large')
            with ImageOps.exif_transpose(original).convert('RGBA') as rgba, Image.new('RGB',rgba.size,'white') as image:
                image.paste(rgba,mask=rgba.getchannel('A'))
                save(image)
    manifest = directory/'manifest.json'
    manifest.write_text(json.dumps(pages),encoding='utf-8')
    os.chmod(manifest,0o600)


if __name__ == '__main__':
    _render(Path(sys.argv[1]),Path(sys.argv[2]))
