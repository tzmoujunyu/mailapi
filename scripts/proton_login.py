from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mail_providers.proton import ProtonMailbox, load_proton_credentials  # noqa: E402


def load_account(name: str) -> dict:
    path = ROOT / "runtime" / "gmail_accounts.json"
    if not path.exists():
        raise RuntimeError(f"邮箱账号配置不存在：{path}")
    value = json.loads(path.read_text(encoding="utf-8"))
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
