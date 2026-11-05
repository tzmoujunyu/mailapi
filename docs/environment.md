# MailAPI 迁移环境清单

这份清单用于把项目迁移到另一台本地电脑或服务器。当前验证基线是 Python 3.14.5。开发机为 macOS arm64，服务器为 Linux x86_64。迁移时应保持 Python 主版本、依赖和环境变量一致。

## 运行时和工具

| 项目 | 用途 | 当前验证值 |
| --- | --- | --- |
| Python | 服务、授权脚本和 Python 测试 | 3.14.5 |
| `uv` | 本地虚拟环境和测试 | 0.11.0 |
| Conda | 服务器环境管理 | 服务器使用 `gmail` 环境 |
| Uvicorn | FastAPI 服务 | 来自 `requirements.txt` |
| Node.js | 前端测试 | 24.14.0，仅测试需要 |
| npm | Node 工具检查 | 11.9.0，仅测试需要 |

服务运行不依赖 Node.js。只有运行 `tests/test_account_ui.cjs` 时才需要 Node.js。

## Python 依赖

生产和测试服务都安装 `requirements.txt`，开发和测试环境再安装 `requirements-dev.txt`。

`requirements.txt` 当前包含 FastAPI、Uvicorn、Google API/OAuth、MSAL、Proton API client、python-multipart 和 pyotp。`requirements-dev.txt` 当前额外包含 httpx，用于 FastAPI `TestClient`。

本地使用 `uv` 时：

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-dev.txt
```

服务器使用 Conda 时：

```bash
conda create -n mailapi python=3.14
conda activate mailapi
python -m pip install -r requirements.txt -r requirements-dev.txt
```

当前服务器环境路径是 `/home/moujy/.conda/envs/gmail`。项目目前没有完整依赖 lockfile，迁移验证后可以保存目标环境的 `python -m pip freeze` 作为版本记录。

## 启动入口

| 模式 | 命令 | 默认端口 | 数据行为 |
| --- | --- | --- | --- |
| 正式版 | `python code-receive.py` | 8000 | 使用 `runtime/` |
| 测试版 | `python code-receive-test.py` | 8001 | 默认复制正式运行数据到临时目录 |

正式版默认监听 `0.0.0.0`，测试版默认监听 `127.0.0.1`。可分别通过 `MAILAPI_HOST` 和 `TEST_HOST` 调整。

每个端口只运行一个服务进程。启动前检查旧进程，启动后检查端口：

```bash
pgrep -af 'code-receive(-test)?\.py'
ss -ltnp | grep -E ':(8000|8001)\b'
```

## 配置文件和环境变量

### 正式版 `.env`

首次网站账号初始化至少需要 `ADMIN_ACCESS_PASSWORD`，已有旧部署还应保留 `ACCESS_PASSWORD`。

| 配置 | 作用 |
| --- | --- |
| `ACCESS_PASSWORD` | 旧版访问口令兼容 |
| `ADMIN_ACCESS_PASSWORD` | 正式版 admin 初始化和旧管理员口令 |
| `PORT`、`MAILAPI_HOST` | 正式网站端口和监听地址 |
| `MAILAPI_DATA_DIR` | 正式运行数据目录，默认 `runtime` |
| `MAILAPI_EXTERNAL_WATCHERS` | 外部邮箱和 Codex 监听，默认开启 |
| `APP_BASE_URL` | 网站公网基地址 |
| `GMAIL_OAUTH_REDIRECT_BASE` | Gmail OAuth 回调基地址 |
| `CODEX_OAUTH_REDIRECT_BASE`、`CODEX_LOGIN_ADDR` | Codex OAuth 回调配置 |
| `MAIL_RELAY_SECRET_FILE` | 邮件中继密钥文件 |
| `OUTLOOK_CLIENT_ID`、`OUTLOOK_CLIENT_SECRET`、`OUTLOOK_TENANT`、`OUTLOOK_OAUTH_REDIRECT_URI` | Outlook Graph OAuth |
| `PROTON_CONFIG_FILE` | Proton 配置文件路径 |

### 测试版 `.env.test`

可以从 `.env.test.example` 复制开始。

| 配置 | 作用 |
| --- | --- |
| `TEST_ADMIN_PASSWORD` | 测试版 admin 初始化口令 |
| `TEST_PORT`、`TEST_HOST` | 测试网站端口和监听地址 |
| `TEST_DATA_DIR` | 独立测试数据目录，不设置时使用临时目录 |
| `TEST_EXTERNAL_WATCHERS` | 是否连接真实邮箱和 Codex，默认关闭 |
| `TEST_APP_BASE_URL`、`TEST_GMAIL_OAUTH_REDIRECT_BASE` | 测试版网站和 Gmail 回调地址 |
| `TEST_CODEX_LOGIN_ADDR` | 测试版 Codex 回调地址 |
| `TEST_PROTON_CONFIG_FILE` | 测试版 Proton 配置路径 |
| `TEST_GMAIL_ACCOUNTS` | 测试版没有账号文件时的可选账号配置 |

测试版不读取正式版网站登录口令。测试外部监听时必须使用独立邮箱、OAuth 回调和授权文件。

## 必须迁移的文件

代码仓库不包含以下文件，迁移时需要通过安全方式单独复制：

| 文件或目录 | 用途 | 权限建议 |
| --- | --- | --- |
| `.env`、`.env.test` | 正式和测试配置 | 600 |
| `credentials.json` | Google OAuth 客户端配置 | 600 |
| `runtime/` | 邮箱、Codex、账号、凭据、历史和日志 | 目录 700，文件 600 |
| `runtime/gmail_tokens/` | Gmail OAuth token | 目录 700，文件 600 |
| `runtime/outlook_tokens/` | Outlook OAuth token | 目录 700，文件 600 |
| `runtime/codex_auth/` | Codex 授权文件 | 目录 700，文件 600 |
| `runtime/mail_relay_secret` | 邮件中继共享密钥 | 600 |
| Proton 配置和 session 文件 | Proton 登录和会话 | 仅复制到需要 Proton 的机器 |

正式运行数据至少包括 `gmail_accounts.json`、`codex_accounts.json`、`account_credentials.json`、`site_accounts.json`，以及 token、auth、历史和日志目录。

测试版默认从正式 `runtime/` 创建临时快照，但会跳过正式 `site_accounts.json`、正式中继密钥和 `runtime/test/`。测试账号和分组不是正式迁移数据。

## 可选外部服务

| 功能 | 额外条件 |
| --- | --- |
| Gmail | `credentials.json`、Google Cloud OAuth 客户端和可访问 Google 的网络 |
| Outlook | Microsoft Graph 应用、客户端密钥、HTTPS 回调地址和 `msal` |
| Codex | OAuth/用量接口网络和回调端口 1455 或自定义地址 |
| Proton | `protonmail-api-client`、Proton 配置，必要时手工 CAPTCHA 登录 |
| 本地邮件中继 | 发送端配置、目标 URL 和双方一致的中继密钥 |

只做网站账号、资源权限和页面开发时，测试版保持 `TEST_EXTERNAL_WATCHERS=0` 即可。

## 迁移后的检查

在目标机器项目根目录执行：

```bash
python -m py_compile mailapi_shared/*.py test_app/*.py code-receive.py code-receive-test.py
python -m unittest discover -s tests -q
node --test tests/test_account_ui.cjs
MAILAPI_UI_ROOT=mailapi_shared/static node --test tests/test_account_ui.cjs
```

然后先启动测试版，确认 `/login`、`/api/auth/login`、`/api/auth/me` 和 `/api/accounts`。正式版启动后再确认相同接口，并确认正式 `runtime/` 没有被测试目录覆盖。

## 同步边界

代码同步可以覆盖 Python、静态资源、测试、README 和 `docs/`。同步时排除 `.env`、`.env.test`、`runtime/`、`credentials.json`、虚拟环境和 `__pycache__`。推荐先验证测试版，再提交 Git 检查点，最后同步代码并单独处理目标机器的运行进程。
