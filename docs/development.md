# 正式版与测试版

正式入口继续使用 `python code-receive.py`，默认端口 8000。
测试入口使用 `python code-receive-test.py`，默认监听 `127.0.0.1:8001`。
在远程开发环境中转发 8001 端口；需要直接通过服务器地址访问时设置 `TEST_HOST=0.0.0.0`。

## 文件职责

```text
code-receive.py             正式入口和现有路由
static/                     正式页面
*.py、mail_providers/        正式版服务模块，保留现有导入兼容
code-receive-test.py        测试入口
test_app/
  main.py                   测试版邮件、资源和授权路由
  services/                 测试版独立服务模块
    mail_providers/         邮箱提供商
  static/                   测试页面
scripts/                    运维、授权与旧中继脚本
tests/                      Python 与前端回归测试
docs/                       开发流程
runtime/                    正式运行数据（不提交）
  test/                     测试运行数据（不提交）
```

测试代码、页面和服务模块从当前工作区建立独立快照。保留正式文件的位置和导入方式，
新开发集中到 `test_app/` 内，避免因整理文件影响已有部署。

测试版只加载根目录 `.env.test`，不会读取正式 `.env`。首次启动资源列表为空，
不会导入正式默认邮箱、`GMAIL_ACCOUNTS`、共享登录密码或邮件中继密钥。
测试账号、token、历史、分组与日志均位于 `runtime/test/`。
自动测试通过 `TEST_DATA_DIR` 使用临时目录；该变量不能指向正式运行目录。

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
```
