"""Version metadata and private backups for OAuth/Codex authorization files."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
from datetime import datetime, timezone
import re


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _digest(path: Path):
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class AuthArtifactVersionStore:
    def __init__(self, manifest_path, archive_directory):
        self.path = Path(manifest_path)
        self.archive_directory = Path(archive_directory)
        self._lock = threading.RLock()
        self._data = {"version": 1, "artifacts": {}}
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if loaded.get("version") != 1 or not isinstance(loaded.get("artifacts"), dict):
                raise ValueError("授权文件版本记录格式无效")
            self._data = loaded
        self._secure_directories()

    def _secure_directories(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.archive_directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        os.chmod(self.archive_directory, 0o700)
        if self.path.exists():
            os.chmod(self.path, 0o600)

    @staticmethod
    def _key(provider, resource_id):
        return f"{str(provider).strip()}:{str(resource_id).strip()}"

    @staticmethod
    def _safe_segment(value):
        segment = re.sub(r"[^A-Za-z0-9_.=-]+", "_", str(value).strip()).strip("._")
        return (segment or "unknown")[:160]

    def _write_manifest(self):
        self._secure_directories()
        temporary = self.path.with_name(self.path.name + f".tmp.{os.getpid()}.{threading.get_ident()}")
        try:
            with temporary.open("w", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                json.dump(self._data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _next_version(self, entries):
        return max((int(item.get("version", 0)) for item in entries), default=0) + 1

    def write_json(self, path, value, *, provider, resource_id, source, writer=None):
        """Archive the current file, atomically write JSON, and record metadata."""
        target = Path(path)
        key = self._key(provider, resource_id)
        with self._lock:
            entries = self._data["artifacts"].setdefault(key, [])
            next_version = self._next_version(entries)
            if target.is_file():
                archive_dir = self.archive_directory / self._safe_segment(provider) / self._safe_segment(resource_id)
                archive_dir.mkdir(parents=True, exist_ok=True)
                os.chmod(archive_dir, 0o700)
                archive_path = archive_dir / f"v{next_version}-before.json"
                counter = 1
                while archive_path.exists():
                    archive_path = archive_dir / f"v{next_version}-before-{counter}.json"
                    counter += 1
                shutil.copy2(target, archive_path)
                os.chmod(archive_path, 0o600)
                entries.append({
                    "provider": str(provider),
                    "resource_id": str(resource_id),
                    "version": next_version,
                    "file_name": target.name,
                    "archive_file": str(archive_path.relative_to(self.archive_directory)),
                    "sha256": _digest(archive_path),
                    "size": archive_path.stat().st_size,
                    "created_at": _timestamp(),
                    "source": "legacy-existing" if not entries else "replaced",
                    "active": False,
                    "replaced_at": _timestamp(),
                })
                next_version += 1

            if writer is not None:
                writer(str(target), value)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.chmod(target.parent, 0o700)
                temporary = target.with_name(target.name + f".tmp.{os.getpid()}.{threading.get_ident()}")
                try:
                    with temporary.open("w", encoding="utf-8") as output:
                        os.chmod(temporary, 0o600)
                        json.dump(value, output, ensure_ascii=False, indent=2)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, target)
                    os.chmod(target, 0o600)
                finally:
                    if temporary.exists():
                        temporary.unlink()

            for item in entries:
                item["active"] = False
                item.setdefault("replaced_at", _timestamp())
            entries.append({
                "provider": str(provider),
                "resource_id": str(resource_id),
                "version": next_version,
                "file_name": target.name,
                "sha256": _digest(target),
                "size": target.stat().st_size,
                "created_at": _timestamp(),
                "source": str(source),
                "active": True,
            })
            self._write_manifest()
            return deepcopy(entries[-1])

    def register_existing(self, path, *, provider, resource_id, source="existing"):
        """Register an existing authorization file without copying its contents."""
        target = Path(path)
        if not target.is_file():
            return None
        key = self._key(provider, resource_id)
        digest = _digest(target)
        with self._lock:
            entries = self._data["artifacts"].setdefault(key, [])
            active = next((item for item in reversed(entries) if item.get("active")), None)
            if active and active.get("sha256") == digest:
                return deepcopy(active)
            for item in entries:
                item["active"] = False
                item.setdefault("replaced_at", _timestamp())
            item = {
                "provider": str(provider),
                "resource_id": str(resource_id),
                "version": self._next_version(entries),
                "file_name": target.name,
                "sha256": digest,
                "size": target.stat().st_size,
                "created_at": _timestamp(),
                "source": str(source),
                "active": True,
            }
            entries.append(item)
            self._write_manifest()
            return deepcopy(item)

    def list_public(self, *, provider=None, resource_id=None):
        with self._lock:
            result = []
            for key, entries in self._data["artifacts"].items():
                if provider is not None and not key.startswith(f"{provider}:"):
                    continue
                if resource_id is not None and key != self._key(provider or "", resource_id):
                    continue
                result.extend(deepcopy(entries))
            return result
