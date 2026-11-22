"""Local extraction and confirmed identifiers for Chinese ordinary invoices.

Recognition is not tax verification. Native PDF/OCR work runs in a bounded child
process; only bundled local models are used, and invoice bytes never leave here.
"""
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import subprocess
import sys
import unicodedata


def confirmed_fields(value):
    if not isinstance(value, dict) or set(value) - {'number','code','date','total','check_code'}:
        raise ValueError('请核对并补全发票信息')
    if any(not isinstance(item, str) or len(item) > 100 for item in value.values()):
        raise ValueError('发票信息格式无效')
    result = {key:unicodedata.normalize('NFKC',value.get(key,'')).strip() for key in ('number','code','date','total','check_code')}
    for key in ('number','code','check_code'):
        result[key] = re.sub(r'\s+', '', result[key])
    if not re.fullmatch(r'(?:[0-9]{8}|[0-9]{20})',result['number']):
        raise ValueError('发票号码须为 8 位或 20 位数字')
    if len(result['number']) == 8 and not re.fullmatch(r'(?:[0-9]{10}|[0-9]{12})',result['code']):
        raise ValueError('8 位票号须填写 10 位或 12 位发票代码')
    if len(result['number']) == 20:
        result['code'] = ''
    if result['check_code'] and not re.fullmatch(r'(?:[0-9]{6}|[0-9]{20})',result['check_code']):
        raise ValueError('校验码须为末 6 位或完整 20 位数字')
    try:
        result['date'] = date.fromisoformat(result['date']).isoformat()
        total = Decimal(result['total'].replace(',',''))
        if not total.is_finite() or abs(total) > 10000000000 or total != total.quantize(Decimal('0.01')):
            raise ValueError()
    except (ValueError,InvalidOperation):
        raise ValueError('请填写有效开票日期和价税合计') from None
    result['total'] = format(total,'.2f')
    return result


def invoice_key(fields):
    return fields['number'] if len(fields['number']) == 20 else fields['code'] + ':' + fields['number']


def parse_text(text):
    compact = re.sub(r'\s+', '', unicodedata.normalize('NFKC',text))
    fields = {}
    numbers = list(dict.fromkeys(re.findall(r'(?:发票号码|发票号)[:：]?([0-9]{20}|[0-9]{8})(?![0-9])',compact)))
    if len(numbers) == 1:
        fields['number'] = numbers[0]
    code = re.search(r'发票代码[:：]?([0-9]{12}|[0-9]{10})(?![0-9])',compact)
    if code:
        fields['code'] = code[1]
    when = re.search(r'开票日期[:：]?([0-9]{4})[-年./]([0-9]{1,2})[-月./]([0-9]{1,2})日?',compact)
    if when:
        try:
            fields['date'] = date(*map(int,when.groups())).isoformat()
        except ValueError:
            pass
    amount = re.search(r'(?:小写|价税合计)[)）:：]*[¥￥]?(-?[0-9][0-9,]*\.[0-9]{2})(?![0-9])',compact)
    if amount:
        fields['total'] = amount[1].replace(',','')
    check = re.search(r'校验码[:：]?([0-9]{20}|[0-9]{6})(?![0-9])',compact)
    if check:
        fields['check_code'] = check[1]
    return fields, len(numbers) > 1


def _recognize(path):
    import pypdfium2 as pdfium
    from PIL import Image, ImageOps
    images, text, confidence = [], '', None
    try:
        if path.suffix.lower() == '.pdf':
            with pdfium.PdfDocument(path) as document:
                if len(document) > 5:
                    raise ValueError('PDF 超过 5 页，请手动核对完整发票信息')
                for index in range(len(document)):
                    with closing(document[index]) as page:
                        with closing(page.get_textpage()) as textpage:
                            text += textpage.get_text_range() + '\n'
                fields, multiple = parse_text(text)
                try:
                    confirmed_fields(fields)
                    return {'fields':fields,'method':'pdf_text','confidence':None,'multiple':multiple,'message':'请核对识别信息'}
                except ValueError:
                    pass
                for index in range(len(document)):
                    with closing(document[index]) as page:
                        scale = min(2.5,2400/max(page.get_size()))
                        with closing(page.render(scale=scale)) as bitmap:
                            images.append(bitmap.to_pil().convert('RGB').copy())
        else:
            with Image.open(path) as original:
                if original.width * original.height > 30000000:
                    raise ValueError('图片像素过大，请缩小后识别或手动填写')
                image = ImageOps.exif_transpose(original).convert('RGB')
                image.thumbnail((2400,2400))
                images.append(image)
        import rapidocr
        models = Path(rapidocr.__file__).parent / 'models'
        paths = {'Det.model_path':models/'PP-OCRv6_det_small.onnx','Cls.model_path':models/'ch_ppocr_mobile_v2.0_cls_mobile.onnx','Rec.model_path':models/'PP-OCRv6_rec_small.onnx'}
        if not all(value.is_file() for value in paths.values()):
            raise ValueError('本地识别模型缺失，请手动填写')
        engine = rapidocr.RapidOCR(params={**{key:str(value) for key,value in paths.items()},'Global.log_level':'critical',
            'EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':1})
        scores = []
        for image in images:
            output = engine(image)
            text += '\n' + '\n'.join(output.txts or ())
            scores.extend(output.scores or ())
        if scores:
            confidence = round(sum(scores)/len(scores),3)
        fields, multiple = parse_text(text)
        return {'fields':fields,'method':'ocr','confidence':confidence,'multiple':multiple,'message':'请核对识别信息'}
    finally:
        for image in images:
            image.close()


def recognize_file(path):
    """Isolate native parsers and terminate hung OCR without blocking the web loop."""
    try:
        result = subprocess.run([sys.executable,'-m','mailapi_shared.finance_invoices',str(path)],
            cwd=Path(__file__).resolve().parent.parent, capture_output=True, timeout=45, check=True)
        data = json.loads(result.stdout)
        if not isinstance(data,dict) or not isinstance(data.get('fields'),dict):
            raise ValueError()
        return data
    except (subprocess.SubprocessError,ValueError):
        return {'fields':{},'method':'manual','confidence':None,'multiple':False,'message':'识别未完成，请手动填写并核对发票信息'}


if __name__ == '__main__':
    try:
        output = _recognize(Path(sys.argv[1]))
    except Exception:
        output = {'fields':{},'method':'manual','confidence':None,'multiple':False,'message':'无法识别此文件，请手动填写并核对发票信息'}
    print(json.dumps(output,ensure_ascii=False))
