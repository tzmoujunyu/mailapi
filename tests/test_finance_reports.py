import csv
import io
from pathlib import Path
import unittest

from openpyxl import load_workbook

from tests import test_test_features_http as http_tests


class FinanceReportTests(unittest.TestCase):
    setUp = http_tests.FinanceFeatureHttpTests.setUp

    def item(self, *, owner='alice-id', name='模型订阅', amount=1250, group='A'):
        return self.store.create_finance_application(user_id=owner, username=self.site.users[owner]['username'],
            user_note='', groups=['A','B'], project=name, amount_cents=amount, files=[],
            expense_group_id=group, expense_group_name=group)

    def export(self, **changes):
        user = changes.pop('user', 'admin-id')
        return self.client.post('/api/finance/reports/export', headers={'x-test-user':user},
            json={'report':'applications','format':'csv','scope':'all','filters':{}, **changes})

    def test_reports_enforce_user_scope_and_export_all_matching_rows(self):
        for index in range(30):
            self.item(name=f'订阅{index}')
        self.item(owner='admin-id', name='管理员资料')
        response = self.export(user='alice-id', scope='mine')
        self.assertEqual(response.status_code, 200, response.text)
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 31)
        self.assertTrue(all(row[3] == 'alice' for row in rows[1:]))
        for options in ({'scope':'all'},{'scope':'mine','report':'history'},{'scope':'mine','report':'groups'}):
            self.assertEqual(self.export(user='alice-id', **options).status_code, 403)
        self.assertEqual(list((Path(self.directory)/'finance_exports').iterdir()), [])

    def test_group_totals_use_current_status_once_and_exclude_cancelled(self):
        first = self.item()
        second = self.item(amount=2000)
        cancelled = self.item(amount=9900)
        self.store.cancel_finance_application(cancelled['id'],'alice-id')
        self.store.review_finance_application(first['id'],'approved','admin-id','admin','',expected_version=1)
        self.store.review_finance_application(second['id'],'approved','admin-id','admin','',expected_version=1)
        event = self.store.finance_history(second['id'])[0]
        self.store.re_review_finance_application(second['id'],'rejected','admin-id','admin','误操作',expected_version=1,event_id=event['id'])
        response = self.export(report='groups')
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][2:4], ['2','32.5'])
        self.assertEqual(rows[1][6:10], ['1','12.5','1','20'])
        response = self.export(report='history')
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(row[7] == '是' for row in rows[1:]), 2)

    def test_excel_uses_literal_text_and_numeric_money_and_csv_defuses_formulas(self):
        self.item(name='=1+1')
        response = self.export(format='xlsx')
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(io.BytesIO(response.content))
        self.addCleanup(workbook.close)
        sheet = workbook.active
        self.assertEqual(sheet['H2'].value, '=1+1')
        self.assertEqual(sheet['H2'].data_type, 's')
        self.assertEqual(sheet['I2'].value, 12.5)
        self.assertEqual(sheet.freeze_panes, 'A2')
        self.assertEqual(sheet.auto_filter.ref, 'A1:T2')
        rows = list(csv.reader(io.StringIO(self.export().content.decode('utf-8-sig'))))
        self.assertEqual(rows[1][7], "'=1+1")
        self.assertEqual(list((Path(self.directory)/'finance_exports').iterdir()), [])

    def test_filters_respect_timezone_date_keywords_and_amount(self):
        item = self.item()
        with self.store._connect() as db:
            db.execute('UPDATE finance_applications SET created_at=? WHERE id=?',('2026-11-01T01:00:00Z',item['id']))
        filters = {'from':'2026-10-31','to':'2026-10-31','timezone':'America/Los_Angeles','q':'模型','min':'12.49','max':'12.51'}
        self.assertEqual(self.export(filters=filters).headers['X-Export-Count'], '1')
        self.assertEqual(self.export(filters={**filters,'timezone':'Asia/Shanghai'}).headers['X-Export-Count'], '0')
        for invalid in ({'min':'NaN'}, {'from':'2026-11-02','to':'2026-11-01'}, {'timezone':'../etc/passwd'}, {'user_id':'alice-id'}, {'min':'2','max':'1'}):
            self.assertEqual(self.export(filters=invalid).status_code, 400)


if __name__ == '__main__':
    unittest.main()
