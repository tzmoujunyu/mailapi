"""Enable the shared workspace without changing production security or storage."""
import os


def workspace_enabled():
    mode = os.getenv('MAILAPI_MODE', 'test').strip().lower()
    return mode == 'test' or (
        mode == 'production'
        and os.getenv('MAILAPI_WORKSPACE_ENABLED', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    )
