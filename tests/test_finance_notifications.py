from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from mailapi_shared.finance_notifications import notification_page
from tests import test_test_features_http as http_tests


class FinanceNotificationTests(unittest.TestCase):
    def setUp(self):
        http_tests.FinanceFeatureHttpTests.setUp(self)
        self.site.users['manager-id']={'id':'manager-id','username':'manager','role':'manager','note':''}
        self.site.users['bob-id']={'id':'bob-id','username':'bob','role':'user','note':''}

    def create(self):
        return self.store.create_finance_application(user_id='alice-id',username='alice',user_note='',groups=[],
            project='测试通知',amount_cents=1250,files=[])

    def feed(self, actor, **params):
        return self.client.get('/api/finance/notifications',headers={'x-test-user':actor},params=params).json()

    def read(self, actor, body):
        return self.client.post('/api/finance/notifications/read',headers={'x-test-user':actor},json=body)

    def test_audiences_and_read_receipts_are_separate_for_each_account(self):
        item=self.create()
        self.assertEqual(self.feed('alice-id')['total'],0)
        self.assertEqual(self.feed('admin-id')['unread_count'],1)
        self.assertEqual(self.feed('manager-id')['unread_count'],1)
        admin=self.feed('admin-id')
        self.read('admin-id',{'through_id':admin['max_id']})
        self.assertEqual(self.feed('admin-id')['unread_count'],0)
        self.assertEqual(self.feed('manager-id')['unread_count'],1)
        self.store.review_finance_application(item['id'],'rejected','admin-id','admin','请补充材料',expected_version=1)
        owner=self.feed('alice-id')
        self.assertEqual(owner['total'],1)
        self.assertEqual(owner['items'][0]['payload']['note'],'请补充材料')
        self.assertEqual(self.feed('bob-id')['total'],0)
        self.read('bob-id',{'ids':[owner['items'][0]['id']]})
        self.assertEqual(self.feed('alice-id')['unread_count'],1)
        self.read('alice-id',{'ids':[owner['items'][0]['id']]})
        self.assertEqual(self.feed('alice-id')['unread_count'],0)
        self.site.users['manager-id']['role']='user'
        self.assertEqual(self.feed('manager-id')['total'],0)

    def test_read_all_does_not_consume_notifications_created_after_the_displayed_cursor(self):
        self.create();first=self.feed('admin-id')
        self.create();self.read('admin-id',{'through_id':first['max_id']})
        self.assertEqual(self.feed('admin-id')['unread_count'],1)
        for payload in ({'ids':[True]},{'ids':['1']},{'ids':[]},{'ids':[1],'through_id':2},{'through_id':2**64}):
            self.assertEqual(self.read('admin-id',payload).status_code,400)

    def test_seven_day_reminders_are_not_repeated_within_twenty_four_hours_and_stop_after_review(self):
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        with patch('mailapi_shared.test_features._now',return_value=start.isoformat().replace('+00:00','Z')):
            item=self.create()
        admin=self.site.users['admin-id']
        def reminders(now):
            return [entry for entry in notification_page(self.store,admin,now=now)['items'] if entry['kind']=='overdue']
        self.assertEqual(reminders(start+timedelta(days=7,seconds=-1)),[])
        first=reminders(start+timedelta(days=7))
        self.assertEqual(len(first),1)
        same=reminders(start+timedelta(days=7,hours=23))
        self.assertEqual(same[0]['id'],first[0]['id'])
        next_day=reminders(start+timedelta(days=8))
        self.assertEqual(len(next_day),1)
        self.assertNotEqual(next_day[0]['id'],first[0]['id'])
        self.store.review_finance_application(item['id'],'approved','admin-id','admin','',expected_version=1)
        self.assertEqual(reminders(start+timedelta(days=9)),[])

    def test_resubmission_starts_a_new_waiting_period_and_old_results_remain_historical(self):
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        with patch('mailapi_shared.test_features._now',return_value=start.isoformat().replace('+00:00','Z')):
            item=self.create()
        notification_page(self.store,self.site.users['admin-id'],now=start+timedelta(days=8))
        self.store.review_finance_application(item['id'],'rejected','admin-id','admin','补充',expected_version=1)
        later=start+timedelta(days=10)
        with patch('mailapi_shared.test_features._now',return_value=later.isoformat().replace('+00:00','Z')):
            self.store.update_finance_application(item['id'],'alice-id','再次提交',1250,{},expected_version=1)
        feed=notification_page(self.store,self.site.users['admin-id'],now=later+timedelta(days=1))
        self.assertFalse(any(entry['kind']=='overdue' for entry in feed['items']))
        owner=self.feed('alice-id')['items'][0]
        self.assertEqual(owner['payload']['result'],'rejected')
        self.assertEqual(owner['current_status'],'pending')
        self.assertEqual(owner['current_version'],2)

    def test_review_notifications_are_paginated_over_the_full_owned_history(self):
        for _ in range(31):
            item=self.create()
            self.store.review_finance_application(item['id'],'approved','admin-id','admin','',expected_version=1)
        first=self.feed('alice-id');second=self.feed('alice-id',page=2)
        self.assertEqual(first['total'],31)
        self.assertTrue(first['has_more'])
        self.assertEqual(len(first['items']),20)
        self.assertEqual(len(second['items']),11)
        self.assertFalse({item['id'] for item in first['items']} & {item['id'] for item in second['items']})


if __name__=='__main__':
    unittest.main()
