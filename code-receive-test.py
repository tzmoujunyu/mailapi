"""Run the shared application in test mode."""
import importlib
import os

os.environ.setdefault("MAILAPI_MODE", "test")
# The interactive test entry point always starts from a fresh production snapshot.
os.environ.setdefault("TEST_SNAPSHOT_FROM_PRODUCTION", "1")
from mailapi_shared import application as app_module

if app_module.APP_MODE != "test":
    os.environ["MAILAPI_MODE"] = "test"
    app_module = importlib.reload(app_module)

TEST_PORT = app_module.APP_PORT
app = app_module.app

if __name__ == "__main__":
    app_module.run()
