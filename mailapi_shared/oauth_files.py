"""Encrypted OAuth artifacts stored under the isolated runtime directory."""
from base64 import b64decode, b64encode
import json
import os
from pathlib import Path
import tempfile

from .auth_storage import ensure_private_directory
from .credential_crypto import CredentialCryptoError


class OAuthFiles:
    def __init__(self, directory, keyring):
        self.directory = Path(directory).resolve()
        self.keyring = keyring

    def _scope(self, path):
        path = Path(path)
        try:
            relative = path.absolute().relative_to(self.directory)
            if path.resolve() != self.directory / relative or ".." in relative.parts:
                raise ValueError
        except ValueError:
            raise CredentialCryptoError("OAuth 文件须位于隔离数据目录内，且不能使用符号链接") from None
        return ("oauth-file", relative.as_posix())

    def read_bytes(self, path):
        scope = self._scope(path)
        try:
            envelope = json.loads(Path(path).read_bytes())
            return b64decode(self.keyring.open(envelope, *scope), validate=True)
        except (ValueError, TypeError):
            raise CredentialCryptoError("无法验证 OAuth 授权文件，请检查密钥或还原备份") from None

    def read_json(self, path):
        try:
            return json.loads(self.read_bytes(path))
        except (UnicodeError, json.JSONDecodeError):
            raise CredentialCryptoError("OAuth 授权文件内容格式无效") from None

    def write_bytes(self, path, raw):
        scope = self._scope(path)
        path = Path(path)
        envelope = self.keyring.seal(b64encode(raw).decode("ascii"), *scope)
        ensure_private_directory(path.parent)
        fd, temp = tempfile.mkstemp(prefix=".oauth-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(envelope, output, separators=(",", ":"))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, path)
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            Path(temp).unlink(missing_ok=True)

    def write_json(self, path, value):
        self.write_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))

    def copy(self, source, target):
        self.write_bytes(target, self.read_bytes(source))
        info = Path(source).stat()
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))

    def identical(self, first, second):
        return self.read_bytes(first) == self.read_bytes(second)
