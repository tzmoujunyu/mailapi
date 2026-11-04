"""Compatibility alias for the shared site-account implementation."""
import sys
from mailapi_shared import site_accounts as _implementation
sys.modules[__name__] = _implementation
