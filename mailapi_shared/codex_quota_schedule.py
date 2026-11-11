"""Process-wide scheduling for automatic and manual Codex quota queries."""
from dataclasses import dataclass
import math
import random
import threading
import time


@dataclass
class _RefreshWindow:
    attempted_at: float = -math.inf
    automatic_at: float = -math.inf
    pending: bool = False


class CodexQuotaSchedule:
    def __init__(self):
        self._windows = {}
        self._lock = threading.Lock()

    def begin(self, account_id, *, manual=False, last_checked_at=None):
        now = time.monotonic()
        with self._lock:
            window = self._windows.get(account_id)
            if window is None:
                window = self._windows[account_id] = _RefreshWindow()
                if last_checked_at is not None:
                    age = max(0, time.time() - last_checked_at)
                    window.attempted_at = now - age
                    window.automatic_at = window.attempted_at + random.randint(180, 480)
            if (window.pending or now - window.attempted_at < 60
                    or (not manual and now < window.automatic_at)):
                return None
            window.attempted_at = now
            window.automatic_at = now + random.randint(180, 480)
            window.pending = True
            return window

    def finish(self, account_id, window):
        with self._lock:
            if self._windows.get(account_id) is window:
                window.pending = False

    def discard(self, account_id):
        with self._lock:
            self._windows.pop(account_id, None)
