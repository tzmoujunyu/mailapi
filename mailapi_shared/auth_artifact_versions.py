"""Version metadata and private backups for OAuth/Codex authorization files."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
from datetime import datetime, timedelta, timezone
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
    def __init__(self, manifest_path, archive_directory, *, oauth_files=None):
        self.path = Path(manifest_path)
        self.archive_directory = Path(archive_directory)
        self.oauth_files = oauth_files
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

    def _metadata(self, path, provider):
        if self.oauth_files and provider in {"gmail", "outlook", "codex"}:
            raw = self.oauth_files.read_bytes(path)
            return hashlib.sha256(raw).hexdigest(), len(raw)
        return _digest(path), path.stat().st_size

    @staticmethod
    def _entry_time(entry):
        for name in ("created_at", "replaced_at"):
            value = entry.get(name)
            if not value:
                continue
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
        return None

    def _archive_path(self, relative):
        archive_root = self.archive_directory.resolve()
        raw_candidate = self.archive_directory / str(relative)
        if raw_candidate.is_symlink():
            raise ValueError("授权归档不能使用符号链接")
        candidate = raw_candidate.resolve()
        if candidate != archive_root and not candidate.is_relative_to(archive_root):
            raise ValueError("授权归档路径超出归档目录")
        return candidate

    def _create_recovery_point(self):
        if self.oauth_files is None:
            raise ValueError("授权清理需要已启用的加密 OAuth 存储")
        recovery_root = self.archive_directory.parent / "auth_artifact_restore_points"
        recovery_root.mkdir(parents=True, exist_ok=True)
        os.chmod(recovery_root, 0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        recovery = recovery_root / stamp
        counter = 1
        while recovery.exists():
            recovery = recovery_root / f"{stamp}-{counter}"
            counter += 1
        recovery.mkdir(mode=0o700)
        self.oauth_files.write_json(recovery / "manifest.json", self._data)
        for source in self.archive_directory.rglob("*"):
            if source.is_dir():
                continue
            if source.is_symlink() or not source.is_file():
                raise ValueError("授权归档包含不受支持的文件")
            target = recovery / "archives" / source.relative_to(self.archive_directory)
            self.oauth_files.copy(source, target)
        return recovery

    def prune(self, *, keep_history=5, max_age_days=30, now=None):
        """Create an encrypted restore point, then prune stale archive versions.

        The current entry is always kept. Historical entries are retained when
        they are among the newest ``keep_history`` entries for that resource or
        were created within ``max_age_days``. The two retention rules are a
        union, so a recent entry is never removed just because the count is full.
        """
        if not isinstance(keep_history, int) or keep_history < 0:
            raise ValueError("授权历史保留数量无效")
        if not isinstance(max_age_days, int) or max_age_days < 0:
            raise ValueError("授权历史保留天数无效")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        cutoff = current - timedelta(days=max_age_days)

        with self._lock:
            recovery = self._create_recovery_point()
            original = deepcopy(self._data)
            kept = {}
            removed_archive_files = set()
            removed_entries = 0
            try:
                for key, entries in self._data["artifacts"].items():
                    historical = [
                        (index, item) for index, item in enumerate(entries)
                        if not item.get("active")
                    ]
                    ranked = sorted(
                        historical,
                        key=lambda pair: (
                            self._entry_time(pair[1]) or datetime.min.replace(tzinfo=timezone.utc),
                            int(pair[1].get("version", 0)),
                        ),
                        reverse=True,
                    )
                    keep_indexes = {index for index, _ in ranked[:keep_history]}
                    for index, item in historical:
                        timestamp = self._entry_time(item)
                        if timestamp is None or timestamp >= cutoff:
                            keep_indexes.add(index)
                    selected = []
                    for index, item in enumerate(entries):
                        if item.get("active") or index in keep_indexes:
                            selected.append(item)
                        else:
                            removed_entries += 1
                            if item.get("archive_file"):
                                removed_archive_files.add(item["archive_file"])
                    kept[key] = selected

                referenced = {
                    item["archive_file"]
                    for entries in kept.values()
                    for item in entries
                    if item.get("archive_file")
                }
                for path in self.archive_directory.rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        relative = path.relative_to(self.archive_directory).as_posix()
                        if relative not in referenced:
                            removed_archive_files.add(relative)

                self._data["artifacts"] = kept
                self._write_manifest()
                for relative in sorted(removed_archive_files):
                    path = self._archive_path(relative)
                    if path.is_file():
                        path.unlink()
                for directory in sorted(
                    (path for path in self.archive_directory.rglob("*") if path.is_dir()),
                    key=lambda path: len(path.parts), reverse=True,
                ):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
            except Exception:
                self._data = original
                self._restore_recovery_point(recovery)
                raise

            return {
                "recovery_point": str(recovery),
                "removed_entries": removed_entries,
                "removed_files": len(removed_archive_files),
                "remaining_entries": sum(len(entries) for entries in kept.values()),
            }

    def _restore_recovery_point(self, recovery):
        manifest = self.oauth_files.read_json(recovery / "manifest.json")
        for path in self.archive_directory.rglob("*"):
            if path.is_file() and not path.is_symlink():
                path.unlink()
        for source in (recovery / "archives").rglob("*"):
            if source.is_dir():
                continue
            target = self.archive_directory / source.relative_to(recovery / "archives")
            self.oauth_files.copy(source, target)
        self._data = manifest
        self._write_manifest()

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
                if self.oauth_files and provider in {"gmail", "outlook", "codex"}:
                    self.oauth_files.copy(target, archive_path)
                else:
                    shutil.copy2(target, archive_path)
                os.chmod(archive_path, 0o600)
                digest, size = self._metadata(archive_path, provider)
                entries.append({
                    "provider": str(provider),
                    "resource_id": str(resource_id),
                    "version": next_version,
                    "file_name": target.name,
                    "archive_file": str(archive_path.relative_to(self.archive_directory)),
                    "sha256": digest,
                    "size": size,
                    "created_at": _timestamp(),
                    "source": "legacy-existing" if not entries else "replaced",
                    "active": False,
                    "replaced_at": _timestamp(),
                })
                next_version += 1

            if self.oauth_files and provider in {"gmail", "outlook", "codex"}:
                self.oauth_files.write_json(target, value)
            elif writer is not None:
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
            digest, size = self._metadata(target, provider)
            entries.append({
                "provider": str(provider),
                "resource_id": str(resource_id),
                "version": next_version,
                "file_name": target.name,
                "sha256": digest,
                "size": size,
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
        digest, size = self._metadata(target, provider)
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
                "size": size,
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
