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

保留原界面的正式服务安装 `requirements.txt`。启用新版工作台、财务和 API 时安装
`requirements-workspace.txt`；开发和测试环境安装 `requirements-dev.txt`，其中已包含工作台依赖。

`requirements.txt` 当前包含 FastAPI、Uvicorn、Google API/OAuth、MSAL、python-multipart、pyotp、Pillow 和 pillow-heif。
`requirements-workspace.txt` 另包含 httpx、Excel 导出所需的 openpyxl，以及发票 PDF/OCR 所需的
pypdfium2、RapidOCR 和 ONNX Runtime。切换前须在目标服务器的独立 Conda 环境检查安装与导入，不能仅凭本地测试判断服务器已经具备依赖。

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
| `MAILAPI_WORKSPACE_ENABLED` | 正式模式显式设为 `1` 后启用新版工作台、财务和 API；默认不启用 |
| `MAILAPI_BACKUP_ENABLED` | 正式环境设为 `1`，并指定匹配的正式备份配置后才启用定期备份 |
| `MAILAPI_BACKUP_CONFIG` | 私有备份配置绝对路径；正式配置须声明 `environment: production`、`production_enabled: true`，不得使用测试样例 |
| `MAILAPI_EXTERNAL_WATCHERS` | 外部邮箱和 Codex 监听，默认开启 |
| `MAILAPI_AUTH_ARTIFACT_CLEANUP` | 设为 `1` 后正式版启动时清理授权归档和旧恢复点，要求启用 OAuth 加密 |
| `MAILAPI_AUTH_ARTIFACT_KEEP_HISTORY`、`MAILAPI_AUTH_ARTIFACT_MAX_AGE_DAYS` | 正式版授权归档与恢复点保留数量和天数，默认 `5` 和 `30` |
| `APP_BASE_URL` | 网站基地址，通过反代公开时填写 HTTPS 域名 |
| `GMAIL_OAUTH_REDIRECT_BASE` | Gmail OAuth 回调基地址 |
| `CODEX_OAUTH_REDIRECT_BASE`、`CODEX_LOGIN_ADDR` | Codex OAuth 回调配置 |
| `OUTLOOK_CLIENT_ID`、`OUTLOOK_CLIENT_SECRET`、`OUTLOOK_TENANT`、`OUTLOOK_OAUTH_REDIRECT_URI` | Outlook Graph OAuth |

公网反代需保留站点 `Host`，覆盖客户端传入的 `X-Forwarded-Proto` 和 `X-Forwarded-For`，
分别传入 `https` 与代理确认的客户端地址。源站网络层应只对反代开放服务端口，反代访问日志应省略
OAuth 回调的查询参数。应用登录请求上限为 8 KiB，一般请求体上限为 1 MiB。

### 新版工作台的正式切换准备

`.env.workspace.example` 只提供可选开关，不能覆盖服务器原有 `.env`。
正式入口继续使用 `python code-receive.py`，保留 `MAILAPI_MODE=production`、正式数据目录、
凭据密钥、管理员 TOTP、外部监听和可信反代设置。启用工作台不会读取 `TEST_*` 数据或初始化测试管理员，
也不会启用测试性能监测脚本。

同步应先记录正式提交和进程、备份匹配的数据与密钥，再在隔离副本安装工作台依赖并演练登录、权限、附件与导出。
确认后只同步代码，最后在正式配置增加 `MAILAPI_WORKSPACE_ENABLED=1` 并重启正式入口。
此文档和示例文件不代表已经完成服务器部署。

财务、通知和本站 API 关联继续使用名为 `test_features.sqlite3` 的数据库，但位置取当前运行模式的数据目录；
正式首次启用时在正式目录创建空库及私有附件目录。不要用测试库、账户或附件目录覆盖正式数据。
如需迁入测试中的真实报销，必须另行确认范围、账户对应关系和历史附件，不能直接复制整库。
正式备份从启用后也必须包含此库和 `finance_files/`。

API 转发还需独立的正式 NewAPI 服务、数据库及真实渠道；复制网页代码不会自动部署网关。
NewAPI 的数据库、配置和管理凭据需要独立备份。测试预览使用的模拟上游、演示 Key 和额度不能用于正式配置。

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
| `TEST_GMAIL_ACCOUNTS` | 测试版没有账号文件时的可选账号配置 |
| `TEST_AUTH_ARTIFACT_CLEANUP` | 设为 `1` 时，测试版启动后创建加密恢复点并清理授权历史归档，需要同时配置独立测试密钥和 OAuth 加密 |
| `TEST_AUTH_ARTIFACT_KEEP_HISTORY`、`TEST_AUTH_ARTIFACT_MAX_AGE_DAYS` | 测试版授权归档保留数量和天数，默认 `5` 和 `30`，两条规则取并集 |

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

正式运行数据包括两个 SQLite 库、`resource_secrets/` 及配套的 OAuth、接码历史和图文目录。完整归档还需备份运行配置与目录外的客户端配置。正式凭据密钥单独保管。

测试版默认从正式 `runtime/` 创建临时快照，跳过正式账户 JSON、SQLite 及其日志文件和 `runtime/test/`。正式 OAuth 加密启用后，测试版必须使用独立数据目录和测试密钥。

### 混合存储边界

正式版的两个 SQLite 文件是结构化业务数据的主存储。

- `site_accounts.sqlite3` 保存网站账户、密码哈希、会话摘要、分组、成员和资源授权。
- `resources.sqlite3` 保存 GPT 资料、Coding Plan、中转站、邮箱池、Codex 元数据、GPT 修改记录和恢复点。
- GPT 密码、2FA、API Key 和恢复点在 SQLite 中保存为带版本、密钥 ID、随机 nonce 和鉴别数据的密文。
- `gmail_tokens/`、`outlook_tokens/`、`codex_auth/` 和 `auth_artifact_versions/` 保存加密 OAuth 文件及其历史副本。
- 活动授权文件会原子覆盖。测试版启用 `TEST_AUTH_ARTIFACT_CLEANUP=1`，或正式版启用 `MAILAPI_AUTH_ARTIFACT_CLEANUP=1` 后，每次启动先在 `auth_artifact_restore_points/` 创建加密恢复点，再按“当前版本 + 最近 N 个历史版本 + N 天内版本”的并集清理授权归档和旧恢复点。恢复点清理不会递归创建新的恢复点。
- `oauth_clients.json` 保存加密的 Google 和 Outlook 客户端配置。
- `resource_secrets/` 保存资源库中的加密私有数据。`mail_filters.json`、接码历史、历史游标、Codex 图文提示、图片和错误日志继续使用独立文件。

数据库中的资源记录可以引用 OAuth 文件和其他文件的相对路径。路径、资源 ID、文件版本和加密状态属于关联元数据，不能替代文件本身。正式版不再从旧账户 JSON 或旧资源 JSON 读取运行数据。

### 正式备份、恢复与回滚

备份必须在服务停写后进行，或使用 SQLite Backup API 生成一致副本。一次完整备份至少包含下面几类内容。

1. 两个 SQLite 数据库及其 `-wal`、`-shm` 文件处理结果。完成备份后应执行 `PRAGMA integrity_check` 和外键检查。
2. `resource_secrets/`、OAuth 加密目录、`oauth_clients.json`、接码历史、图文提示、图片和日志。
3. `.env`、测试配置、客户端回调配置和外部配置路径。OAuth 客户端配置已经集中加密时，不再需要保留旧明文客户端文件作为运行文件。
4. 正式凭据密钥、OAuth 加密密钥、旧恢复密钥和归档解密密钥。密钥与数据分开存放，并保留每个备份对应的密钥版本。
5. 代码提交哈希和工作区补丁。服务器工作区不应包含未记录的运行代码改动。

恢复时必须把匹配的一组代码、两个数据库、`resource_secrets/`、OAuth 文件、客户端配置和密钥放入新的隔离目录。先以只读方式核对数据库完整性、账户数量、分组授权、OAuth 文件解密和密钥版本，再启动测试入口验证登录、权限和授权读取。正式切换前应停止旧进程，原子替换完整数据组，启动后检查登录页、`/api/auth/me`、资源列表和授权状态。

回滚时保留当前运行目录和新密钥，避免丢失已经轮换的 OAuth token。恢复旧版本时必须同时恢复与旧代码匹配的两个数据库、加密文件、密钥和配置。只回退代码或只替换一个数据库都不安全。切换前的旧 JSON、未加密授权和历史快照只能作为受保护的加密归档，不能直接重新放回运行目录。

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

备份和恢复时须配套保存资源库、`resource_secrets/`、`account_credentials.restore.key` 以及授权和图片文件。加密资源库禁止直接导出明文 JSON。切换后如需回退，应恢复同一备份中的代码、两个 SQLite 库、加密文件和密钥，或使用专门的离线回滚工具在隔离目录重建旧版布局。已切换后产生的更新只会写入新存储。

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

迁移保留 OAuth token 和 MSAL 缓存内容。服务商可撤销令牌，或者在续期时使旧 refresh token 失效，因此恢复旧备份时仍要核对当时的令牌是否有效。演练用合成凭据验证读取、模拟续期和加密写回。Google 和 Outlook 客户端配置沿用现有管理方式。

### 正式版 OAuth 加密

停写并备份正式运行目录、配置及 OAuth 文件，另存对应密钥。将备份恢复到独立目录。使用 `encrypt-oauth --production-key --original-runtime /path/production-runtime` 创建并核对加密副本。该选项把旧绝对路径映射到副本。切换资源库、配套文件与 OAuth 目录后设置 `MAILAPI_OAUTH_ENCRYPTION=1`。正式密钥取自 `MAILAPI_CREDENTIAL_KEY_FILE`。先验证读取、刷新与加密写回，再恢复正常监听。

若切换后服务商已经轮换令牌，先停写并保存当前加密状态。从停写副本调用 `rollback_oauth` 转换最新 OAuth 文件和配套资源库，然后按原布局还原并关闭 OAuth 加密开关。切换前备份用于核对和更早的恢复点，已轮换的令牌可能无法从该备份继续使用。

### OAuth 客户端配置与接码保留期

`oauth_clients.json` 集中保存完整的 Google 和 Outlook 客户端配置，使用当前环境的 OAuth 文件密钥加密，随运行目录备份并在密钥轮换时重新加密。停写后可用下面的命令从现有配置迁移：

```bash
python -m mailapi_shared.oauth_clients --data-dir /path/test-data \
  --key-file /keys/test.json --google-file /private/google.json --outlook-env /private/test.env
```

正式版增加 `--production-key`，并在校验通过后设置 `MAILAPI_OAUTH_CLIENTS_ENCRYPTED=1`。隔离版用 `TEST_OAUTH_CLIENTS_ENCRYPTED=1`。现有 Gmail 的 client ID 须与 Google 配置一致。启用后，新写入的 Gmail 令牌文件从集中配置取得客户端密钥。撤销此开关前，恢复 Google 客户端文件和 Outlook 环境配置，并把 Gmail 客户端密钥填回采用新格式的令牌文件。

接码历史从本站捕获时间起保留 24 小时，每个邮箱最多 30 条。过期记录不再出现在接口中，正式进程启动时和运行期间每五分钟清理历史文件。旧错误日志与历史备份仍受各自的保留策略约束。

## 可选外部服务

| 功能 | 额外条件 |
| --- | --- |
| Gmail | `credentials.json`、Google Cloud OAuth 客户端和可访问 Google 的网络 |
| Outlook | Microsoft Graph 应用、客户端密钥、HTTPS 回调地址和 `msal` |
| Codex | OAuth/用量接口网络和回调端口 1455 或自定义地址 |

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
