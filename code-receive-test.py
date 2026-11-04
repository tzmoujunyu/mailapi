"""Run the shared application in test mode."""
import importlib
import os

os.environ.setdefault("MAILAPI_MODE", "test")
from mailapi_shared import application as app_module

if app_module.APP_MODE != "test":
    os.environ["MAILAPI_MODE"] = "test"
    app_module = importlib.reload(app_module)

TEST_PORT = app_module.APP_PORT
app = app_module.app

if __name__ == "__main__":
    app_module.run()
