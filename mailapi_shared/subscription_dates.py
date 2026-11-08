"""Informational subscription dates from imported ID-token claims."""
from datetime import datetime
import time


MAX_SUBSCRIPTION_AGE_SECONDS = 7 * 24 * 60 * 60


def _timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        result = int(parsed.timestamp())
        return result if result > 0 else None
    except (ValueError, OverflowError, OSError):
        return None


def read_subscription_claims(claims, account_id, workspace_id, identity_matches):
    auth = claims.get("https://api.openai.com/auth")
    auth = auth if isinstance(auth, dict) else {}
    result = {
        "source": "id_token", "account_id": account_id, "workspace_id": workspace_id,
        "started_at": None, "expires_at": None, "checked_at": None, "invalid_reason": None,
    }
    if not auth or not any(key in auth for key in (
        "chatgpt_subscription_active_start", "chatgpt_subscription_active_until", "chatgpt_subscription_last_checked",
    )):
        result["invalid_reason"] = "missing_dates"
        return result
    if not identity_matches:
        result["invalid_reason"] = "account_mismatch"
        return result
    start = auth.get("chatgpt_subscription_active_start")
    result.update(
        started_at=_timestamp(start),
        expires_at=_timestamp(auth.get("chatgpt_subscription_active_until")),
        checked_at=_timestamp(auth.get("chatgpt_subscription_last_checked")),
    )
    if not result["expires_at"]:
        result["invalid_reason"] = "missing_dates"
    elif start is not None and (not result["started_at"] or result["started_at"] >= result["expires_at"]):
        result["invalid_reason"] = "invalid_dates"
    elif not result["checked_at"]:
        result["invalid_reason"] = "missing_check_time"
    return result


def public_subscription(snapshot, now=None):
    now = time.time() if now is None else now
    checked = snapshot.get("checked_at")
    reason = snapshot.get("invalid_reason")
    if not reason:
        if checked > now:
            reason = "future_check_time"
        elif now - checked > MAX_SUBSCRIPTION_AGE_SECONDS:
            reason = "stale"
    available = reason is None
    return {
        "source": "id_token", "available": available, "unavailable_reason": reason,
        "started_at": snapshot.get("started_at") if available else None,
        "expires_at": snapshot.get("expires_at") if available else None,
        "checked_at": checked,
        "valid_until": checked + MAX_SUBSCRIPTION_AGE_SECONDS if checked else None,
    }
