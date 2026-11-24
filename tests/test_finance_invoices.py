from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mailapi_shared.finance_invoices import _recognize, confirmed_fields, invoice_key, parse_text
from mailapi_shared.test_features import TestFeatureError, TestFeatureStore
from tests import test_test_features_http as http_tests


FIELDS = {'number':'20261122000000000001','code':'','date':'2026-11-22','total':'123.45','check_code':''}


class InvoiceFieldTests(unittest.TestCase):
    def test_scanned_pdf_is_rendered_and_passed_to_local_ocr(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'scanned.pdf'
            with Image.new('RGB',(800,400),'white') as image:
                image.save(path)
            output=SimpleNamespace(txts=['发票号码：20261122000000000001','开票日期：2026年11月22日','价税合计（小写）：￥123.45'],scores=[.99,.99,.99])
            with patch('rapidocr.RapidOCR') as engine:
                engine.return_value.return_value=output
                result=_recognize(path)
            self.assertEqual(confirmed_fields(result['fields']),FIELDS)
            self.assertEqual(result['method'],'ocr')
            self.assertEqual(engine.return_value.call_count,1)

    def test_ordinary_and_digital_invoice_identifiers_preserve_leading_zeros(self):
        fields, multiple = parse_text('增值税电子普通发票\n发 票 代 码：０１２００２６００１１１\n发票号码：００００１２３４\n开票日期：2026年11月22日\n价税合计（小写）：￥123.45\n校验码：123456')
        fields = confirmed_fields(fields)
        self.assertEqual(invoice_key(fields),'012002600111:00001234')
        self.assertFalse(multiple)
        fields, multiple = parse_text('电子发票（普通发票） 发票号码：20261122000000000001 开票日期：2026-11-22 价税合计(小写)¥1,234.50')
        self.assertEqual(confirmed_fields(fields)['total'],'1234.50')
        self.assertFalse(multiple)
        self.assertTrue(parse_text('发票号码：20261122000000000001 发票号码：20261122000000000002')[1])
        for changes in ({'number':'1'},{'number':'12345678','code':''},{'date':'2026-02-30'},{'total':'NaN'},{'total':'1.001'},{'check_code':'123'}):
            with self.assertRaises(ValueError):
                confirmed_fields({**FIELDS,**changes})


class FinanceInvoiceHttpTests(unittest.TestCase):
    def setUp(self):
        http_tests.FinanceFeatureHttpTests.setUp(self)
        self.recognizer=self.enterContext(patch('mailapi_shared.finance_invoices.recognize_file',return_value={'fields':FIELDS,'method':'ocr','confidence':0.99,'multiple':False,'message':'请核对识别信息'}))

    def create(self, *, number=FIELDS['number'], content=b'invoice-one', user='alice-id'):
        return self.client.post('/api/finance/applications',headers={'x-test-user':user},
            data={'project':'测试报销','amount':'12.50','invoice_fields':json.dumps({**FIELDS,'number':number})},
            files={'invoice':('测试发票.pdf',io.BytesIO(content),'application/pdf')})

    def analyze(self, content=b'invoice-one', user='alice-id', content_type='application/pdf'):
        return self.client.post('/api/finance/invoices/analyze',headers={'x-test-user':user},
            files={'invoice':('测试发票.pdf',io.BytesIO(content),content_type)})

    def test_delegated_submission_reuses_uploading_admin_recognition_and_keeps_duplicate_and_payment_rules(self):
        content=b'admin-uploaded-invoice'
        self.assertEqual(self.analyze(content,user='admin-id').status_code,200)
        created=self.client.post('/api/finance/applications',
            data={'payer_user_id':'alice-id','project':'代提交','invoice_fields':json.dumps(FIELDS)},
            files={'invoice':('发票.pdf',content,'application/pdf')})
        self.assertEqual(created.status_code,201,created.text)
        self.assertEqual(self.recognizer.call_count,1)
        duplicate=self.client.post('/api/finance/applications',
            data={'project':'重复票据','invoice_fields':json.dumps(FIELDS)},
            files={'invoice':('发票.pdf',content,'application/pdf')})
        self.assertEqual(duplicate.status_code,409,duplicate.text)
        large={**FIELDS,'number':'20261122000000000099','total':'1000.00'}
        missing_payment=self.client.post('/api/finance/applications',
            data={'payer_user_id':'alice-id','project':'满千代提交','invoice_fields':json.dumps(large)},
            files={'invoice':('发票.pdf',b'another-admin-invoice','application/pdf')})
        self.assertEqual(missing_payment.status_code,400,missing_payment.text)
        self.assertIn('支付记录',missing_payment.json()['detail'])

    def test_invoice_total_is_the_only_amount_and_payment_is_required_at_1000(self):
        for index, total in enumerate(('999.99', '1000.00', '1000.01')):
            fields = {**FIELDS, 'total': total, 'number': f'202611220000000000{index+10:02d}'}
            data = {'project': '票面金额', 'invoice_fields': json.dumps(fields), 'amount': '1.00'}
            upload = {'invoice': ('发票.pdf', f'invoice-{index}'.encode(), 'application/pdf')}
            before = set(self.store.files.iterdir())
            response = self.client.post('/api/finance/applications', headers={'x-test-user': 'alice-id'}, data=data, files=upload)
            if total == '999.99':
                self.assertEqual(response.status_code, 201, response.text)
            else:
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn('必须上传支付记录', response.json()['detail'])
                self.assertEqual(set(self.store.files.iterdir()), before)
                data.pop('amount')
                upload['payment_record'] = ('支付记录.png', b'payment-image', 'image/png')
                response = self.client.post('/api/finance/applications', headers={'x-test-user': 'alice-id'}, data=data, files=upload)
                self.assertEqual(response.status_code, 201, response.text)
            item = response.json()['item']
            self.assertEqual(item['amount'], total)
            self.assertEqual(item['amount_cents'], int(total.replace('.', '')))

    def test_payment_requirement_on_edit_and_resubmission_preserves_history_and_files(self):
        item = self.create().json()['item']
        url = f"/api/finance/applications/{item['id']}"
        fields = {**FIELDS, 'total': '1000.00'}
        data = {'project': '补充支付记录', 'version': '1', 'invoice_fields': json.dumps(fields)}
        before = set(self.store.files.iterdir())
        response = self.client.put(url, headers={'x-test-user': 'alice-id'}, data=data)
        self.assertEqual(response.status_code, 400, response.text)
        unchanged = self.store.finance_application(item['id'])
        self.assertEqual(unchanged['amount'], FIELDS['total'])
        self.assertEqual(unchanged['version'], 1)
        self.assertEqual(len(self.store.finance_history(item['id'])), 1)
        self.assertEqual(set(self.store.files.iterdir()), before)
        files = {'payment_record': ('支付记录.png', b'payment-image', 'image/png')}
        response = self.client.put(url, headers={'x-test-user': 'alice-id'}, data=data, files=files)
        self.assertEqual(response.status_code, 200, response.text)
        payment = next(file for file in response.json()['item']['files'] if file['kind'] == 'payment')
        self.client.post(f"/api/admin/finance/applications/{item['id']}/review", json={'status':'rejected','note':'补充说明','version':2})
        data['version'] = '2'
        response = self.client.put(url, headers={'x-test-user': 'alice-id'}, data=data)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['item']['status'], 'pending')
        self.assertEqual(next(file for file in response.json()['item']['files'] if file['kind'] == 'payment')['id'], payment['id'])
        history = self.store.finance_history(item['id'])
        self.assertEqual(history[-1]['snapshot']['amount'], FIELDS['total'])
        stored = self.store.finance_application(item['id'])
        (self.store.files / next(file for file in stored['files'] if file['kind'] == 'payment')['stored_name']).unlink()
        data['version'] = '3'
        response = self.client.put(url, headers={'x-test-user': 'alice-id'}, data=data)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.store.finance_application(item['id'])['version'], 3)

    def test_empty_payment_upload_does_not_satisfy_the_requirement_or_leave_orphans(self):
        data = {'project': '空支付记录', 'invoice_fields': json.dumps({**FIELDS, 'total': '1000.00'})}
        response = self.client.post('/api/finance/applications', data=data, files={
            'invoice': ('发票.pdf', b'invoice', 'application/pdf'),
            'payment_record': ('支付记录.png', b'', 'image/png')})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('不能为空', response.json()['detail'])
        self.assertEqual(list(self.store.files.iterdir()), [])
        self.assertEqual(self.store.list_finance_applications('admin-id'), [])

    def test_submission_reuses_the_uploaded_file_recognition_but_keeps_manual_corrections(self):
        response=self.analyze()
        self.assertEqual(response.status_code,200,response.text)
        response=self.create(number='20261122000000000002')
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(self.recognizer.call_count,1)
        self.assertEqual(response.json()['item']['invoice']['fields']['number'],'20261122000000000002')
        self.assertEqual(response.json()['item']['invoice']['recognition']['fields'],FIELDS)

    def test_cached_recognition_does_not_cache_the_duplicate_reimbursement_decision(self):
        self.assertFalse(self.analyze().json()['duplicate'])
        self.assertEqual(self.create(user='admin-id',content=b'another encoding').status_code,201)
        before=list(self.store.files.iterdir())
        response=self.create()
        self.assertEqual(response.status_code,409,response.text)
        self.assertNotIn('admin',response.text)
        self.assertEqual(self.recognizer.call_count,2)
        self.assertEqual(list(self.store.files.iterdir()),before)

    def test_replacing_an_invoice_also_reuses_its_upload_recognition(self):
        item=self.create().json()['item']
        self.analyze(b'replacement')
        response=self.client.put(f"/api/finance/applications/{item['id']}",headers={'x-test-user':'alice-id'},
            data={'project':'更换发票','amount':'12.50','version':'1','invoice_fields':json.dumps({**FIELDS,'number':'20261122000000000002'})},
            files={'invoice':('新发票.pdf',io.BytesIO(b'replacement'),'application/pdf')})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.recognizer.call_count,2)
        self.assertEqual(response.json()['item']['invoice']['fields']['number'],'20261122000000000002')

    def test_simultaneous_uploads_of_the_same_invoice_share_completed_recognition(self):
        import time
        output=self.recognizer.return_value
        def recognize(path):
            time.sleep(.05)
            return output
        self.recognizer.side_effect=recognize
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda _:self.analyze(),range(2)))
        self.assertEqual([response.status_code for response in responses],[200,200])
        self.assertEqual(self.recognizer.call_count,1)

    def test_recognition_cache_is_bound_to_account_file_bytes_and_parser_type(self):
        for user,content,content_type in [('alice-id',b'invoice-one','application/pdf'),
                ('admin-id',b'invoice-one','application/pdf'),('alice-id',b'changed','application/pdf'),
                ('alice-id',b'invoice-one','image/png')]:
            response=self.analyze(content,user,content_type)
            self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.recognizer.call_count,4)
        self.assertEqual(self.create().status_code,201)
        self.assertEqual(self.recognizer.call_count,4)

    def test_recognition_cache_expires_and_has_a_bounded_number_of_results(self):
        with patch('mailapi_shared.test_features.time.monotonic',return_value=100):
            self.analyze()
        with patch('mailapi_shared.test_features.time.monotonic',return_value=1901):
            self.assertEqual(self.create().status_code,201)
        self.assertEqual(self.recognizer.call_count,2)
        for index in range(65):
            self.analyze(f'cache-{index}'.encode())
        before=self.recognizer.call_count
        self.analyze(b'cache-0')
        self.assertEqual(self.recognizer.call_count,before+1)

    def test_failed_recognition_can_be_retried_instead_of_caching_a_timeout(self):
        self.recognizer.return_value={'fields':{},'method':'manual','multiple':False,'message':'识别未完成'}
        self.analyze()
        self.recognizer.return_value={'fields':FIELDS,'method':'ocr','multiple':False,'message':'请核对识别信息'}
        self.analyze()
        self.assertEqual(self.create().status_code,201)
        self.assertEqual(self.recognizer.call_count,2)

    def test_duplicate_numbers_and_files_are_blocked_across_users_without_leaking_identity(self):
        first = self.create().json()['item']
        second = self.create(content=b'different encoding',user='admin-id')
        self.assertEqual(second.status_code,409)
        self.assertNotIn('alice',second.text)
        self.assertEqual(self.create(number='20261122000000000002',user='admin-id').status_code,409)
        self.assertEqual(self.client.post(f"/api/admin/finance/applications/{first['id']}/review",json={'status':'rejected','note':'补充材料','version':1}).status_code,200)
        self.assertEqual(self.create(content=b'different encoding').status_code,201)
        changed = self.client.put(f"/api/finance/applications/{first['id']}",headers={'x-test-user':'alice-id'},data={'project':'重新报销','amount':'12.50','version':'1'})
        self.assertEqual(changed.status_code,409)
        rejected = next(event for event in self.store.finance_history(first['id']) if event['action']=='rejected')
        restore = self.client.post(f"/api/admin/finance/applications/{first['id']}/re-review",json={'status':'approved','note':'恢复','version':1,'event_id':rejected['id']})
        self.assertEqual(restore.status_code,409)

    def test_cancellation_releases_invoice_and_analysis_is_private_and_temporary(self):
        item = self.create().json()['item']
        url=f"/api/finance/applications/{item['id']}"
        self.assertEqual(self.client.get(url+'/invoice',headers={'x-test-user':'admin-id'}).status_code,200)
        before=list(self.store.files.iterdir())
        analysis=self.client.post('/api/finance/invoices/analyze',headers={'x-test-user':'admin-id'},files={'invoice':('副本.pdf',io.BytesIO(b'invoice-one'),'application/pdf')})
        self.assertTrue(analysis.json()['duplicate'])
        self.assertEqual(list(self.store.files.iterdir()),before)
        self.client.post(url+'/cancel',headers={'x-test-user':'alice-id'})
        self.assertEqual(self.create(user='admin-id').status_code,201)
        self.assertEqual(self.client.get(url+'/invoice',headers={'x-test-user':'alice-id'}).status_code,200)
        other=self.store.list_finance_applications('admin-id')[0]
        self.assertEqual(self.client.get(f"/api/finance/applications/{other['id']}/invoice",headers={'x-test-user':'alice-id'}).status_code,404)

    def test_manual_verification_is_audited_versioned_and_separate_from_approval(self):
        item=self.create().json()['item'];url=f"/api/admin/finance/applications/{item['id']}/invoice/verify"
        payload={'status':'verified','note':'测试平台结果登记','version':1,'revision':0}
        self.assertEqual(self.client.post(url,headers={'x-test-user':'alice-id'},json=payload).status_code,403)
        response=self.client.post(url,json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['item']['status'],'pending')
        self.assertEqual(self.client.post(url,json=payload).status_code,409)
        self.client.post(f"/api/admin/finance/applications/{item['id']}/review",json={'status':'approved','version':1})
        self.assertTrue(self.store.finance_history(item['id'])[0]['is_current_review'])
        self.assertEqual(len(self.store.invoice_verifications(item['id'])),1)
        self.assertEqual(self.store.invoice_verifications(item['id'])[0]['actor_name'],'admin')

    def test_replaced_invoice_resets_verification_but_keeps_original_check_and_file_version(self):
        item=self.create().json()['item'];app_id=item['id']
        self.client.post(f'/api/admin/finance/applications/{app_id}/invoice/verify',json={'status':'verified','note':'测试登记','version':1,'revision':0})
        changed=self.client.put(f'/api/finance/applications/{app_id}',headers={'x-test-user':'alice-id'},
            data={'project':'更换发票','amount':'20.00','version':'1','invoice_fields':json.dumps({**FIELDS,'number':'20261122000000000002'})},
            files={'invoice':('新发票.pdf',io.BytesIO(b'invoice-two'),'application/pdf')})
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(changed.json()['item']['invoice']['verification']['status'],'unverified')
        checks=self.store.invoice_verifications(app_id)
        self.assertEqual(checks[0]['fields']['number'],FIELDS['number'])
        self.assertNotEqual(checks[0]['file_id'],changed.json()['item']['invoice']['file_id'])

    def test_two_database_connections_cannot_claim_the_same_invoice_concurrently(self):
        other=TestFeatureStore(self.directory,None)
        def create(pair):
            index,store=pair
            file={'id':str(index),'kind':'invoice','stored_name':f'{index}.pdf','original_name':'测试.pdf','content_type':'application/pdf','size':1,'sha256':hashlib.sha256(str(index).encode()).hexdigest()}
            try:
                store.create_finance_application(user_id='alice-id',username='alice',user_note='',groups=[],project='并发测试',amount_cents=100,files=[file],invoice_fields=FIELDS)
                return 201
            except TestFeatureError as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(create,[(1,self.store),(2,other)]))
        self.assertEqual(sorted(statuses),[201,409])

    def test_admin_can_backfill_legacy_invoice_without_rewriting_financial_history(self):
        item=self.create().json()['item'];app_id=item['id']
        with self.store._connect() as db:
            db.execute('DELETE FROM finance_invoice_details WHERE application_id=?',(app_id,))
        before=self.store.finance_history(app_id)
        payload={'status':'unavailable','note':'历史测试样张，未做税务查验','version':1,'revision':0,'fields':FIELDS}
        response=self.client.post(f'/api/admin/finance/applications/{app_id}/invoice/verify',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['item']['invoice']['fields'],FIELDS)
        self.assertEqual(self.store.finance_history(app_id),before)
        checks=self.store.invoice_verifications(app_id)
        self.assertEqual(len(checks),1)
        self.assertEqual(checks[0]['fields'],FIELDS)
        self.assertEqual(checks[0]['status'],'unavailable')


if __name__=='__main__':
    unittest.main()
