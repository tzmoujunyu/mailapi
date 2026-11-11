import unittest
from unittest.mock import patch

from mailapi_shared.codex_quota_schedule import CodexQuotaSchedule


class CodexQuotaScheduleTests(unittest.TestCase):
    def setUp(self):
        self.schedule = CodexQuotaSchedule()
        self.clock = self.enterContext(patch('mailapi_shared.codex_quota_schedule.time.monotonic', return_value=1000))
        self.enterContext(patch('mailapi_shared.codex_quota_schedule.time.time', return_value=2000))
        self.interval = self.enterContext(patch('mailapi_shared.codex_quota_schedule.random.randint', return_value=180))

    def test_automatic_accounts_draw_independent_intervals_between_three_and_eight_minutes(self):
        self.interval.side_effect = [180, 480, 250]
        first = self.schedule.begin('one')
        second = self.schedule.begin('two')
        self.schedule.finish('one', first)
        self.schedule.finish('two', second)
        self.clock.return_value = 1179
        self.assertIsNone(self.schedule.begin('one'))
        self.clock.return_value = 1180
        self.assertIsNotNone(self.schedule.begin('one'))
        self.assertIsNone(self.schedule.begin('two'))
        for call in self.interval.call_args_list:
            self.assertEqual(call.args, (180, 480))

    def test_manual_and_automatic_requests_share_the_minute_cooldown(self):
        first = self.schedule.begin('one')
        self.schedule.finish('one', first)
        self.clock.return_value = 1059.999
        self.assertIsNone(self.schedule.begin('one', manual=True))
        self.clock.return_value = 1060
        second = self.schedule.begin('one', manual=True)
        self.assertIsNotNone(second)
        self.schedule.finish('one', second)
        self.clock.return_value = 1239
        self.assertIsNone(self.schedule.begin('one'))
        self.clock.return_value = 1240
        self.assertIsNotNone(self.schedule.begin('one'))

    def test_recent_saved_checks_survive_a_process_restart_without_a_query_burst(self):
        self.assertIsNone(self.schedule.begin('one', manual=True, last_checked_at=1980))
        self.clock.return_value = 1039
        self.assertIsNone(self.schedule.begin('one', manual=True))
        self.clock.return_value = 1040
        self.assertIsNotNone(self.schedule.begin('one', manual=True))
        self.assertIsNone(self.schedule.begin('future-date', manual=True, last_checked_at=2100))

    def test_running_query_remains_single_flight_even_after_a_minute(self):
        first = self.schedule.begin('one', manual=True)
        self.clock.return_value = 2000
        self.assertIsNone(self.schedule.begin('one', manual=True))
        self.assertIsNone(self.schedule.begin('one'))
        self.schedule.finish('one', first)
        self.assertIsNotNone(self.schedule.begin('one'))

    def test_finishing_deleted_account_does_not_unlock_a_new_query_for_the_same_id(self):
        old = self.schedule.begin('one')
        self.schedule.discard('one')
        new = self.schedule.begin('one')
        self.schedule.finish('one', old)
        self.clock.return_value = 2000
        self.assertIsNone(self.schedule.begin('one', manual=True))
        self.schedule.finish('one', new)
        self.assertIsNotNone(self.schedule.begin('one', manual=True))
