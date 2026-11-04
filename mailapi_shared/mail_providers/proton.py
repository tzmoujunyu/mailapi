from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


class ProtonDependencyError(RuntimeError):
    pass


class ProtonAuthorizationRequired(RuntimeError):
    pass


@dataclass(frozen=True)
class ProtonCredentials:
    username: str
    password: str
    totp_secret: str = ""
    login_type: str = "web"


def _read_env_file(path: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    env_path = Path(path).expanduser()
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def load_proton_credentials(config_file: str) -> ProtonCredentials:
    values = _read_env_file(config_file)

    def configured(name: str, default: str = "") -> str:
        environment_value = os.getenv(name, "").strip()
        return environment_value or values.get(name, default).strip()

    credentials = ProtonCredentials(
        username=configured("PROTON_USERNAME"),
        password=configured("PROTON_PASSWORD"),
        totp_secret=configured("PROTON_TOTP_SECRET"),
        login_type=configured("PROTON_LOGIN_TYPE", configured("LOGIN_TYPE", "web")).lower(),
    )
    if not credentials.username:
        raise ProtonAuthorizationRequired("Proton 配置缺少 PROTON_USERNAME")
    if not credentials.password:
        raise ProtonAuthorizationRequired("Proton 配置缺少 PROTON_PASSWORD")
    if credentials.login_type not in {"web", "dev"}:
        raise ValueError("PROTON_LOGIN_TYPE 必须是 web 或 dev")
    return credentials


class _TotpProvider:
    def __init__(self, secret: str) -> None:
        try:
            import pyotp
        except ImportError as error:
            raise ProtonDependencyError("缺少 pyotp，请安装 Proton 运行依赖") from error
        self._totp = pyotp.TOTP(secret)
        self._last: Optional[str] = None

    def __call__(self) -> str:
        code = self._totp.now()
        if code == self._last:
            wait = self._totp.interval - (time.time() % self._totp.interval) + 1
            time.sleep(wait)
            code = self._totp.now()
        self._last = code
        return code


class ProtonMailbox:
    def __init__(self, session_file: str) -> None:
        self.session_file = str(Path(session_file).expanduser())
        self.client: Any = None

    @staticmethod
    def _types() -> tuple:
        try:
            from protonmail import ProtonMail
            from protonmail.models import CaptchaConfig, LoginType
        except ImportError as error:
            raise ProtonDependencyError(
                "缺少 protonmail-api-client，请在 gmail conda 环境安装依赖"
            ) from error
        return ProtonMail, LoginType, CaptchaConfig

    def _protect_session_file(self) -> None:
        try:
            os.chmod(self.session_file, 0o600)
        except OSError:
            pass

    def connect(self) -> Any:
        if not os.path.exists(self.session_file):
            raise ProtonAuthorizationRequired("Proton 会话不存在，请在账号控制台登录")
        ProtonMail, _, _ = self._types()
        try:
            client = ProtonMail()
            client.load_session(self.session_file, auto_save=True)
            client.get_user_info()
        except Exception as error:
            raise ProtonAuthorizationRequired(
                "Proton 会话已失效，请在账号控制台重新登录"
            ) from error
        self.client = client
        self._protect_session_file()
        return client

    def login_fresh(
        self,
        credentials: ProtonCredentials,
        manual_captcha: bool = False,
    ) -> Any:
        ProtonMail, LoginType, CaptchaConfig = self._types()
        session_path = Path(self.session_file)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        login_type = LoginType.DEV if credentials.login_type == "dev" else LoginType.WEB

        def missing_totp() -> str:
            raise ProtonAuthorizationRequired("Proton 请求两步验证码，但未配置 TOTP secret")

        getter = _TotpProvider(credentials.totp_secret) if credentials.totp_secret else missing_totp
        client = ProtonMail()
        login_options: Dict[str, Any] = {
            "getter_2fa_code": getter,
            "login_type": login_type,
        }
        if manual_captcha:
            login_options["captcha_config"] = CaptchaConfig(
                type=CaptchaConfig.CaptchaType.MANUAL
            )
        client.login(credentials.username, credentials.password, **login_options)
        client.save_session(self.session_file)
        self.client = client
        self._protect_session_file()
        return client

    def get_recent_messages(self, limit: int = 30) -> List[Any]:
        if self.client is None:
            raise ProtonAuthorizationRequired("Proton 尚未连接")
        return list(self.client.get_messages(label_or_id="0"))[:limit]

    def read_message(self, message: Any) -> Any:
        if self.client is None:
            raise ProtonAuthorizationRequired("Proton 尚未连接")
        return self.client.read_message(message, mark_as_read=False)

    def mark_as_read(self, message_id: str) -> None:
        if self.client is None:
            raise ProtonAuthorizationRequired("Proton 尚未连接")
        self.client.mark_messages_as_read([message_id])

    def wait_for_new_message(self, interval: int, timeout: int) -> Any:
        if self.client is None:
            raise ProtonAuthorizationRequired("Proton 尚未连接")
        return self.client.wait_for_new_message(
            interval=interval,
            timeout=timeout,
            read_message=False,
        )
