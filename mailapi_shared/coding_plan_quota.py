"""Read and normalize subscription quotas for the supported Coding Plans."""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


CODING_PLAN_ENDPOINTS = {
    "zhipu": "https://open.bigmodel.cn/api/monitor/usage/quota/limit",
    "kimi": "https://api.kimi.com/coding/v1/usages",
}
QUERY_TIMEOUT = 8
QUERY_COOLDOWN = 30
MAX_RESPONSE_BYTES = 256 * 1024


class CodingPlanQuotaError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _object(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _reset_time(value):
    number = _number(value)
    try:
        if number is not None and number > 0:
            seconds = number / 1000 if number >= 10**12 else number
            reset = datetime.fromtimestamp(seconds, timezone.utc)
        elif isinstance(value, str):
            reset = datetime.fromisoformat(value)
        else:
            return None
        return reset.astimezone(timezone.utc).isoformat() if reset.tzinfo else None
    except (OSError, ValueError, OverflowError):
        return None


def _window(label, used_percent, reset_time):
    if used_percent is None or used_percent < 0:
        return None
    return {
        "label": label,
        "remaining_percent": round(max(0, 100 - used_percent), 1),
        "reset_at": _reset_time(reset_time),
    }


def _zhipu_windows(data):
    windows = []
    for limit in map(_object, _list(data.get("limits"))):
        kind = limit.get("type")
        if kind not in ("CREDIT_LIMIT", "TOKENS_LIMIT", "TIME_LIMIT"):
            continue
        minutes = _number(limit.get("windowMinutes"))
        unit = _number(limit.get("unit"))
        count = _number(limit.get("number"))
        if minutes == 300 or (unit, count) == (3, 5):
            period = "5 小时"
        elif minutes == 10080 or (unit, count) == (6, 1):
            period = "每周"
        elif (unit, count) == (5, 1) or (kind == "TIME_LIMIT" and unit is None and minutes is None):
            period = "每月"
        else:
            period = ""
        label = (period + " MCP").strip() if kind == "TIME_LIMIT" else period or "模型额度"
        used = _number(limit.get("percentage"))
        window = _window(label, used, limit.get("nextResetTime"))
        if window is not None:
            windows.append(window)
    return windows


def _kimi_counter(label, data):
    limit = _number(data.get("limit"))
    if limit is None or limit <= 0:
        return None
    remaining = _number(data.get("remaining"))
    used = limit - min(remaining, limit) if remaining is not None else _number(data.get("used"))
    return _window(label, used / limit * 100 if used is not None else None, data.get("resetTime"))


def _kimi_windows(data):
    # The legacy counters and newer ratio fields may appear together.
    quota = _object(data.get("quota")) or data
    ratios = _object(quota.get("usages"))
    windows = {}
    for key, camel_key, label in (
        ("limit_5h", "limit5h", "5 小时"),
        ("limit_7d", "limit7d", "每周"),
        ("limit_month_code", "monthCode", "每月编程"),
        ("limit_month_total", "monthTotal", "每月总额度"),
    ):
        entry = _object(ratios.get(key) or ratios.get(camel_key))
        ratio = _number(entry.get("used_ratio", entry.get("usedRatio")))
        window = _window(label, ratio * 100 if ratio is not None else None, entry.get("reset_time", entry.get("resetAt")))
        if window is not None:
            windows[key] = window

    for limit in map(_object, _list(data.get("limits"))):
        period = _object(limit.get("window"))
        if period.get("timeUnit") == "TIME_UNIT_MINUTE" and _number(period.get("duration")) == 300:
            window = _kimi_counter("5 小时", _object(limit.get("detail")))
            if window is not None:
                windows["limit_5h"] = window
                break
    # The legacy top-level usage counter corresponds to the long window.
    if "limit_7d" in windows or not ({"limit_month_code", "limit_month_total"} & windows.keys()):
        window = _kimi_counter("每周", _object(data.get("usage")))
        if window is not None:
            windows["limit_7d"] = window
    return [windows[key] for key in ("limit_5h", "limit_7d", "limit_month_code", "limit_month_total") if key in windows]


def fetch_coding_plan_quota(provider: str, api_key: str) -> list[dict]:
    url = CODING_PLAN_ENDPOINTS.get(provider)
    if not url:
        raise CodingPlanQuotaError("请先设置 Coding Plan 平台")
    if not api_key or any(not 33 <= ord(char) <= 126 for char in api_key):
        raise CodingPlanQuotaError("API Key 格式无效")

    authorization = f"Bearer {api_key}" if provider == "kimi" else api_key
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "MailAPI"})
    request.add_unredirected_header("Authorization", authorization)
    try:
        with build_opener(_NoRedirect()).open(request, timeout=QUERY_TIMEOUT) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise CodingPlanQuotaError("平台额度响应过长")
        payload = _object(json.loads(body))
    except HTTPError as error:
        if error.code in (401, 403):
            raise CodingPlanQuotaError("平台拒绝查询，请检查 Coding Plan API Key 和订阅") from None
        if error.code == 429:
            raise CodingPlanQuotaError("平台限制查询频率，请稍后重试") from None
        raise CodingPlanQuotaError(f"平台额度接口暂不可用（HTTP {error.code}）") from None
    except (URLError, OSError, HTTPException):
        raise CodingPlanQuotaError("连接额度接口失败，请稍后重试") from None
    except (ValueError, RecursionError):
        raise CodingPlanQuotaError("平台返回的额度格式无效") from None

    if str(payload.get("code")) in {"401", "403", "1001", "1002", "40111", "40112"}:
        raise CodingPlanQuotaError("平台拒绝查询，请检查 Coding Plan API Key 和订阅")
    data = _object(payload.get("data", payload))
    windows = _zhipu_windows(data) if provider == "zhipu" else _kimi_windows(data)
    if not windows:
        raise CodingPlanQuotaError("平台未返回可显示的额度，请检查订阅和 Coding Plan API Key")
    return windows


@dataclass
class _QuotaEntry:
    credential: tuple[str, bytes]
    snapshot: dict | None = None
    checked_at: float = -math.inf
    pending: bool = False


class CodingPlanQuotaCache:
    """Share manual checks across users and keep the last result on failures."""

    def __init__(self):
        self._entries: dict[str, _QuotaEntry] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _credential(provider: str, api_key: str) -> tuple[str, bytes]:
        return provider, hashlib.sha256(api_key.encode()).digest()

    def snapshot(self, plan_id: str, provider: str, api_key: str) -> dict | None:
        credential = self._credential(provider, api_key)
        with self._lock:
            entry = self._entries.get(plan_id)
            if not entry or entry.credential != credential:
                return None
            return {**(entry.snapshot or {}), "pending": True} if entry.pending else entry.snapshot

    def refresh(self, plan_id: str, provider: str, api_key: str) -> dict:
        credential = self._credential(provider, api_key)
        with self._lock:
            entry = self._entries.get(plan_id)
            if not entry or entry.credential != credential:
                entry = self._entries[plan_id] = _QuotaEntry(credential)
            if entry.pending:
                return {**(entry.snapshot or {}), "pending": True}
            if entry.snapshot is not None and time.monotonic() - entry.checked_at < QUERY_COOLDOWN:
                return entry.snapshot
            entry.pending = True

        snapshot = entry.snapshot
        try:
            windows = fetch_coding_plan_quota(provider, api_key)
            snapshot = {"windows": windows, "updated_at": datetime.now(timezone.utc).isoformat()}
        except CodingPlanQuotaError as error:
            snapshot = {**(snapshot or {}), "error": str(error)}
        finally:
            with self._lock:
                entry.snapshot = snapshot
                entry.checked_at = time.monotonic()
                entry.pending = False
        return snapshot

    def discard(self, plan_id: str) -> None:
        with self._lock:
            self._entries.pop(plan_id, None)
