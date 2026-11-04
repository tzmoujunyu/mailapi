# 正式版与测试版

正式入口继续使用 `python code-receive.py`，默认端口 8000。
测试入口使用 `python code-receive-test.py`，默认监听 `127.0.0.1:8001`。
在远程开发环境中转发 8001 端口；需要直接通过服务器地址访问时设置 `TEST_HOST=0.0.0.0`。

测试版首次启动：

```bash
cp .env.test.example .env.test
chmod 600 .env.test
# 编辑 .env.test，填写 TEST_ADMIN_PASSWORD，至少 8 个字符。
python code-receive-test.py
```

访问 `http://localhost:8001/login`，用户名为 `admin`，密码为上述独立密码。
默认会为本次进程创建临时数据快照；测试版 admin 写入临时目录中的
`site_accounts.json`，进程退出后随快照一起删除。初始化完成后可从配置中移除初始密码。

## 文件职责

```text
code-receive.py             正式入口和现有路由
static/                     正式页面
*.py、mail_providers/        正式版服务模块，保留现有导入兼容
code-receive-test.py        测试入口
test_app/
  main.py                   测试版邮件、资源和授权路由
  accounts.py               登录、网站账号和分组 API
  site_accounts.py          密码哈希、会话、角色和分组授权存储
  services/                 测试版独立服务模块
    mail_providers/         邮箱提供商
  static/                   测试页面
scripts/                    运维、授权与旧中继脚本
tests/                      Python 与前端回归测试
docs/                       开发流程
runtime/                    正式运行数据（不提交）
  test/                     仅供显式 TEST_DATA_DIR 场景使用的测试数据（不提交）
```

测试代码、页面和服务模块从当前工作区建立独立快照。保留正式文件的位置和导入方式，
新开发集中到 `test_app/` 内，避免因整理文件影响已有部署。

测试版只加载根目录 `.env.test`，不会读取正式 `.env`。启动时会复制正式 `runtime/` 中的资源、
邮箱、Codex、凭据、历史和规则数据，网站登录控制数据、`runtime/test/` 和正式邮件中继密钥
会被跳过。测试版首次启动自己的 admin。测试模式默认不连接外部邮箱或 Codex 接口，测试页面
的修改只写临时副本；需要测试外部监听时才设置 `TEST_EXTERNAL_WATCHERS=1`。
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
| `PUT/DELETE /api/admin/users/{id}` | 重置密码、调整角色、删除网站账号 |
| `GET/POST /api/admin/groups` | 列表、创建分组 |
| `PUT/DELETE /api/admin/groups/{id}` | 编辑、删除分组 |
| `GET /api/admin/resources` | 可分配资源目录（不包含凭据） |

创建分组请求示例（ID 和资源 key 从上述列表接口取得）：

```json
{"name":"研发组","member_ids":["网站账号ID"],"resource_keys":["gpt:email:example@example.com"]}
```

## 授权配置

Gmail 可以复用根目录 `credentials.json` 中的 OAuth 客户端配置；这不是邮箱 token。
在 Google 客户端中增加测试回调 `http://localhost:8001/`。
公网地址可以在 `.env.test` 设置 `TEST_GMAIL_OAUTH_REDIRECT_BASE` 和 `TEST_APP_BASE_URL`。
Outlook 在 `.env.test` 配置 `OUTLOOK_CLIENT_ID`、`OUTLOOK_CLIENT_SECRET` 和独立的
`OUTLOOK_OAUTH_REDIRECT_URI`，指向测试服务的 `/api/admin/outlook/auth/callback`。

Codex 测试沿用原授权回调 URI `http://localhost:1455/auth/callback`，测试版不占用该端口。
完成登录后，把浏览器的完整返回链接粘贴到**测试版**控制台的“Codex 授权返回链接”。
链接中的一次性 state 仅由发起授权的版本识别；正式版不会处理测试版会话。

用独立的资源账号测试邮箱监听和授权，避免两个服务同时消费同一邮箱或刷新同一授权。

## 开发与同步

1. 新功能只修改 `test_app/`；同步更新相关测试和文档。
2. 在测试服务验收界面、权限、资源和授权流程。
3. 验证通过后，明确选择需要同步的功能，把对应服务、路由和页面迁入正式版；
   逐项审阅差异，保留正式端口、配置和数据目录。不要整目录覆盖正式运行数据。
4. 执行回归验证并提交一个可独立回退的 git 检查点，再重启正式服务。

```bash
python -m unittest discover -s tests -q
node --test tests/test_account_ui.cjs
MAILAPI_UI_ROOT=test_app/static node --test tests/test_account_ui.cjs
```
