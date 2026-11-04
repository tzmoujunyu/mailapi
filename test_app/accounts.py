"""Compatibility alias for the shared account-router implementation."""
import sys
from mailapi_shared import accounts as _implementation
sys.modules[__name__] = _implementation
