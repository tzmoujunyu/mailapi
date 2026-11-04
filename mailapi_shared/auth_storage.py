from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Literal


MigrationResult = Literal["missing", "unchanged", "moved", "deduplicated", "archived"]


def ensure_private_directory(path: str | Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def secure_auth_file(path: str | Path) -> None:
    target = Path(path)
    if target.is_file():
        os.chmod(target, 0o600)


def files_are_identical(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False

    def digest(path: Path) -> bytes:
        hasher = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(64 * 1024), b""):
                hasher.update(chunk)
        return hasher.digest()

    return digest(first) == digest(second)


def unique_archive_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stamp = time.strftime("%Y%m%d%H%M%S")
    counter = 0
    while True:
        suffix = f".conflict.{stamp}" if counter == 0 else f".conflict.{stamp}.{counter}"
        candidate = directory / f"{filename}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def archive_auth_file(source: str | Path, archive_directory: str | Path) -> MigrationResult:
    source_path = Path(source)
    if not source_path.is_file():
        return "missing"
    archive_dir = ensure_private_directory(archive_directory)
    target = archive_dir / source_path.name
    if target.exists() and files_are_identical(source_path, target):
        source_path.unlink()
        secure_auth_file(target)
        return "deduplicated"
    target = unique_archive_path(archive_dir, source_path.name)
    source_path.replace(target)
    secure_auth_file(target)
    return "archived"


def migrate_auth_file(
    source: str | Path,
    target: str | Path,
    conflict_archive_directory: str | Path,
) -> MigrationResult:
    source_path = Path(source)
    target_path = Path(target)
    if source_path == target_path:
        secure_auth_file(target_path)
        return "unchanged" if target_path.exists() else "missing"
    if not source_path.is_file():
        secure_auth_file(target_path)
        return "missing"

    ensure_private_directory(target_path.parent)
    if target_path.exists():
        if files_are_identical(source_path, target_path):
            source_path.unlink()
            secure_auth_file(target_path)
            return "deduplicated"
        archive_auth_file(target_path, conflict_archive_directory)

    source_path.replace(target_path)
    secure_auth_file(target_path)
    return "moved"
