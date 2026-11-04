"""Compatibility alias for :mod:`mailapi_shared.mail_messages`."""
import sys

from mailapi_shared import mail_messages as _implementation

sys.modules[__name__] = _implementation
