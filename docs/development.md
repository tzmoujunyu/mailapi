# 正式版与测试版

架构分层、代码归属、数据边界和回退原则见[架构说明](architecture.md)。本页只保留开发者
日常启动、验证和同步所需的操作步骤。

正式入口 `python code-receive.py` 和测试入口 `python code-receive-test.py` 都加载
`mailapi_shared.application`。入口文件只选择运行模式；登录、路由、网站账号、资源聚合、
邮件监听和授权流程由同一份应用实现。正式版默认端口 8000，测试版默认端口 8001；测试版
默认监听 `127.0.0.1`，需要直接通过服务器地址访问时设置 `TEST_HOST=0.0.0.0`。

测试版首次启动：

```bash
cp .env.test.example .env.test
chmod 600 .env.test
# 编辑 .env.test，填写 TEST_ADMIN_PASSWORD，至少 8 个字符。
python code-receive-test.py
```

访问 `http://localhost:8001/login`，用户名为 `admin`，密码为上述独立密码。正式版也使用
相同的用户名加密码登录；首次创建正式版 admin 时使用 `ADMIN_ACCESS_PASSWORD`，未设置时
回退到现有 `ACCESS_PASSWORD`。
默认会为本次进程创建临时数据快照；测试版 admin 写入临时目录中的
`site_accounts.json`，进程退出后随快照一起删除。初始化完成后可从配置中移除初始密码。

## 文件职责

```text
code-receive.py             正式入口，只设置 MAILAPI_MODE=production
code-receive-test.py        测试入口，只设置 MAILAPI_MODE=test
mailapi_shared/application.py
                            共用 FastAPI 应用、登录中间件和所有业务路由
mailapi_shared/accounts.py  用户名密码登录、会话和网站账号 API
mailapi_shared/site_accounts.py
                            密码哈希、会话、角色和分组授权存储
mailapi_shared/static/      共用页面、脚本和样式
static -> mailapi_shared/static
test_app/static -> ../mailapi_shared/static
                            历史路径兼容链接
*.py、mail_providers/        正式版兼容入口，实际实现来自 mailapi_shared/
test_app/services/           测试版历史导入路径，实际实现来自 mailapi_shared/
scripts/                    运维、授权与旧中继脚本
tests/                      Python 与前端回归测试
docs/                       开发流程
runtime/                    正式运行数据（不提交）
  test/                     仅供显式 TEST_DATA_DIR 场景使用的测试数据（不提交）
```

两版的共用应用实现集中在 `mailapi_shared/`。根目录模块和 `test_app/services/` 只保留
原有导入路径的兼容入口。两版只通过运行模式选择数据目录、初始登录配置和外部监听开关；
页面、登录接口和业务路由不再分叉。

测试版只加载根目录 `.env.test`，不会读取正式 `.env`。启动时会复制正式 `runtime/` 中的资源、
邮箱、Codex、凭据、历史和规则数据，网站登录控制数据、`runtime/test/` 和正式邮件中继密钥
会被跳过。测试版首次启动自己的 admin。测试模式默认不连接外部邮箱或 Codex 接口，测试页面
的修改只写临时副本；需要测试外部监听时才设置 `TEST_EXTERNAL_WATCHERS=1`。正式版默认
使用 `runtime/`，也可通过 `MAILAPI_DATA_DIR` 指定独立目录；正式外部监听默认开启，使用
`MAILAPI_EXTERNAL_WATCHERS=0` 可暂时关闭。
自动测试可通过 `TEST_DATA_DIR` 使用指定的独立目录；该变量不能指向正式运行目录，指定目录由
测试调用方负责清理。

## 账号和权限

| 权限 | 用户 (`user`) | 普通管理员 (`manager`) | `admin` |
| --- | --- | --- | --- |
| 查看和使用资源 | 所属分组分配的资源，GPT 还须上架 | 全部资源 | 全部资源 |
| 创建用户或普通管理员 | 否 | 是 | 是 |
| 创建、命名、删除分组，增删成员与分配资源 | 否 | 是 | 是 |
| 重置用户密码 | 否 | 是 | 是 |
| 重置普通管理员密码、调整角色 | 否 | 否 | 是 |
| 删除网站账号 | 否 | 否 | 是 |
| 修改本人密码、退出登录 | 是 | 是 | 是 |

`admin` 是唯一保留账号，不能新增、删除或降级。其密码通过“修改本人密码”更新。
网站账号的用户名不区分大小写；不开放注册。两类管理员在 `/admin/access` 创建账号，
维护分组名称、成员与 GPT、Coding Plan、中转站资源分配；资源本身在 `/admin` 管理。
一个账号或资源可以属于多个分组，用户获得这些分组资源的并集。
所有列表、验证码、历史、密码、2FA 与资源密钥读取接口都执行同一权限检查。
在网站账号表点击“按此账号预览”核对指定用户的分组视图；预览始终按用户资源权限展示。

账号、分组和会话写入临时目录中的 `site_accounts.json`，采用原子写入和文件权限 600。
网站密码使用带独立盐的 PBKDF2-SHA256 哈希，登录 cookie 只携带随机会话 token，
服务器只保存 token 摘要；会话有效期为 24 小时。退出、删除账号或修改密码会使相关旧会话失效，
分组和角色变化从下一次请求起生效。服务保持单进程运行；不要设置多个 Uvicorn worker。

主要接口：

| 接口 | 用途 |
| --- | --- |
| `POST /api/auth/login` | JSON 用户名和密码登录，响应设置会话 cookie |
| `GET /api/auth/me` | 当前账号和所属分组 |
| `POST /api/auth/logout`、`POST /api/auth/password` | 退出、修改本人密码 |
| `GET/POST /api/admin/users` | 列表、创建网站账号 |
| `POST /api/admin/users/batch` | 仅 admin 批量创建用户或普通管理员，共用一个初始密码 |
| `PUT/DELETE /api/admin/users/{id}` | 重置密码、调整角色、删除网站账号 |
| `GET/POST /api/admin/groups` | 列表、创建分组 |
| `PUT/DELETE /api/admin/groups/{id}` | 编辑、删除分组 |
| `GET /api/admin/resources` | 可分配资源目录（不包含凭据） |

创建分组请求示例（ID 和资源 key 从上述列表接口取得）：

```json
{"name":"研发组","member_ids":["网站账号ID"],"resource_keys":["gpt:email:example@example.com"]}
```

账号控制台的“批量”按钮支持每行一个用户名，也支持空格、逗号或分号分隔。admin 选择统一
角色和初始密码后一次提交；后端会先校验整批用户名，遇到重复或已存在账号时整批拒绝，
不会产生部分成功的结果。一次最多创建 100 个账号，每个账号仍使用独立密码哈希。

## 授权配置

Gmail 可以复用根目录 `credentials.json` 中的 OAuth 客户端配置；这不是邮箱 token。
在 Google 客户端中增加测试回调 `http://localhost:8001/`。
公网地址可以在 `.env.test` 设置 `TEST_GMAIL_OAUTH_REDIRECT_BASE` 和 `TEST_APP_BASE_URL`。
Outlook 在 `.env.test` 配置 `OUTLOOK_CLIENT_ID`、`OUTLOOK_CLIENT_SECRET` 和独立的
`OUTLOOK_OAUTH_REDIRECT_URI`，指向测试服务的 `/api/admin/outlook/auth/callback`。

Codex 回调监听也由共享应用实现。测试版默认关闭外部监听，因此不会启动回调监听或刷新
外部账号；正式版默认按 `CODEX_LOGIN_ADDR`（默认 `localhost:1455`）启动。测试时若显式
设置 `TEST_EXTERNAL_WATCHERS=1`，应使用独立的回调地址和资源，避免与正式版同时刷新同一授权。

用独立的资源账号测试邮箱监听和授权，避免两个服务同时消费同一邮箱或刷新同一授权。

## 开发与同步

1. 修改 `mailapi_shared/`，保持入口、静态资源和兼容导入路径只承担模式选择或转发。
2. 先启动测试入口，使用正式数据快照验收登录、页面、权限、资源和路由；测试默认不连接外部服务。
3. 运行两版回归测试，确认测试和正式入口都加载同一应用；不要把测试运行目录覆盖到正式 `runtime/`。
4. 提交一个可独立回退的 Git 检查点，同步服务器，再按正式服务的配置和数据目录重启检查。

同步时只覆盖代码、静态资源、测试和文档；不要覆盖服务器 `.env`、`.env.test`、`runtime/`、
`credentials.json` 或虚拟环境。每个端口只保留一个服务进程，避免同一版本同时由 tmux、终端
和 `nohup` 启动。

停止服务前先确认 PID 属于预期入口：

```bash
pgrep -af 'code-receive(-test)?\.py'
ss -ltnp | grep -E ':(8000|8001)\b'
kill -TERM <pid>
```

测试版默认是临时快照；测试过程中创建的网站账号和分组不会作为数据同步到正式版。要验证
正式版行为，应同步代码后用正式 `runtime/` 启动一个独立正式模式实例，或在服务器重启正式
服务后做健康检查。

```bash
python -m unittest discover -s tests -q
node --test tests/test_account_ui.cjs
MAILAPI_UI_ROOT=mailapi_shared/static node --test tests/test_account_ui.cjs
```
