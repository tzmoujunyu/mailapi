"""Safe diagnostics for errors that may contain provider response bodies."""
import re
from pathlib import Path


OAUTH_ERRORS = ("invalid_grant", "invalid_client", "invalid_scope", "interaction_required",
                "temporarily_unavailable", "refresh_token_reused", "refresh_token_expired",
                "refresh_token_invalidated", "app_session_terminated")
_STATUS = re.compile(r"\b(?:HTTP\s+(?:status\s+)?|status[=: ]+)([45][0-9]{2})\b", re.I)
_JWT = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b")
_GOOGLE = re.compile(r"(?:\bya29\.|1//)[\w/-]{12,}")
_URL = re.compile(r"https?://[^\s\"'<>]+", re.I)
_SECRET = re.compile(
    r"""\b(access[_-]?token|refresh[_-]?token|id[_-]?token|api[_-]?key|authorization|client[_-]?secret|"""
    r"""password|totp_secret|session_id|(?:set-)?cookie|signature|code_verifier|code_challenge|"""
    r"""code|state|nonce)["']?\s*[:=].*""", re.I,
)


def redact_log_text(value):
    text = _URL.sub("<url>", str(value))
    text = _JWT.sub("<redacted>", text)
    text = _GOOGLE.sub("<redacted>", text)
    return _SECRET.sub(r"\1=<redacted>", re.sub(r"\bBearer\s+[^\s,;]+", "Bearer <redacted>", text, flags=re.I))


def error_diagnostic(error):
    # Report only recognized categories and numeric status values, never raw response text.
    text = str(error).lower()
    label = next((name for name in OAUTH_ERRORS if re.search(rf"\b{name}\b", text)), "")
    status = getattr(error, "status", None)
    if status is None:
        match = _STATUS.search(text)
        status = int(match[1]) if match else None
    if not label:
        label = next((kind for marker, kind in (
            ("timed out", "timeout"), ("timeout", "timeout"), ("certificate_verify_failed", "tls_certificate"),
            ("handshake", "tls_handshake"), ("connection reset", "connection_reset"),
            ("connection refused", "connection_refused"),
        ) if marker in text), "")
    return " ".join(part for part in (
        type(error).__name__, label, f"HTTP {status}" if isinstance(status, int) and 400 <= status <= 599 else "",
    ) if part)


def safe_traceback(error):
    trace = error.__traceback__
    frames = []
    while trace:
        frame = trace.tb_frame.f_code
        frames.append(f"{Path(frame.co_filename).name}:{trace.tb_lineno} {frame.co_name}")
        trace = trace.tb_next
    return " > ".join(frames)
