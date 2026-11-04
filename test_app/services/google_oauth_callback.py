"""Compatibility alias for :mod:`mailapi_shared.google_oauth_callback`."""
import sys

from mailapi_shared import google_oauth_callback as _implementation

sys.modules[__name__] = _implementation
