"""Provision a privileged website account while MailAPI is stopped."""
import argparse
from getpass import getpass
import json
import sys
from pathlib import Path

from .site_accounts import SiteAccountError, SiteAccountStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--username", default="admin")
    parser.add_argument("--stdin", action="store_true", help="read password and totp_secret as JSON")
    args = parser.parse_args()
    try:
        if args.stdin:
            payload = json.load(sys.stdin)
            password, secret = payload["password"], payload["totp_secret"]
        else:
            password = getpass("New password: ")
            if password != getpass("Confirm password: "):
                parser.error("两次输入的密码不一致")
            secret = getpass("Authenticator Base32 secret: ")
        if not isinstance(secret, str):
            parser.error("验证器密钥必须是字符串")
        store = SiteAccountStore(args.data_dir / "site_accounts.json")
        store.provision_admin(args.username, password, secret)
    except SiteAccountError as error:
        parser.error(str(error))
    except (KeyError, TypeError, json.JSONDecodeError):
        parser.error("凭据输入格式无效")
    print("管理员凭据已更新，原会话已失效。")


if __name__ == "__main__":
    main()
