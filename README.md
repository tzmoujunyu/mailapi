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
