"""Compatibility alias for :mod:`mailapi_shared.mail_providers.proton`."""
import sys

from mailapi_shared.mail_providers import proton as _implementation

sys.modules[__name__] = _implementation
