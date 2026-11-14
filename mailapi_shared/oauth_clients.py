"""One authenticated file for the two OAuth application configurations."""
import argparse
import json
import os
from pathlib import Path

from .configuration import dotenv_entries
from .credential_crypto import CredentialCryptoError, CredentialKeyring
from .oauth_files import OAuthFiles


FILENAME = "oauth_clients.json"
OUTLOOK_FIELDS = ("OUTLOOK_CLIENT_ID", "OUTLOOK_CLIENT_SECRET", "OUTLOOK_TENANT",
                  "OUTLOOK_AUTHORITY", "OUTLOOK_OAUTH_REDIRECT_URI")


def google_client(document):
    return document["google"].get("web") or document["google"].get("installed")


def gmail_token_info(document, token):
    client = google_client(document)
    if token.get("client_id") != client["client_id"]:
        raise CredentialCryptoError("现有 Gmail 授权的 client ID 与集中配置不一致")
    return {**token, "client_secret": client["client_secret"]}


def load_clients(storage):
    try:
        document = storage.read_json(storage.directory / FILENAME)
        outlook = document["outlook"]
        client = google_client(document)
        if (not all(isinstance(client.get(key), str) and client[key]
                    for key in ("client_id", "client_secret", "auth_uri", "token_uri"))
                or not all(isinstance(outlook.get(key), str) and outlook[key].strip()
                           for key in OUTLOOK_FIELDS[:2])):
            raise ValueError
        return document
    except (OSError, AttributeError, KeyError, TypeError, ValueError):
        raise CredentialCryptoError("无法读取集中 OAuth 客户端配置，请检查密钥及 Google、Outlook 配置") from None


def migrate_clients(storage, google_file, outlook_settings):
    path = storage.directory / FILENAME
    if path.exists() or path.is_symlink():
        raise CredentialCryptoError("集中 OAuth 客户端配置已存在")
    document = {
        "google": json.loads(Path(google_file).read_bytes()),
        "outlook": {key: outlook_settings[key] for key in OUTLOOK_FIELDS if key in outlook_settings},
    }
    storage.write_json(path, document)
    try:
        if load_clients(storage) != document:
            raise CredentialCryptoError("集中 OAuth 客户端配置校验失败")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--google-file", type=Path, required=True)
    parser.add_argument("--outlook-env", type=Path, required=True)
    parser.add_argument("--production-key", action="store_true")
    args = parser.parse_args()
    try:
        keyring = CredentialKeyring(args.key_file, data_dirs=(args.data_dir,), production=args.production_key)
        env = dict(dotenv_entries(args.outlook_env))
        env.update({key: os.environ[key] for key in OUTLOOK_FIELDS if key in os.environ})
        migrate_clients(OAuthFiles(args.data_dir, keyring), args.google_file, env)
    except (OSError, TypeError, ValueError):
        parser.exit(1, "集中 OAuth 客户端配置迁移失败，请检查输入文件与密钥。\n")
    print("Google 和 Outlook 客户端配置已加密并核对。")


if __name__ == "__main__":
    main()
