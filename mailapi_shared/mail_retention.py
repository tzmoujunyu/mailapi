"""Retention policy for captured codes and sign-in links."""
from datetime import datetime


def retain_mail_records(records, *, now, retention_seconds):
    kept = []
    for record in records:
        try:
            stamp = datetime.fromisoformat(record["created_at"])
            if stamp.tzinfo is not None and 0 <= now - stamp.timestamp() < retention_seconds:
                kept.append(record)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
    return kept
