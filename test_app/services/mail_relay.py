"""Compatibility alias for :mod:`mailapi_shared.mail_relay`."""
import sys

from mailapi_shared import mail_relay as _implementation

sys.modules[__name__] = _implementation
