"""Website identities, sessions and group grants for both app modes."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time


SESSION_SECONDS = 86400
PASSWORD_ITERATIONS = 600_000
ADMIN_ROLES = {"manager", "admin"}


class SiteAccountError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def password_hash(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 256:
        raise SiteAccountError("密码长度须为 8–256 个字符")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest.hex()}"


def password_matches(password, encoded):
    if not isinstance(password, str) or len(password) > 256:
        return False
    algorithm, iterations, salt, digest = encoded.split("$", 3)
    if algorithm != "pbkdf2_sha256":
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations))
    return hmac.compare_digest(actual.hex(), digest)


class SiteAccountStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data = {"version": 1, "users": {}, "groups": {}, "sessions": {}}
        if self.path.exists():
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            if self._data.get("version") != 1 or any(
                not isinstance(self._data.get(key), dict) for key in ("users", "groups", "sessions")
            ):
                raise SiteAccountError("网站账号存储格式无效")
            admins = [user for user in self._data["users"].values() if user["role"] == "admin"]
            if len(admins) != 1 or admins[0]["username"] != "admin":
                raise SiteAccountError("网站账号存储必须包含唯一的 admin")
            os.chmod(self.path, 0o600)

    @contextmanager
    def _edit(self):
        with self._lock:
            data = deepcopy(self._data)
            yield data
            self.path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
            descriptor, temporary = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(data, output, ensure_ascii=False, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
                self._data = data
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    @staticmethod
    def _public(user):
        return {key: user[key] for key in ("id", "username", "role", "created_at")}

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
            encoded = password_hash(password)
            with self._edit() as data:
                user_id = secrets.token_hex(16)
                data["users"][user_id] = {
                    "id": user_id, "username": "admin", "role": "admin",
                    "password_hash": encoded, "created_at": int(time.time()),
                }

    def list_users(self, actor_id):
        with self._lock:
            self._administrator(self._data, actor_id)
            return [self._public(user) for user in self._data["users"].values()]

    def get_user(self, user_id):
        with self._lock:
            user = self._data["users"].get(user_id)
            return self._public(user) if user else None

    def create_user(self, actor_id, username, password, role="user"):
        username = username.strip().lower() if isinstance(username, str) else ""
        if not re.fullmatch(r"[a-z0-9_.-]{3,64}", username):
            raise SiteAccountError("用户名须为 3–64 位字母、数字、下划线、点或短横线")
        if username == "admin" or not isinstance(role, str) or role not in {"user", "manager"}:
            raise SiteAccountError("admin 为唯一保留账号，新增角色只能是用户或普通管理员")
        with self._edit() as data:
            self._administrator(data, actor_id)
            if any(user["username"] == username for user in data["users"].values()):
                raise SiteAccountError("用户名已存在", 409)
            user_id = secrets.token_hex(16)
            user = {
                "id": user_id, "username": username, "role": role,
                "password_hash": password_hash(password), "created_at": int(time.time()),
            }
            data["users"][user_id] = user
        return self._public(user)

    @staticmethod
    def _revoke_user_sessions(data, user_id):
        data["sessions"] = {
            key: session for key, session in data["sessions"].items() if session["user_id"] != user_id
        }

    def update_user(self, actor_id, user_id, payload):
        with self._edit() as data:
            actor = self._administrator(data, actor_id)
            user = data["users"].get(user_id)
            if not user:
                raise SiteAccountError("未找到网站账号", 404)
            if user["role"] == "admin":
                raise SiteAccountError("admin 账号固定；请通过修改本人密码更新其密码", 403)
            if "role" in payload:
                self._administrator(data, actor_id, admin_only=True)
                if not isinstance(payload["role"], str) or payload["role"] not in {"user", "manager"}:
                    raise SiteAccountError("角色只能是用户或普通管理员")
                user["role"] = payload["role"]
            if "password" in payload:
                if actor["role"] != "admin" and user["role"] != "user":
                    raise SiteAccountError("普通管理员只能重置用户的密码", 403)
                user["password_hash"] = password_hash(payload["password"])
                self._revoke_user_sessions(data, user_id)
        return self._public(user)

    def change_password(self, user_id, current_password, new_password):
        with self._edit() as data:
            user = data["users"].get(user_id)
            if not user or not password_matches(current_password, user["password_hash"]):
                raise SiteAccountError("当前密码错误", 403)
            user["password_hash"] = password_hash(new_password)
            self._revoke_user_sessions(data, user_id)

    def delete_user(self, actor_id, user_id):
        with self._edit() as data:
            self._administrator(data, actor_id, admin_only=True)
            user = data["users"].get(user_id)
            if not user:
                raise SiteAccountError("未找到网站账号", 404)
            if user["role"] == "admin":
                raise SiteAccountError("不能删除唯一的 admin 账号", 403)
            del data["users"][user_id]
            self._revoke_user_sessions(data, user_id)
            for group in data["groups"].values():
                group["member_ids"] = [member for member in group["member_ids"] if member != user_id]

    def login(self, username, password):
        username = username.strip().lower() if isinstance(username, str) else ""
        with self._lock:
            user = next((user for user in self._data["users"].values() if user["username"] == username), None)
            # Do the same password derivation for unknown usernames.
            encoded = user["password_hash"] if user else f"pbkdf2_sha256${PASSWORD_ITERATIONS}$" + "0" * 32 + "$" + "0" * 64
            matched = password_matches(password, encoded)
            if not user or not matched:
                return None
            token = secrets.token_urlsafe(32)
            with self._edit() as data:
                data["sessions"] = {key: value for key, value in data["sessions"].items() if value["expires_at"] > time.time()}
                data["sessions"][hashlib.sha256(token.encode()).hexdigest()] = {
                    "user_id": user["id"], "expires_at": int(time.time()) + SESSION_SECONDS,
                }
            return token, self._public(user)

    def session_user(self, token):
        if not token or len(token) > 256:
            return None
        with self._lock:
            session = self._data["sessions"].get(hashlib.sha256(token.encode()).hexdigest())
            if not session or session["expires_at"] <= time.time():
                return None
            return self.get_user(session["user_id"])

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
                values = group[field]
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
