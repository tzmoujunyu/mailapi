"""Compatibility alias for :mod:`mailapi_shared.account_credentials`."""
import sys

from mailapi_shared import account_credentials as _implementation

sys.modules[__name__] = _implementation
