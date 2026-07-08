# ChatGPT 验证码监听

## 启动

```bash
export ACCESS_PASSWORD='你的口令'
python3 code-receive.py
```

## 缺少 Gmail Token 时授权

程序缺少 `runtime/token_account*.json` 时，会在终端打印完整的 Google 授权链接。

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

## Codex 账号信息

页面下方的“Codex账号信息”点击“导入”会打开 Codex/OpenAI 授权页面。选择账号并授权后，回调会自动保存 auth 文件并刷新账号订阅和额度信息。

授权结果会按账号保存在 `runtime/codex_auth/`，同时同步最新一次授权到 `runtime/codex_auth.json` 兼容旧路径。账号快照保存在 `runtime/codex_accounts.json`，这些运行时文件已被 git 忽略。如果授权失效，点击“刷新”重新授权，会覆盖对应账号 auth 文件并重新导入账号。

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
