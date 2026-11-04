"""Compatibility alias for :mod:`mailapi_shared.resource_accounts`."""
import sys

from mailapi_shared import resource_accounts as _implementation

sys.modules[__name__] = _implementation
