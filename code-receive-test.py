"""Run the isolated test version; production still uses code-receive.py."""
import os

import uvicorn

from test_app.main import TEST_PORT, app


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("TEST_HOST", "127.0.0.1"), port=TEST_PORT)
