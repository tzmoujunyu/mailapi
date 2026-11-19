"""Website identities, sessions and group grants for both app modes."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import hmac
from http.client import HTTPException
import json
import logging
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
import pyotp


SESSION_SECONDS = 86400
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
PASSWORD_HASHER = PasswordHasher(memory_cost=19456, time_cost=2, parallelism=1, type=Type.ID)
PASSWORD_HASH_SLOTS = threading.BoundedSemaphore(4)
ADMIN_ROLES = {"manager", "admin"}
MAX_BATCH_USERS = 100
MAX_USER_NOTE_LENGTH = 80
USERNAME_PATTERN = re.compile(r"[a-z0-9_.-]{3,64}")
TOTP_PATTERN = re.compile(r"[0-9]{6}")
INTERFACE_OPTIONS = {
    "theme": ("system", "light", "dark"),
    "preset": ("default", "anthropic", "simple-large", "underground", "rose-garden", "lake-view",
               "sunset-glow", "forest-whisper", "ocean-breeze", "lavender-dream"),
    "font": ("auto", "sans", "serif"),
    "radius": ("auto", "0", "0.3", "0.5", "0.75", "1"),
    "language": ("zh-CN", "en"),
}


def timestamp_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class SiteAccountError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def validate_password_length(password):
    if not isinstance(password, str) or not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise SiteAccountError(f"密码长度须为 {MIN_PASSWORD_LENGTH}–{MAX_PASSWORD_LENGTH} 个字符")
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 or
           0xD800 <= ord(char) <= 0xDFFF or char in "\u2028\u2029" for char in password):
        raise SiteAccountError("密码包含不可输入的字符")


def password_is_pwned(password):
    # SHA-1 is only the lookup format of the HIBP range API.
    digest = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    request = Request(f"https://api.pwnedpasswords.com/range/{digest[:5]}",
                      headers={"Add-Padding": "true", "User-Agent": "MailAPI"})
    try:
        with urlopen(request, timeout=2) as response:
            body = response.read(128 * 1024 + 1)
        if len(body) > 128 * 1024:
            raise ValueError("range response too large")
        suffix = digest[5:].encode("ascii")
        for line in body.splitlines():
            candidate, _, count = line.partition(b":")
            if candidate.upper() == suffix:
                return int(count) > 0
    except (OSError, HTTPException, ValueError):
        logging.getLogger(__name__).warning("HIBP 密码检查暂不可用，已跳过本次查询")
    return False


def validate_new_password(password, *identities):
    validate_password_length(password)
    folded = password.casefold()
    for identity in identities:
        prefix = str(identity or "").partition("@")[0].casefold()
        if prefix and prefix in folded:
            raise SiteAccountError("密码不能包含用户名或邮箱前缀")
    if password_is_pwned(password):
        raise SiteAccountError("该密码曾出现在泄露记录中，请换一个密码")


def password_hash(password):
    validate_password_length(password)
    with PASSWORD_HASH_SLOTS:
        return PASSWORD_HASHER.hash(password)


def password_matches(password, encoded):
    if not isinstance(password, str) or len(password) > MAX_PASSWORD_LENGTH or not isinstance(encoded, str):
        return False
    legacy = encoded.startswith("pbkdf2_sha256$")
    try:
        with PASSWORD_HASH_SLOTS:
            if encoded.startswith("$argon2id$"):
                return PASSWORD_HASHER.verify(encoded, password)
            if legacy:
                _, iterations, salt, digest = encoded.split("$", 3)
                actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
                return hmac.compare_digest(actual.hex(), digest)
    except (ValueError, VerificationError, InvalidHashError):
        return False
    return False


DUMMY_PASSWORD_HASH = password_hash(secrets.token_urlsafe(32))


def normalize_user_note(note):
    if not isinstance(note, str):
        raise SiteAccountError("账户备注须为文本")
    note = note.strip()
    if len(note) > MAX_USER_NOTE_LENGTH or len(note.splitlines()) > 1:
        raise SiteAccountError(f"账户备注须为单行文本，最多 {MAX_USER_NOTE_LENGTH} 个字符")
    try:
        note.encode("utf-8")
    except UnicodeEncodeError:
        raise SiteAccountError("账户备注包含无效字符") from None
    return note


class SiteAccountStore:
    def __init__(self, path, *, require_admin_mfa=False):
        self.path = Path(path)
        self.require_admin_mfa = require_admin_mfa
        self._lock = threading.RLock()
        self._data = {"version": 1, "users": {}, "groups": {}, "sessions": {}}
        if self.path.exists():
            self._data = self._read_snapshot()
            self._normalize_snapshot()
            os.chmod(self.path, 0o600)

    def _read_snapshot(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    @staticmethod
    def validate_snapshot(data):
        if not isinstance(data, dict) or data.get("version") != 1 or any(
            not isinstance(data.get(key), dict) for key in ("users", "groups", "sessions")
        ):
            raise SiteAccountError("网站账户存储格式无效")
        admins = [user for user in data["users"].values() if user["role"] == "admin"]
        if len(admins) != 1 or admins[0]["username"] != "admin":
            raise SiteAccountError("网站账户存储必须包含唯一的 admin")

    def _normalize_snapshot(self):
        self.validate_snapshot(self._data)
        data = deepcopy(self._data)
        for user in data["users"].values():
            self._normalize_user(user)
        if data != self._data:
            self._write_snapshot(data)
            self._data = data

    @staticmethod
    def _normalize_user(user):
        user.setdefault("status", "active")
        user.setdefault("last_login_at", None)
        user.setdefault("last_failed_login_at", None)
        user.setdefault("failed_login_count", 0)
        user.setdefault("password_changed_at", None)
        user.setdefault("password_version", 1)
        user.setdefault("password_source", "legacy")
        user.setdefault("must_change_password", False)
        return user

    @contextmanager
    def _edit(self):
        with self._lock:
            data = deepcopy(self._data)
            yield data
            self._write_snapshot(data)
            self._data = data

    def _write_snapshot(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _public(user, *, include_note=False, include_status=False, include_reminder=False):
        result = {key: user.get(key) for key in ("id", "username", "role", "created_at")}
        if include_status:
            result.update({
                key: user.get(key)
                for key in (
                    "status", "last_login_at", "last_failed_login_at", "failed_login_count",
                    "password_changed_at", "password_version", "password_source",
                    "must_change_password",
                )
            })
        if include_note:
            result["note"] = user.get("note", "")
        if include_reminder:
            result["must_change_password"] = user.get("must_change_password", False)
        return result

    @staticmethod
    def _administrator(data, actor_id, *, admin_only=False):
        actor = data["users"].get(actor_id)
        roles = {"admin"} if admin_only else ADMIN_ROLES
        if not actor or actor["role"] not in roles:
            raise SiteAccountError("仅 admin 可以执行此操作" if admin_only else "需要管理员权限", 403)
        return actor

    def ensure_admin(self, password):
        with self._lock:
            if self._data["users"]:
                return
            if not password:
                raise SiteAccountError("首次启动须设置管理员密码（至少 8 个字符）")
            validate_new_password(password, "admin")
            encoded = password_hash(password)
            with self._edit() as data:
                user_id = secrets.token_hex(16)
                data["users"][user_id] = {
                    "id": user_id, "username": "admin", "role": "admin",
                    "password_hash": encoded, "created_at": int(time.time()),
                    "status": "active", "password_source": "initial",
                    "password_version": 1, "must_change_password": False,
                    "password_changed_at": None, "last_login_at": None,
                    "last_failed_login_at": None, "failed_login_count": 0,
                }

    def provision_admin(self, username, password, totp_secret):
        username = username.strip().lower()
        totp_secret = totp_secret.replace(" ", "").upper()
        if not re.fullmatch(r"[A-Z2-7]{32}", totp_secret):
            raise SiteAccountError("请输入 32 个字符的 Base32 验证器密钥")
        with self._edit() as data:
            if not data["users"] and username == "admin":
                user_id = secrets.token_hex(16)
                data["users"][user_id] = {
                    "id": user_id, "username": username, "role": "admin",
                    "created_at": int(time.time()), "status": "active",
                    "last_login_at": None, "last_failed_login_at": None,
                    "failed_login_count": 0, "password_changed_at": None,
                    "password_version": 0, "password_source": "initial",
                    "must_change_password": False,
                }
            user = next((user for user in data["users"].values() if user["username"] == username), None)
            if not user or user["role"] not in ADMIN_ROLES:
                raise SiteAccountError("未找到管理员账户")
            validate_new_password(password, username, user.get("email"))
            user["password_hash"] = password_hash(password)
            user["password_changed_at"] = timestamp_iso()
            user["password_source"] = "admin"
            user["password_version"] = int(user.get("password_version", 0)) + 1
            user["must_change_password"] = user["role"] != "admin"
            user["totp_secret"] = totp_secret
            user.pop("totp_last_step", None)
            self._revoke_user_sessions(data, user["id"])

    def check_admin_setup(self):
        with self._lock:
            if not any(user["role"] == "admin" and user.get("totp_secret")
                       for user in self._data["users"].values()):
                raise SiteAccountError("请先运行 python -m mailapi_shared.admin_credentials 配置 admin 密码和双因素认证")

    def _needs_mfa(self, user):
        return user["role"] in ADMIN_ROLES and (self.require_admin_mfa or user.get("totp_secret"))

    def list_users(self, actor_id):
        with self._lock:
            self._administrator(self._data, actor_id)
            return [self._public(user, include_note=True, include_status=True) for user in self._data["users"].values()]

    def require_admin(self, actor_id):
        with self._lock:
            return self._administrator(self._data, actor_id, admin_only=True)

    def get_user(self, user_id, *, include_note=False):
        with self._lock:
            user = self._data["users"].get(user_id)
            return self._public(user, include_note=include_note) if user else None

    def preferences(self, user_id):
        with self._lock:
            user = self._data["users"].get(user_id)
            if not user:
                raise SiteAccountError("请先登录", 401)
            theme = user.get("theme", "system")
            return {"theme": theme if theme in ("system", "light", "dark") else "system"}

    def set_theme(self, user_id, theme):
        if not isinstance(theme, str) or theme not in {"system", "light", "dark"}:
            raise SiteAccountError("外观须为浅色、深色或跟随系统")
        with self._edit() as data:
            user = data["users"].get(user_id)
            if not user:
                raise SiteAccountError("请先登录", 401)
            user["theme"] = theme
        return {"theme": theme}

    def interface_preferences(self, user_id):
        with self._lock:
            user = self._data["users"].get(user_id)
            if not user:
                raise SiteAccountError("请先登录", 401)
            saved = {**user.get("interface", {}), "theme": user.get("theme", "system")}
            return {key: saved[key] if saved.get(key) in values else values[0]
                    for key, values in INTERFACE_OPTIONS.items()}

    def set_interface_preferences(self, user_id, payload):
        if not payload or set(payload) - INTERFACE_OPTIONS.keys() or any(
            not isinstance(value, str) or value not in INTERFACE_OPTIONS[key]
            for key, value in payload.items()
        ):
            raise SiteAccountError("界面设置无效")
        with self._edit() as data:
            user = data["users"].get(user_id)
            if not user:
                raise SiteAccountError("请先登录", 401)
            settings = user.setdefault("interface", {})
            settings.update({key: value for key, value in payload.items() if key != "theme"})
            if "theme" in payload:
                user["theme"] = payload["theme"]
        return self.interface_preferences(user_id)

    def create_user(self, actor_id, username, password, role="user", *, note=""):
        return self.create_users(actor_id, [username], password, role, note=note)[0]

    def create_users(self, actor_id, usernames, password, role="user", *, admin_only=False, note=""):
        if not isinstance(usernames, list) or not usernames:
            raise SiteAccountError("至少需要一个用户名")
        if len(usernames) > MAX_BATCH_USERS:
            raise SiteAccountError(f"一次最多创建 {MAX_BATCH_USERS} 个账户")
        if not isinstance(role, str) or role not in {"user", "manager"}:
            raise SiteAccountError("admin 为唯一保留账户，新增角色只能是用户或普通管理员")
        note = normalize_user_note(note)
        normalized = []
        seen = set()
        for value in usernames:
            username = value.strip().lower() if isinstance(value, str) else ""
            if not USERNAME_PATTERN.fullmatch(username):
                raise SiteAccountError("用户名须为 3–64 位字母、数字、下划线、点或短横线")
            if username == "admin":
                raise SiteAccountError("admin 为唯一保留账户，不能批量创建")
            if username in seen:
                raise SiteAccountError(f"批量用户名重复：{username}", 409)
            seen.add(username)
            normalized.append(username)

        def check_new_users(data):
            self._administrator(data, actor_id, admin_only=admin_only)
            existing = {user["username"] for user in data["users"].values()}
            conflicts = [username for username in normalized if username in existing]
            if conflicts:
                raise SiteAccountError(f"用户名已存在：{', '.join(conflicts)}", 409)
        with self._lock:
            check_new_users(self._data)
        validate_new_password(password, *normalized)
        hashes = [password_hash(password) for _ in normalized]
        with self._edit() as data:
            check_new_users(data)
            created = []
            created_at = int(time.time())
            for username, encoded in zip(normalized, hashes):
                user_id = secrets.token_hex(16)
                user = {
                    "id": user_id, "username": username, "role": role,
                    "password_hash": encoded, "created_at": created_at,
                    "status": "active", "password_changed_at": None,
                    "password_source": "initial", "password_version": 1,
                    "must_change_password": True, "last_login_at": None,
                    "last_failed_login_at": None, "failed_login_count": 0,
                }
                if note:
                    user["note"] = note
                data["users"][user_id] = user
                created.append(self._public(user, include_note=True))
        return created

    @staticmethod
    def _revoke_user_sessions(data, user_id):
        data["sessions"] = {
            key: session for key, session in data["sessions"].items() if session["user_id"] != user_id
        }

    def update_user(self, actor_id, user_id, payload):
        if "password" in payload:
            with self._lock:
                actor = self._administrator(self._data, actor_id)
                user = self._data["users"].get(user_id)
                if not user:
                    raise SiteAccountError("未找到网站账户", 404)
                if user["role"] == "admin" or (actor["role"] != "admin" and
                                              (user["role"] != "user" or "role" in payload)):
                    raise SiteAccountError("无权重置此账户的密码", 403)
            validate_new_password(payload["password"], user["username"], user.get("email"))
            encoded = password_hash(payload["password"])
        with self._edit() as data:
            actor = self._administrator(data, actor_id)
            user = data["users"].get(user_id)
            if not user:
                raise SiteAccountError("未找到网站账户", 404)
            if user["role"] == "admin" and (actor["role"] != "admin" or set(payload) - {"note"}):
                raise SiteAccountError("admin 账户固定。密码可通过修改本人密码更新", 403)
            if "role" in payload:
                self._administrator(data, actor_id, admin_only=True)
                if not isinstance(payload["role"], str) or payload["role"] not in {"user", "manager"}:
                    raise SiteAccountError("角色只能是用户或普通管理员")
                if user["role"] != payload["role"]:
                    user["role"] = payload["role"]
                    self._revoke_user_sessions(data, user_id)
            if "password" in payload:
                if actor["role"] != "admin" and user["role"] != "user":
                    raise SiteAccountError("普通管理员只能重置用户的密码", 403)
                user["password_hash"] = encoded
                user["password_changed_at"] = timestamp_iso()
                user["password_source"] = "admin"
                user["password_version"] = int(user.get("password_version", 0)) + 1
                user["must_change_password"] = True
                self._revoke_user_sessions(data, user_id)
            if "note" in payload:
                if actor["role"] != "admin" and user["role"] != "user":
                    raise SiteAccountError("普通管理员只能修改用户的备注", 403)
                note = normalize_user_note(payload["note"])
                if note:
                    user["note"] = note
                else:
                    user.pop("note", None)
        return self._public(user, include_note=True)

    def change_password(self, user_id, current_password, new_password):
        validate_password_length(new_password)
        with self._lock:
            user = self._data["users"].get(user_id)
        if not user or not password_matches(current_password, user["password_hash"]):
            raise SiteAccountError("当前密码错误", 403)
        if new_password == current_password:
            raise SiteAccountError("新密码必须与当前密码不同")
        validate_new_password(new_password, user["username"], user.get("email"))
        encoded = password_hash(new_password)
        previous_hash = user["password_hash"]
        with self._edit() as data:
            user = data["users"].get(user_id)
            if not user or user["password_hash"] != previous_hash:
                raise SiteAccountError("账户凭据已更新，请重新登录", 409)
            user["password_hash"] = encoded
            user["password_changed_at"] = timestamp_iso()
            user["password_source"] = "user"
            user["password_version"] = int(user.get("password_version", 0)) + 1
            user["must_change_password"] = False
            self._revoke_user_sessions(data, user_id)

    def scan_initial_password(self, actor_id, password, *, usernames=None):
        if not isinstance(password, str) or not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise SiteAccountError("请输入待检测的初始密码")
        checked_at = timestamp_iso()
        with self._lock:
            self._administrator(self._data, actor_id, admin_only=True)
            candidates = [
                user for user in self._data["users"].values()
                if user["username"] != "admin" and user.get("password_source") != "user"
                and (usernames is None or user["username"] in usernames)
            ]
        matches = [user for user in candidates if password_matches(password, user["password_hash"])]
        with self._edit() as data:
            self._administrator(data, actor_id, admin_only=True)
            matched = []
            for candidate in matches:
                user = data["users"].get(candidate["id"])
                if not user or user["password_hash"] != candidate["password_hash"] or user.get("password_source") == "user":
                    continue
                user["must_change_password"] = True
                if user.get("password_source") == "legacy":
                    user["password_source"] = "initial"
                matched.append(self._public(user, include_note=True, include_status=True))
        return {"checked_at": checked_at, "matched_count": len(matched), "matched": matched}

    def _record_login_failure(self, user_id):
        if not user_id:
            return
        with self._edit() as data:
            user = data["users"].get(user_id)
            if user:
                user["last_failed_login_at"] = timestamp_iso()
                user["failed_login_count"] = int(user.get("failed_login_count", 0)) + 1

    def delete_user(self, actor_id, user_id):
        with self._edit() as data:
            self._administrator(data, actor_id, admin_only=True)
            user = data["users"].get(user_id)
            if not user:
                raise SiteAccountError("未找到网站账户", 404)
            if user["role"] == "admin":
                raise SiteAccountError("不能删除唯一的 admin 账户", 403)
            del data["users"][user_id]
            self._revoke_user_sessions(data, user_id)
            for group in data["groups"].values():
                group["member_ids"] = [member for member in group["member_ids"] if member != user_id]

    def login(self, username, password, totp_code=None, *, on_admin_failure=None):
        username = username.strip().lower() if isinstance(username, str) else ""
        with self._lock:
            user = next((user for user in self._data["users"].values() if user["username"] == username), None)

        def fail(reason):
            self._record_login_failure(user.get("id") if user else None)
            if on_admin_failure and user and user["role"] in ADMIN_ROLES:
                on_admin_failure(reason)

        # Do the same password derivation for unknown usernames without holding the store lock.
        encoded = user["password_hash"] if user else DUMMY_PASSWORD_HASH
        if not password_matches(password, encoded) or not user:
            fail("password_mismatch")
            return None
        with self._lock:
            user = self._data["users"].get(user["id"])
            if not user or user["password_hash"] != encoded:
                fail("credentials_changed")
                return None
            step = None
            if self._needs_mfa(user):
                secret = user.get("totp_secret")
                if not secret:
                    fail("totp_not_configured")
                    return None
                if totp_code is None or totp_code == "":
                    fail("totp_missing")
                    return None
                if not isinstance(totp_code, str) or not TOTP_PATTERN.fullmatch(totp_code):
                    fail("totp_bad_format")
                    return None
                totp = pyotp.TOTP(secret)
                current = int(time.time()) // totp.interval
                last = user.get("totp_last_step", -1)
                matches = [step for step in (current, current - 1, current + 1)
                           if hmac.compare_digest(totp.generate_otp(step), totp_code)]
                step = next((step for step in matches if step > last), None)
                if step is None:
                    fail("totp_replayed" if matches else "totp_mismatch")
                    return None
            token = secrets.token_urlsafe(32)
            upgraded_hash = None
            if encoded.startswith("pbkdf2_sha256$"):
                try:
                    upgraded_hash = password_hash(password)
                except SiteAccountError:
                    # A legacy password may still be valid under the previous limits.
                    pass
            with self._edit() as data:
                signed_in_user = data["users"][user["id"]]
                if upgraded_hash:
                    signed_in_user["password_hash"] = upgraded_hash
                signed_in_user["last_login_at"] = timestamp_iso()
                signed_in_user["failed_login_count"] = 0
                if step is not None:
                    signed_in_user["totp_last_step"] = step
                data["sessions"] = {key: value for key, value in data["sessions"].items() if value["expires_at"] > time.time()}
                data["sessions"][hashlib.sha256(token.encode()).hexdigest()] = {
                    "user_id": user["id"], "expires_at": int(time.time()) + SESSION_SECONDS,
                    "mfa_verified": step is not None,
                }
            return token, self._public(signed_in_user, include_reminder=True)

    def session_user(self, token):
        if not token or len(token) > 256:
            return None
        with self._lock:
            session = self._data["sessions"].get(hashlib.sha256(token.encode()).hexdigest())
            if not session or session["expires_at"] <= time.time():
                return None
            user = self._data["users"].get(session["user_id"])
            if not user or (self._needs_mfa(user) and not session.get("mfa_verified")):
                return None
            return self._public(user, include_reminder=True)

    def logout(self, token):
        with self._edit() as data:
            data["sessions"].pop(hashlib.sha256(token.encode()).hexdigest(), None)

    def list_groups(self, actor_id):
        with self._lock:
            self._administrator(self._data, actor_id)
            return deepcopy(list(self._data["groups"].values()))

    def user_groups(self, user_id):
        with self._lock:
            return [{"id": group["id"], "name": group["name"]} for group in self._data["groups"].values() if user_id in group["member_ids"]]

    def resource_allowed(self, user_id, resource_key):
        with self._lock:
            return any(user_id in group["member_ids"] and resource_key in group["resource_keys"] for group in self._data["groups"].values())

    def assign_resource_groups(self, actor_id, resource_key, group_ids, valid_resources):
        with self._edit() as data:
            self._administrator(data, actor_id)
            if not isinstance(resource_key, str) or resource_key not in valid_resources:
                raise SiteAccountError("请选择有效资源")
            if not isinstance(group_ids, list) or any(not isinstance(value, str) or value not in data["groups"] for value in group_ids):
                raise SiteAccountError("分配包含无效分组，请刷新后重试")
            selected = set(group_ids)
            for group in data["groups"].values():
                keys = group["resource_keys"]
                if group["id"] in selected:
                    if resource_key not in keys:
                        keys.append(resource_key)
                else:
                    group["resource_keys"] = [key for key in keys if key != resource_key]
        return deepcopy(list(data["groups"].values()))

    def save_group(self, actor_id, group_id, payload, valid_resources):
        with self._edit() as data:
            self._administrator(data, actor_id)
            current = data["groups"].get(group_id) if group_id else None
            if group_id and not current:
                raise SiteAccountError("未找到分组", 404)
            group = {**(current or {"id": secrets.token_hex(16), "member_ids": [], "resource_keys": []})}
            group.update({key: payload[key] for key in ("name", "member_ids", "resource_keys") if key in payload})
            name = group.get("name")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise SiteAccountError("分组名称须为 1–80 个字符")
            group["name"] = name.strip()
            if any(item["id"] != group["id"] and item["name"].casefold() == group["name"].casefold() for item in data["groups"].values()):
                raise SiteAccountError("分组名称已存在", 409)
            for field, allowed in (("member_ids", data["users"]), ("resource_keys", valid_resources)):
                if field not in payload:
                    continue
                values = payload[field]
                if not isinstance(values, list) or any(not isinstance(value, str) or value not in allowed for value in values):
                    raise SiteAccountError("分组包含无效成员或资源")
                group[field] = list(dict.fromkeys(values))
            group = {key: group[key] for key in ("id", "name", "member_ids", "resource_keys")}
            data["groups"][group["id"]] = group
        return deepcopy(group)

    def delete_group(self, actor_id, group_id):
        with self._edit() as data:
            self._administrator(data, actor_id)
            if group_id not in data["groups"]:
                raise SiteAccountError("未找到分组", 404)
            del data["groups"][group_id]
