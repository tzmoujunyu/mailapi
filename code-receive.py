"""Production entry point backed by the shared application factory."""
import importlib
import os
import sys
import types

os.environ.setdefault("MAILAPI_MODE", "production")
_impl = importlib.import_module("mailapi_shared.application")
if _impl.APP_MODE != "production":
    os.environ["MAILAPI_MODE"] = "production"
    _impl = importlib.reload(_impl)

if __name__ == "__main__":
    _impl.run()
else:
    for _name in dir(_impl):
        if not _name.startswith("__"):
            globals().setdefault(_name, getattr(_impl, _name))

    _module = sys.modules.get(__name__)
    if _module is not None:
        class _ApplicationAlias(types.ModuleType):
            def __getattr__(self, name):
                return getattr(_impl, name)

            def __setattr__(self, name, value):
                if name not in {"_implementation"} and hasattr(_impl, name):
                    setattr(_impl, name, value)
                super().__setattr__(name, value)

        _module.__class__ = _ApplicationAlias
        _module._implementation = _impl
    else:
        # ``module_from_spec(...).exec_module(...)`` does not insert the
        # temporary module into sys.modules.  Export the shared symbols so
        # that inspection and one-off tooling still work in that form.
        for _name in dir(_impl):
            if not _name.startswith("__"):
                globals().setdefault(_name, getattr(_impl, _name))
        _implementation = _impl
