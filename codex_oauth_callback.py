"""Compatibility alias for :mod:`mailapi_shared.codex_oauth_callback`."""
import sys

from mailapi_shared import codex_oauth_callback as _implementation

sys.modules[__name__] = _implementation
