from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mail_providers.proton import ProtonMailbox, load_proton_credentials  # noqa: E402
from mailapi_shared.resource_database import ResourceDatabase  # noqa: E402


def load_account(name: str) -> dict:
    settings = ROOT / ".env"
    config = {}
    if settings.is_file():
        for line in settings.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.strip().removeprefix("export ").partition("=")
            if separator and key.strip() in {"MAILAPI_DATA_DIR", "MAILAPI_RESOURCE_BACKEND"}:
                config[key.strip()] = value.strip().strip("'\042")
    directory = ROOT / Path(os.getenv("MAILAPI_DATA_DIR", config.get("MAILAPI_DATA_DIR", "runtime"))).expanduser()
    backend = os.getenv("MAILAPI_RESOURCE_BACKEND", config.get("MAILAPI_RESOURCE_BACKEND", "json")).strip()
    path = directory / "gmail_accounts.json"
    if backend == "sqlite":
        value = ResourceDatabase(directory).load(path.name)
    elif path.is_file():
        value = json.loads(path.read_text(encoding="utf-8"))
    else:
        value = None
    if value is None:
        raise RuntimeError(f"邮箱账号配置不存在：{path}")
    items = value.get("items", []) if isinstance(value, dict) else value
    for item in items:
        if item.get("name") == name and item.get("provider") == "proton":
            return item
    raise RuntimeError(f"未找到 Proton 账号：{name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="登录 Proton 并保存本项目会话")
    parser.add_argument("--account", default="account-4", help="邮箱账号编号")
    parser.add_argument("--manual-captcha", action="store_true", help="手动处理 CAPTCHA")
    args = parser.parse_args()

    try:
        account = load_account(args.account)
        config_file = str(account.get("config_file") or "~/proton/.env")
        session_file = str(account.get("session_file") or "")
        if not session_file:
            raise RuntimeError("Proton 账号缺少 session_file")
        credentials = load_proton_credentials(config_file)
        if credentials.username.lower() != str(account.get("email") or "").lower():
            raise RuntimeError("Proton 配置邮箱与账号记录不一致")
        ProtonMailbox(str(ROOT / session_file)).login_fresh(
            credentials,
            manual_captcha=args.manual_captcha,
        )
    except Exception as error:  # noqa: BLE001
        print(f"Proton 登录失败：{error}", file=sys.stderr)
        return 1

    print(f"Proton 登录成功，会话已保存：{session_file}")
    print("请重启 code-receive.py 以启动账号监听。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
