"""Compatibility alias for :mod:`mailapi_shared.auth_storage`."""
import sys

from mailapi_shared import auth_storage as _implementation

sys.modules[__name__] = _implementation
