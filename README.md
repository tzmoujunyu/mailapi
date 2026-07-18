# ChatGPT 验证码监听

## 启动

```bash
export ACCESS_PASSWORD='你的口令'
python3 code-receive.py
```

## 缺少 Gmail Token 时授权

登录后打开 `/admin` 账号控制台，点击“添加账号”即可选择新的 Google 账号。授权完成后，程序会读取实际 Gmail 地址、保存独立 token 并立即开始监听。账号列表保存在 `runtime/gmail_accounts.json`，新 token 保存在 `runtime/gmail_tokens/`。

控制台支持重新授权、停用、启用和删除。删除只移出监听列表，原 token 和验证码历史仍会保留。首次迁移时，原来的 `runtime/token_account1.json` 和 `runtime/token_account2.json` 会继续使用。

程序启动时若发现已有账号缺少 token，也会在终端打印完整的 Google 授权链接。

在 VS Code 远程环境里，只需要保证网站端口 `8000` 已转发到本地，然后把终端里的 `https://accounts.google.com/...` 链接复制到本地浏览器打开。

Google 授权回调统一使用现有网站端口：

```text
http://localhost:8000/
```

如果你使用的是 Google Cloud 的 Web OAuth 客户端，需要把上面的地址加入 `Authorized redirect URIs`。

如果开头的授权链接被忽略，程序会每等待 10 次重新打印一次授权链接。可通过环境变量调整：

```bash
export AUTH_REMINDER_EVERY=10
```

如果浏览器回调仍然不可用，可以强制使用控制台授权：

```bash
export GMAIL_AUTH_FALLBACK=console
python3 code-receive.py
```

如果 `runtime/token_account*.json` 过期或刷新失败，程序启动时会自动把旧 token 文件备份成 `*.bak.YYYYMMDDHHMMSS`，并在终端输出新的 Google 授权链接。打开链接完成授权后会写回新的 token 文件。

如果报 `CERTIFICATE_VERIFY_FAILED` 或 `self-signed certificate`，说明 Python 请求 Google token 接口时不信任当前网络代理/网关的根证书。先把该根证书加入系统或 Python 信任链，或设置：

```bash
export REQUESTS_CA_BUNDLE='/path/to/ca-bundle.pem'
export SSL_CERT_FILE='/path/to/ca-bundle.pem'
```

也可以把同样的配置写入项目根目录 `.env`，程序启动时会自动读取。

验证码邮件会先按 OpenAI 发件人筛选，再校验 ChatGPT/OpenAI 与登录、临时、验证等主题或正文语义。需要增加官方发件地址时，可在 `.env` 中设置逗号分隔的允许列表：

```bash
OPENAI_CODE_SENDERS=noreply@tm.openai.com,noreply@tm1.openai.com
```

## Codex 账号信息

打开 `/admin` 账号控制台，点击 Codex 区域的“导入账号”或“重新授权”会打开 Codex/OpenAI 授权页面。选择账号并授权后，回调会自动保存 auth 文件并刷新账号订阅和额度信息。主页面只负责显示账号状态，不再提供修改操作。

授权结果会按账号保存在 `runtime/codex_auth/`，同时同步最新一次授权到 `runtime/codex_auth.json` 兼容旧路径。账号快照保存在 `runtime/codex_accounts.json`，这些运行时文件已被 git 忽略。自动续期产生的新 token 会同步写回对应 auth 文件；明确失效的账号会停止重复刷新并显示“需重新授权”。

Codex 额度默认每 1 分钟后台刷新一次，网页上的账号额度显示也每 1 分钟同步一次。订阅信息默认每 1 小时刷新一次，账号栏会显示“无订阅”或订阅到期时间。可通过 `CODEX_REFRESH_INTERVAL_SECONDS` 调整额度刷新间隔，通过 `CODEX_SUBSCRIPTION_REFRESH_INTERVAL_SECONDS` 调整订阅刷新间隔。

默认 Codex 授权回调地址与 Codex CLI/Codex-Manager 保持一致，使用独立本地端口：

```text
http://localhost:1455/auth/callback
```

在 VS Code 远程环境里，需要同时转发网站端口 `8000` 和 Codex 回调端口 `1455`。如果你的转发端口或访问域名不同，可以设置：

```bash
export CODEX_OAUTH_REDIRECT_BASE='http://localhost:1455'
export CODEX_LOGIN_ADDR='localhost:1455'
export APP_BASE_URL='http://localhost:8000'
```
