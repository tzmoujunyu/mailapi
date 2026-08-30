# ChatGPT 多邮箱验证码监听

## 启动

```bash
export ACCESS_PASSWORD='你的口令'
python3 code-receive.py
```

依赖可通过 `pip install -r requirements.txt` 安装。网页控制台当前支持 Gmail 和 Outlook；Proton 旧实现仍保留在代码中，但不再提供新增和登录入口。

## 缺少 Gmail Token 时授权

登录后打开 `/admin` 账号控制台，点击“添加 Gmail”即可选择新的 Google 账号。授权完成后，程序会读取实际 Gmail 地址、保存独立 token 并立即开始监听。统一邮箱账号列表暂沿用 `runtime/gmail_accounts.json` 文件名，新 token 保存在 `runtime/gmail_tokens/`。

控制台支持重新授权、停用、启用和删除。删除只移出监听列表，原 token 和验证码历史仍会保留。启动时会把旧的 `runtime/token_accountN.json` 自动迁移为 `runtime/gmail_tokens/account-N.json`，并同步更新账号索引。

程序启动时若发现已有账号缺少 token，也会在终端打印完整的 Google 授权链接。

在 VS Code 远程环境里，只需要保证网站端口 `8000` 已转发到本地，然后把终端里的 `https://accounts.google.com/...` 链接复制到本地浏览器打开。

Google 授权回调统一使用现有网站端口：

```text
http://localhost:8000/
```

人在外部网络时，Google 授权结束后可能无法打开上述 `localhost` 地址。此时不要修改地址栏内容，复制包含 `state` 和 `code` 的完整返回链接，回到公网 `/admin` 账号控制台，将其粘贴到“Google 授权返回链接”输入框并提交。服务器会根据一次性 `state` 找到对应账号、换取 token 并覆盖原 token 文件。

返回链接包含短期有效的一次性授权码，不要发给其他人，也不要粘贴到日志或聊天中。必须先从账号控制台点击“添加 Gmail”或“重新授权”创建当前授权会话；旧链接、已用链接以及服务重启前生成的链接无法提交。

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

如果 Gmail token 过期或刷新失败，程序会把旧文件移动到 `runtime/gmail_tokens/backups/account-N/`，并在终端输出新的 Google 授权链接。打开链接完成授权后会写回 `runtime/gmail_tokens/account-N.json`。

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

## Outlook 接码

Outlook/Hotmail 使用 Microsoft Graph 和 OAuth，不保存邮箱密码。先在 Microsoft Entra 管理中心注册应用：

1. 支持的账号类型选择包含“个人 Microsoft 账号”的选项。
2. 添加 Web 重定向 URI，必须与 `OUTLOOK_OAUTH_REDIRECT_URI` 完全一致。
3. 创建客户端密码。
4. 添加 Microsoft Graph 委托权限 `User.Read` 和 `Mail.ReadWrite`。

在 `.env` 中配置：

```bash
OUTLOOK_CLIENT_ID=应用客户端ID
OUTLOOK_CLIENT_SECRET=客户端密码值
OUTLOOK_TENANT=common
OUTLOOK_OAUTH_REDIRECT_URI=https://你的公网域名/api/admin/outlook/auth/callback
OUTLOOK_POLL_INTERVAL_SECONDS=3
```

公网回调必须使用有效 HTTPS；本机端口转发场景也可以注册
`http://localhost:8000/api/admin/outlook/auth/callback`。重启程序后，在 `/admin`
点击“添加 Outlook”，选择账号并同意权限即可。授权缓存保存在：

```text
runtime/outlook_tokens/account-N.json
```

程序每次轮询只读取收件箱未读邮件，继续使用与 Gmail 相同的 OpenAI 发件人白名单和中英文验证码规则，仅在成功提取验证码后将该邮件标记为已读。MSAL 会使用缓存中的 refresh token 静默续期；失效时控制台显示“需重新授权”。

## Proton 旧实现

Proton 接码代码、中继脚本和已有运行文件会继续保留，便于查看或回退，但账号控制台不再显示“添加 Proton”或“重新登录”。以下内容仅作为旧部署参考。

Proton Free 没有官方 IMAP/API 接口，本项目通过 `protonmail-api-client` 复用 Proton 网页会话。默认从下列配置读取账号密码和 TOTP：

```text
~/proton/.env
```

也可以在项目 `.env` 中设置 `PROTON_CONFIG_FILE` 指向其他配置文件。旧版控制台创建的账号会使用独立会话文件：

```text
runtime/proton_sessions/account-4.pickle
```

如果网页登录触发 CAPTCHA，在项目目录运行：

```bash
/home/moujy/.conda/envs/gmail/bin/python scripts/proton_login.py --account account-4 --manual-captcha
```

登录完成后重启主程序。Proton 密码和 TOTP 不会写入账号列表；会话文件、配置文件和整个 `runtime/` 都不能上传或分享。

### 本地中继模式

服务器无法连接 Proton 时，将对应账号设置为 `delivery_mode: relay`。服务器会停止直连 Proton，改为接收本地电脑推送的验证码。项目当前的 `account-4` 已使用该模式。

服务器首次启动新版程序时会生成专用密钥：

```text
runtime/mail_relay_secret
```

该文件权限为 `600`，不要通过聊天、网页或公开仓库传输。通过 SCP 等安全方式复制到本地电脑，并保持权限为 `600`。可以从 [proton-relay.env.example](proton-relay.env.example) 创建本地配置，其中必须设置：

```bash
MAIL_RELAY_URL=https://你的服务器/api/internal/mail-relay
MAIL_RELAY_SECRET_FILE=mail_relay_secret
MAIL_RELAY_ACCOUNT=account-4
```

公网服务器必须使用有效 HTTPS。中继请求使用 HMAC-SHA256、五分钟时间窗和一次性 nonce 验证；它不使用网页的 `ACCESS_PASSWORD`。

如果服务器没有 HTTPS，可在本地电脑建立 SSH 隧道：

```bash
ssh -N -L 18000:127.0.0.1:8000 服务器用户名@服务器地址
```

然后把本地配置改为：

```bash
MAIL_RELAY_URL=http://localhost:18000/api/internal/mail-relay
```

这种方式下 HTTP 只经过本机回环接口，实际跨网络流量由 SSH 加密。中继脚本不会允许向其他远程地址发送明文 HTTP。

在能访问 Proton 的本地电脑安装依赖后，首次登录并启动监听：

```bash
python scripts/proton_relay.py --config proton-relay.env --login --manual-captcha
```

已有有效本地会话时直接运行：

```bash
python scripts/proton_relay.py --config proton-relay.env
```

本地程序复用与 Gmail 相同的 OpenAI 发件人和验证码规则。只有服务器确认接收成功后，邮件才会标为已读；服务器暂时不可用时会保留未读并重试。完成本地部署后，应从服务器 `.env` 删除 `PROTON_PASSWORD` 和 `PROTON_TOTP_SECRET`，凭据只留在本地电脑。

验证码页面可按邮箱显示并复制对应的 GPT 密码。密码不再需要按 `account-N`
编号写入 `.env`：在 `/admin` 账号控制台找到对应邮箱，点击 GPT 密码栏的“添加”或
“修改”即可。后端会同时校验内部账号标识与邮箱地址，避免账号编号复用时串号。

密码独立保存在：

```text
runtime/gpt_passwords.json
```

该文件与原 `.env` 一样属于服务器端明文凭据，但权限固定为 `600`；内容不会写入邮箱账号列表或普通列表接口。实际密码仅在登录后的
验证码页面点击“复制”时按需读取，并禁止响应缓存。旧版 `.env` 中已有的
`ACCOUNT_PASSWORD_N` 会在首次启动时自动迁移到该文件，确认控制台显示“已添加”后即可
从 `.env` 删除这些旧变量。

## 异常日志

程序会把 Gmail/Outlook 监听、Google/Microsoft/Codex 授权、Codex 信息刷新、Proton 旧监听和未处理 Web 请求的异常写入：

```text
runtime/logs/errors.log
```

日志仅记录异常上下文和堆栈，并会脱敏常见 token、Authorization 和 password 字段。单个文件默认最大 5 MB，保留 5 个滚动备份；可通过 `ERROR_LOG_MAX_BYTES` 和 `ERROR_LOG_BACKUP_COUNT` 调整。

## Codex 账号信息

打开 `/admin` 账号控制台，点击 Codex 区域的“导入账号”或“重新授权”会打开 Codex/OpenAI 授权页面。选择账号并授权后，回调会自动保存 auth 文件并刷新账号订阅和额度信息。主页面只负责显示账号状态，不再提供修改操作。

如果授权完成后浏览器无法打开 `http://localhost:1455/auth/callback`，复制浏览器地址栏中包含 `code` 和 `state` 的完整链接，粘贴到控制台的“Codex 授权返回链接”输入框。必须先从目标账号所在行点击“重新授权”：服务端会通过一次性 `state` 找回目标邮箱，并在保存前核对 token 中的实际邮箱；选错账号时会拒绝覆盖原授权。“导入账号”生成的会话没有预设目标，会按 token 中的实际邮箱识别新账号。

授权结果按账号分别保存在 `runtime/codex_auth/`，不再额外创建根目录兼容副本。旧的 `runtime/codex_auth.json` 会在启动时归并到对应账号文件；冲突版本保存在 `runtime/codex_auth/backups/`。账号快照保存在 `runtime/codex_accounts.json`，这些运行时文件已被 git 忽略。自动续期产生的新 token 会写回对应账号 auth 文件；明确失效的账号会停止重复刷新并显示“需重新授权”。

Gmail 和 Codex 授权目录权限为 `700`，token/auth 文件权限为 `600`。规范布局如下：

```text
runtime/
├── gmail_tokens/
│   ├── account-1.json
│   ├── account-2.json
│   ├── account-3.json
│   └── backups/account-N/
├── outlook_tokens/
│   └── account-N.json
├── gpt_passwords.json
└── codex_auth/
    ├── <账号标识>.json
    └── backups/
```

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
