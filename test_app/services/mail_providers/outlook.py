"""Compatibility alias for :mod:`mailapi_shared.mail_providers.outlook`."""
import sys

from mailapi_shared.mail_providers import outlook as _implementation

sys.modules[__name__] = _implementation
