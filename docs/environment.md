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

服务运行不依赖 Node.js。Node.js 用于前端测试，以及 `frontend/` 中的编辑器静态包构建和 DOM 测试。

## Python 依赖

生产和测试服务都安装 `requirements.txt`，开发和测试环境再安装 `requirements-dev.txt`。

`requirements.txt` 当前包含 FastAPI、Uvicorn、Google API/OAuth、MSAL、Proton API client、python-multipart、pyotp、Pillow 和 pillow-heif。`requirements-dev.txt` 当前额外包含 httpx，用于 FastAPI `TestClient`。

Codex 图文提示需要 Pillow 的 WebP/AVIF 支持及 pillow-heif 的 HEIC 解码支持。本次验证版本为 Pillow 13.0.0、pillow-heif 1.8.1，
macOS arm64 和 Linux x86_64 均使用预编译 wheel，无需另建 Node.js 运行服务。Tiptap 和 PhotoSwipe 静态文件已包含在仓库中。
构建编辑器依赖及运行其 DOM 测试时，在本地执行 `npm --prefix frontend ci --ignore-scripts`，再运行
`npm --prefix frontend run build` 和 `npm --prefix frontend test`。依赖已锁定；本次验证的 Node.js 为 24.14.0。

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

项目目前没有完整依赖 lockfile，迁移验证后可以保存目标环境的 `python -m pip freeze` 作为版本记录。

## 启动入口

| 模式 | 命令 | 默认端口 | 数据行为 |
| --- | --- | --- | --- |
| 正式版 | `python code-receive.py` | 8000 | 使用 `runtime/` |
| 测试版 | `python code-receive-test.py` | 8001 | 默认复制正式运行数据到临时目录 |

两版默认监听 `127.0.0.1`。正式版监听其他地址时需要配置可信反代，测试版只允许回环地址。

每个端口只运行一个服务进程。启动前检查旧进程，启动后检查端口：

```bash
pgrep -af 'code-receive(-test)?\.py'
ss -ltnp | grep -E ':(8000|8001)\b'
```

## 配置文件和环境变量

### 正式版 `.env`

正式进程停止后，从项目根目录运行 `python -m mailapi_shared.admin_credentials --data-dir runtime`。
命令会交互式读取 8–128 字符的密码和 32 字符的随机 Base32 TOTP 密钥。将同一密钥录入验证器
的基于时间条目。若设置了 `MAILAPI_DATA_DIR`，命令应指向相同目录。
配置其他普通管理员时加 `--username 用户名`。配置会撤销目标账号的已有会话。

| 配置 | 作用 |
| --- | --- |
| `PORT`、`MAILAPI_HOST` | 正式网站端口和监听地址 |
| `MAILAPI_TRUSTED_PROXY_IPS` | 反代连接源站时使用的确切 IP，逗号分隔 |
| `MAILAPI_DATA_DIR` | 正式运行数据目录，默认 `runtime` |
| `MAILAPI_EXTERNAL_WATCHERS` | 外部邮箱和 Codex 监听，默认开启 |
| `APP_BASE_URL` | 网站基地址，通过反代公开时填写 HTTPS 域名 |
| `GMAIL_OAUTH_REDIRECT_BASE` | Gmail OAuth 回调基地址 |
| `CODEX_OAUTH_REDIRECT_BASE`、`CODEX_LOGIN_ADDR` | Codex OAuth 回调配置 |
| `MAIL_RELAY_SECRET_FILE` | 邮件中继密钥文件 |
| `OUTLOOK_CLIENT_ID`、`OUTLOOK_CLIENT_SECRET`、`OUTLOOK_TENANT`、`OUTLOOK_OAUTH_REDIRECT_URI` | Outlook Graph OAuth |
| `PROTON_CONFIG_FILE` | Proton 配置文件路径 |

公网反代需保留站点 `Host`，覆盖客户端传入的 `X-Forwarded-Proto` 和 `X-Forwarded-For`，
分别传入 `https` 与代理确认的客户端地址。源站网络层应只对反代开放服务端口，反代访问日志应省略
OAuth 回调的查询参数。应用登录请求上限为 8 KiB，一般请求体上限为 1 MiB。

### 测试版 `.env.test`

可以从 `.env.test.example` 复制开始。

| 配置 | 作用 |
| --- | --- |
| `TEST_ADMIN_PASSWORD` | 测试版 admin 初始化口令 |
| `TEST_PORT`、`TEST_HOST` | 测试网站端口和回环地址 |
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
| `runtime/codex_tips.json`、`runtime/codex_tip_images/` | Codex 图文提示及其 WebP 图片，必须配套备份 | 目录 700，文件 600 |
| `runtime/mail_relay_secret` | 邮件中继共享密钥 | 600 |
| Proton 配置和 session 文件 | Proton 登录和会话 | 仅复制到需要 Proton 的机器 |

正式运行数据至少包括 `gmail_accounts.json`、`codex_accounts.json`、`account_credentials.json`、`site_accounts.json`，以及 token、auth、历史和日志目录。

测试版默认从正式 `runtime/` 创建临时快照，但会跳过正式账户 JSON、SQLite 及其日志文件、正式中继密钥和 `runtime/test/`。测试账号和分组不是正式迁移数据。

### 网站账户迁移到 SQLite

先停写，备份运行目录、配置和目录外授权文件，并记录代码提交。把备份恢复到私有的独立目录，使用对应的 Python 环境执行迁移。

```bash
python -m mailapi_shared.site_database --data-dir /path/to/isolated-data
TEST_DATA_DIR=/path/to/isolated-data TEST_SITE_ACCOUNTS_BACKEND=sqlite TEST_EXTERNAL_WATCHERS=0 python code-receive-test.py
```

迁移命令创建权限为 600 的 `site_accounts.sqlite3`，保留源 JSON，已有目标库时拒绝覆盖。核对登录、分组权限和授权读取后，停止正式服务，对最新正式数据重新执行迁移。正式 `.env` 中设置 `MAILAPI_SITE_ACCOUNTS_BACKEND=sqlite` 并重启。SQLite 仍使用单进程和本机磁盘。离线配置管理员时为 `admin_credentials` 加上 `--backend sqlite`。

切换前的 JSON 可用于上线检查失败时回退。正式 SQLite 已经接受写入后，旧 JSON 不会包含这些更新，回退前应先保全并核对 SQLite 新数据。后续数据库备份应在服务停写时复制，或使用 SQLite Backup API 取得一致副本，并与其他运行文件和密钥配套保存。

### 资源元数据迁移到 SQLite

停写后备份 `runtime/`、配置和目录外授权文件，再在隔离副本演练。

```bash
python -m mailapi_shared.resource_database --data-dir /path/to/isolated-data
TEST_DATA_DIR=/path/to/isolated-data TEST_RESOURCE_BACKEND=sqlite TEST_EXTERNAL_WATCHERS=0 python code-receive-test.py
```

离线迁移校验资源 ID、顺序和导入结果，生成 `resources.sqlite3` 和 `resource_secrets/`，原 JSON 保留为切换前基线。正式版迁移使用停写后的实时数据，在 `.env` 设置 `MAILAPI_RESOURCE_BACKEND=sqlite` 后启动。网站账户数据库独立切换。审核和恢复点沿用原事件 ID 与恢复密钥。SQLite 事务引用已落盘的 JSON 凭据版本，提交后清理旧版本。

备份和恢复时须配套保存资源库、`resource_secrets/`、`account_credentials.restore.key` 以及授权和图片文件。切换后如需回退，停写后用 `python -m mailapi_shared.resource_database --data-dir runtime --export-to /path/to/empty-private-directory` 重建当前版本的五份资源 JSON，再替换对应文件并切回 JSON 后端。已切换后产生的更新只会写入新存储。

### 测试版凭据加密演练

使用项目目录和正式数据目录以外的私有目录。源目录应是使用两个 SQLite 后端的独立测试副本，演练时先停止测试进程。准备两个独立的测试密钥文件，由服务用户持有，权限设为 `0600`，放在项目和数据目录外。密钥文件是 JSON，`namespace` 标识测试环境，`active` 是当前密钥 ID，`keys` 按密钥 ID 保存 Base64 编码的 32 字节随机密钥。轮换使用相同的 `namespace`、新的 ID 和新生成的密钥。

以下命令中的路径需替换为隔离测试目录与密钥的绝对路径。各目标目录须尚未存在。

```bash
python -m mailapi_shared.test_credential_storage migrate \
  --source /path/test-source --destination /path/test-v1 --key-file /keys/test-v1.json
python -m mailapi_shared.test_credential_storage backup \
  --source /path/test-v1 --destination /path/test-backup --key-file /keys/test-v1.json
python -m mailapi_shared.test_credential_storage rotate \
  --source /path/test-v1 --destination /path/test-v2 \
  --old-key-file /keys/test-v1.json --key-file /keys/test-v2.json
python -m mailapi_shared.test_credential_storage restore \
  --source /path/test-backup --destination /path/test-restored --key-file /keys/test-v1.json
```

启动测试版时设置 `TEST_DATA_DIR` 为选中的目录、`TEST_CREDENTIAL_KEY_FILE` 为对应的密钥路径，同时设置 `TEST_SITE_ACCOUNTS_BACKEND=sqlite`、`TEST_RESOURCE_BACKEND=sqlite` 和 `TEST_EXTERNAL_WATCHERS=0`。加密版拒绝明文 JSON 导出。指定测试目录时，演练结束后需清理临时测试库和测试密钥。

备份包括两个 SQLite 库及配套的运行文件，例如 `resource_secrets/`、OAuth 文件、图文和已有 OAuth 恢复点的 Fernet 密钥。配置与数据目录外的授权文件应单独备份。SQLite 加密密钥单独保管，并在旧备份的保留期内保存对应版本。回滚前保留较新的数据副本，再启动已恢复的目录。恢复后的数据对应备份时点，之后的修改仍保存在较新的副本中。迁移前的 SQLite 和 JSON 副本仍按含明文凭据的数据保护。

### 正式版小型凭据加密

正式密钥文件额外设置 `"environment": "production"`，生成独立的随机密钥，放在项目、运行目录和备份目录外。备份正式配置、运行目录和目录外授权文件，先从独立副本恢复验证，再停写制作最终备份。对备份恢复的副本执行上节命令，加上 `--production-key`，并完成检验后切换。切换时替换两个 SQLite 库和对应的 `resource_secrets/`，同时保留原 OAuth 文件和已有 OAuth 恢复点的 Fernet 密钥。确认旧 JSON 已归档到受保护的切换前备份。正式配置中设置 `MAILAPI_CREDENTIAL_KEY_FILE` 和两个 SQLite 后端。加密后启动测试版须显式指定测试数据目录。

若切换后需回退，先停写并保留当前加密库、凭据目录和当前密钥，再恢复切换前的代码、配置和数据库。备份之后的更新仍在切换后的副本中，应核对后再决定如何补回。

### 隔离测试版 OAuth 加密演练

沿用上述独立测试目录和密钥要求。停止测试进程后，对已启用小型凭据加密的测试副本执行下列命令。邮箱记录中的令牌路径应指向该副本内的 `gmail_tokens/`、`outlook_tokens/` 或旧版 `token_accountN.json`。迁移会保存相对路径并逐个核对解密结果。

```bash
python -m mailapi_shared.test_credential_storage encrypt-oauth \
  --source /path/test-source --destination /path/test-oauth --key-file /keys/test.json
python -m mailapi_shared.test_credential_storage backup --oauth \
  --source /path/test-oauth --destination /path/test-backup --key-file /keys/test.json
python -m mailapi_shared.test_credential_storage restore --oauth \
  --source /path/test-backup --destination /path/test-restored --key-file /keys/test.json
```

如果源副本仍是旧版 SQLite 明文授权布局，可用 `migrate --oauth` 一次迁移。密钥轮换用 `rotate --oauth`，附上 `--old-key-file` 和新密钥路径。启动时在原测试配置基础上设 `TEST_OAUTH_ENCRYPTION=1`、`TEST_DATA_DIR=/path/test-oauth`。备份和恢复须保留相同的相对目录结构，密钥另行保存。

迁移保留 OAuth token 和 MSAL 缓存内容。服务商可撤销令牌，或者在续期时使旧 refresh token 失效，因此恢复旧备份时仍要核对当时的令牌是否有效。演练用合成凭据验证读取、模拟续期和加密写回。Google OAuth 客户端配置、Outlook 应用凭据和 Proton 文件沿用现有管理方式。

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
