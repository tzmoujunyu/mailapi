"""Read MailAPI dotenv settings without applying them."""
from pathlib import Path
import re


def dotenv_entries(path):
    try:
        with Path(path).open(encoding="utf-8") as source:
            for raw_line in source:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                key, found, value = line.partition("=")
                key = key.strip()
                if not found or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                yield key, value
    except FileNotFoundError:
        return
