"""Compatibility alias for :mod:`mailapi_shared.account_passwords`."""
import sys

from mailapi_shared import account_passwords as _implementation

sys.modules[__name__] = _implementation
