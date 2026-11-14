"""Authenticated encryption for SQLite credentials."""
from base64 import b64decode, b64encode
import json
import os
from pathlib import Path
import re
import stat

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialCryptoError(ValueError):
    pass


def _encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value)


class CredentialKeyring:
    def __init__(self, path, *, data_dirs, production=False):
        path = Path(path).expanduser()
        if not path.is_absolute() or any(
            path.resolve().is_relative_to(root.resolve())
            for root in (Path(__file__).resolve().parents[1], *data_dirs)
        ):
            raise CredentialCryptoError("密钥须使用项目和数据目录外的绝对路径")
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384):
                    raise ValueError
                document = json.loads(source.read(16385))
            namespace, active, keys = document["namespace"], document["active"], document["keys"]
            environment = "production" if production else "test"
            if document.get("environment", "test") != environment:
                raise ValueError
            if not _identifier(namespace) or not _identifier(active) or not isinstance(keys, dict):
                raise ValueError
            keys = {key_id: b64decode(value, validate=True) for key_id, value in keys.items()}
            if active not in keys or any(not _identifier(key_id) or len(value) != 32 for key_id, value in keys.items()):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            raise CredentialCryptoError("无法读取密钥，检查环境、文件格式、所有者与 0600 权限") from None
        self.environment = environment
        self.namespace = namespace
        self.active = active
        self._keys = keys

    def _aad(self, key_id, scope):
        return _encode(["mailapi-" + self.environment, 1, self.namespace, key_id, *scope])

    def rotation_from(self, old):
        return (self.environment == old.environment and self.namespace == old.namespace and self.active not in old._keys
                and self._keys[self.active] not in old._keys.values())

    def seal(self, value, *scope):
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._keys[self.active]).encrypt(nonce, _encode(value), self._aad(self.active, scope))
        return [1, self.active, b64encode(nonce).decode("ascii"), b64encode(ciphertext).decode("ascii")]

    def open(self, envelope, *scope):
        try:
            if not isinstance(envelope, list) or len(envelope) != 4 or envelope[0] != 1:
                raise ValueError
            _, key_id, nonce, ciphertext = envelope
            nonce = b64decode(nonce, validate=True)
            if len(nonce) != 12:
                raise ValueError
            plaintext = AESGCM(self._keys[key_id]).decrypt(
                nonce, b64decode(ciphertext, validate=True), self._aad(key_id, scope)
            )
            return json.loads(plaintext)
        except (InvalidTag, KeyError, TypeError, ValueError):
            raise CredentialCryptoError("凭据鉴别失败，检查密钥版本与数据库完整性") from None

    def initialize_database(self, db, store):
        db.execute("CREATE TABLE credential_profile (marker TEXT NOT NULL)")
        db.execute("INSERT INTO credential_profile VALUES (?)", (_encode(self.seal(store, "profile", store)),))
        db.execute("PRAGMA user_version = 2")


def check_database_crypto(db, keyring, store):
    version, = db.execute("PRAGMA user_version").fetchone()
    if version == 1 and keyring is None:
        return
    if version != 2 or keyring is None:
        raise CredentialCryptoError("数据库版本与凭据加密配置不匹配")
    markers = db.execute("SELECT marker FROM credential_profile").fetchall()
    if len(markers) != 1:
        raise CredentialCryptoError("凭据库缺少加密标记")
    marker = json.loads(markers[0][0])
    if keyring.open(marker, "profile", store) != store or marker[1] != keyring.active:
        raise CredentialCryptoError("离线轮换后请使用与数据库匹配的当前密钥版本")
