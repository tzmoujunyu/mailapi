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

## Codex 账号信息

页面下方的“Codex账号信息”可以导入 ChatGPT/Codex 授权后的 JSON，例如浏览器中 `https://chatgpt.com/api/auth/session` 返回的内容，或包含 `access_token` / `accessToken` 的 Codex auth JSON。

导入后程序会提取账号邮箱、ChatGPT Account ID、workspace、订阅计划和 Codex 额度窗口。账号 token 只保存在 `runtime/codex_accounts.json`，该目录已被 git 忽略。
