import hashlib
import io
from pathlib import Path
import secrets
import subprocess
import unittest
from unittest.mock import patch

from PIL import Image

from tests.test_finance_invoices import FIELDS
from tests import test_test_features_http as http_tests


class FinancePreviewTests(unittest.TestCase):
    setUp = http_tests.FinanceFeatureHttpTests.setUp

    def image(self, color='red', pdf=False, pages=1):
        with Image.new('RGB',(480,200),color) as image:
            output=io.BytesIO()
            image.save(output,format='PDF' if pdf else 'PNG',save_all=pdf,append_images=[image]*(pages-1) if pdf else [])
            return output.getvalue()

    def file(self, content, pdf=False):
        key=secrets.token_hex(12);name=key+('.pdf' if pdf else '.png')
        (self.store.files/name).write_bytes(content)
        return {'id':key,'kind':'invoice','stored_name':name,'original_name':'测试发票'+Path(name).suffix,
                'content_type':'application/pdf' if pdf else 'image/png','size':len(content),'sha256':hashlib.sha256(content).hexdigest()}

    def application(self, content=None, pdf=False):
        return self.store.create_finance_application(user_id='alice-id',username='alice',user_note='',groups=[],
            project='预览测试',amount_cents=1250,files=[self.file(content or self.image(),pdf)],invoice_fields=FIELDS)

    def get(self, app_id, query='', user='alice-id'):
        return self.client.get(f'/api/finance/applications/{app_id}/invoice-preview'+query,headers={'x-test-user':user})

    def test_thumbnails_are_cached_private_and_do_not_run_ocr(self):
        item=self.application()
        with patch('mailapi_shared.finance_previews.subprocess.run',wraps=subprocess.run) as renderer, \
                patch('mailapi_shared.finance_invoices.recognize_file',side_effect=AssertionError('Preview must not run OCR')):
            response=self.get(item['id'])
            self.assertEqual(response.status_code,200,response.text)
            page=response.json()['pages'][0]
            self.assertEqual((page['width'],page['height']),(480,200))
            thumb=self.client.get(page['thumbnail'],headers={'x-test-user':'alice-id'})
            full=self.client.get(page['src'],headers={'x-test-user':'admin-id'})
            self.assertEqual(renderer.call_count,1)
        self.assertEqual(thumb.headers['content-type'],'image/jpeg')
        self.assertEqual(full.headers['content-type'],'image/png')
        self.assertIn('no-store',full.headers['cache-control'])
        self.assertEqual(full.headers['x-content-type-options'],'nosniff')
        with Image.open(io.BytesIO(thumb.content)) as image:
            self.assertLessEqual(image.width,360)
        for path in (Path(self.directory)/'finance_previews').rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777,0o700 if path.is_dir() else 0o600)

    def test_cached_images_are_still_subject_to_account_permission_checks(self):
        item=self.application();self.assertEqual(self.get(item['id']).status_code,200)
        self.site.users['bob-id']={'id':'bob-id','username':'bob','role':'user','note':''}
        self.assertEqual(self.get(item['id'],user='bob-id').status_code,404)
        self.assertEqual(self.get(item['id'],'?page=1',user='bob-id').status_code,404)
        self.assertEqual(self.get(item['id'],'?page=2').status_code,404)
        self.assertEqual(self.get(item['id'],'?page=0').status_code,422)

    def test_pdf_pages_have_thumbnails_and_full_images(self):
        item=self.application(self.image(pdf=True,pages=2),pdf=True)
        response=self.get(item['id'])
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(len(response.json()['pages']),2)
        for page in response.json()['pages']:
            self.assertEqual(self.client.get(page['src']).status_code,200)
            self.assertEqual(self.client.get(page['thumbnail']).status_code,200)

    def test_replaced_and_historical_invoices_preview_the_correct_file(self):
        item=self.application();app_id=item['id'];event=self.store.finance_history(app_id)[0]
        self.store.update_finance_application(app_id,'alice-id','换票',1250,{'invoice':self.file(self.image('blue'))},
            expected_version=1,invoice_fields={**FIELDS,'number':'20261122000000000002'})
        old=self.get(app_id,'?event_id='+event['id']).json()['pages'][0]
        current=self.get(app_id).json()['pages'][0]
        self.assertIn('event_id='+event['id'],old['src'])
        for page,color in [(old,(255,0,0)),(current,(0,0,255))]:
            response=self.client.get(page['src'],headers={'x-test-user':'alice-id'})
            with Image.open(io.BytesIO(response.content)) as image:
                self.assertEqual(image.getpixel((0,0)),color)
        self.assertEqual(self.get(app_id,'?event_id=missing').status_code,404)

    def test_generated_preview_links_remain_bound_to_the_displayed_file_after_replacement(self):
        item=self.application();page=self.get(item['id']).json()['pages'][0]
        self.store.update_finance_application(item['id'],'alice-id','换票',1250,{'invoice':self.file(self.image('blue'))},
            expected_version=1,invoice_fields={**FIELDS,'number':'20261122000000000002'})
        response=self.client.get(page['src'],headers={'x-test-user':'alice-id'})
        with Image.open(io.BytesIO(response.content)) as image:
            self.assertEqual(image.getpixel((0,0)),(255,0,0))

    def test_invalid_oversized_pdf_and_missing_files_offer_a_safe_download_fallback(self):
        item=self.application(b'invalid PDF',pdf=True)
        response=self.get(item['id']);self.assertEqual(response.status_code,422)
        self.assertNotIn(self.directory,response.text)
        self.assertEqual(list((Path(self.directory)/'finance_previews').glob('.render-*')),[])
        too_many=self.file(self.image(pdf=True,pages=6),pdf=True)
        self.store.update_finance_application(item['id'],'alice-id','多页',1250,{'invoice':too_many},expected_version=1,invoice_fields=FIELDS)
        self.assertEqual(self.get(item['id']).status_code,422)
        (self.store.files/too_many['stored_name']).unlink()
        self.assertEqual(self.get(item['id']).status_code,410)


if __name__=='__main__':
    unittest.main()
