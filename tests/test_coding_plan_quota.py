import io
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from urllib.error import HTTPError

from mailapi_shared import coding_plan_quota as quota


class CodingPlanQuotaTests(unittest.TestCase):
    def query(self, provider, payload):
        with patch.object(quota, "build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(payload).encode())
            windows = quota.fetch_coding_plan_quota(provider, "test-api-key")
        return windows, opener.return_value.open.call_args.args[0]

    def test_zhipu_uses_coding_key_and_reports_model_and_tool_windows(self):
        windows, request = self.query("zhipu", {"data": {"limits": [
            {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "percentage": 35, "nextResetTime": 1794009600000},
            {"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": 100},
            {"type": "TIME_LIMIT", "unit": 5, "number": 1, "percentage": 0},
            {"type": "CREDIT_LIMIT", "unit": 3, "number": 5},
        ]}})
        self.assertEqual(request.full_url, "https://open.bigmodel.cn/api/monitor/usage/quota/limit")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "test-api-key")
        self.assertEqual([(item["label"], item["remaining_percent"]) for item in windows], [
            ("5 小时", 65), ("每周", 0), ("每月 MCP", 100),
        ])
        self.assertEqual(windows[0]["reset_at"], "2026-11-07T00:00:00+00:00")

    def test_kimi_prefers_legacy_counters_when_both_shapes_are_present(self):
        windows, request = self.query("kimi", {
            "usage": {"limit": "100", "used": "85"},
            "limits": [{
                "window": {"timeUnit": "TIME_UNIT_MINUTE", "duration": 300},
                "detail": {"limit": "100", "remaining": "42", "resetTime": "2026-11-06T03:00:00.717479433Z"},
            }],
            "usages": {"limit_7d": {"used_ratio": 0}, "limit_5h": {"used_ratio": 0}},
        })
        self.assertEqual(request.full_url, "https://api.kimi.com/coding/v1/usages")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-api-key")
        self.assertEqual(request.get_header("User-agent"), "MailAPI")
        self.assertEqual([item["remaining_percent"] for item in windows], [42, 15])
        self.assertEqual(windows[0]["reset_at"], "2026-11-06T03:00:00.717479+00:00")

    def test_kimi_monthly_windows_come_only_from_reported_ratios(self):
        windows, _ = self.query("kimi", {"code": 0, "data": {"kind": "ok", "quota": {"usages": {
            "limit5h": {"usedRatio": 0},
            "monthTotal": {"usedRatio": 0.12345, "resetAt": "2026-12-01T00:00:00Z"},
            "monthCode": {"usedRatio": "NaN"},
        }}}})
        self.assertEqual([(item["label"], item["remaining_percent"]) for item in windows], [
            ("5 小时", 100), ("每月总额度", 87.7),
        ])

    def test_invalid_provider_and_upstream_errors_do_not_expose_secrets(self):
        with patch.object(quota, "build_opener") as opener:
            with self.assertRaises(quota.CodingPlanQuotaError):
                quota.fetch_coding_plan_quota("https://unexpected.example", "test-api-key")
            opener.assert_not_called()
            opener.return_value.open.side_effect = HTTPError(
                quota.CODING_PLAN_ENDPOINTS["kimi"], 403, "private-upstream-detail", {}, None
            )
            with self.assertRaisesRegex(quota.CodingPlanQuotaError, "平台拒绝查询") as error:
                quota.fetch_coding_plan_quota("kimi", "test-api-key")
            self.assertNotIn("private-upstream-detail", str(error.exception))
        with self.assertRaisesRegex(quota.CodingPlanQuotaError, "平台未返回可显示的额度"):
            self.query("kimi", {"usages": {"limit_5h": {"reset_time": "2026-11-06T00:00:00Z"}}})

    def test_cache_throttles_queries_and_keeps_last_success_on_failure(self):
        cache = quota.CodingPlanQuotaCache()
        with patch.object(quota, "fetch_coding_plan_quota", return_value=[{"remaining_percent": 25}]) as fetch:
            result = cache.refresh("plan", "kimi", "old-key")
            self.assertEqual(cache.refresh("plan", "kimi", "old-key"), result)
            fetch.assert_called_once()
            self.assertIsNone(cache.snapshot("plan", "kimi", "new-key"))

        with patch.object(quota, "QUERY_COOLDOWN", 0), patch.object(
            quota, "fetch_coding_plan_quota", side_effect=quota.CodingPlanQuotaError("平台额度接口暂不可用")
        ):
            failed = cache.refresh("plan", "kimi", "old-key")
        self.assertEqual(failed["windows"], result["windows"])
        self.assertEqual(failed["updated_at"], result["updated_at"])
        self.assertIn("暂不可用", failed["error"])

    def test_concurrent_refreshes_share_one_request(self):
        cache = quota.CodingPlanQuotaCache()
        started, complete = threading.Event(), threading.Event()

        def fetch(provider, api_key):
            started.set()
            self.assertTrue(complete.wait(timeout=3))
            return [{"remaining_percent": 25}]

        with patch.object(quota, "fetch_coding_plan_quota", side_effect=fetch) as request, ThreadPoolExecutor() as pool:
            running = pool.submit(cache.refresh, "plan", "kimi", "test-key")
            try:
                self.assertTrue(started.wait(timeout=2))
                self.assertTrue(cache.refresh("plan", "kimi", "test-key")["pending"])
            finally:
                complete.set()
            self.assertEqual(running.result(timeout=3)["windows"][0]["remaining_percent"], 25)
            request.assert_called_once()
