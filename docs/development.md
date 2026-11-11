# 正式版与测试版

架构分层、代码归属、数据边界和回退原则见[架构说明](architecture.md)。迁移机器需要的依赖、
配置和运行数据见[迁移环境清单](environment.md)。本页只保留开发者
日常启动、验证和同步所需的操作步骤。

正式入口 `python code-receive.py` 和测试入口 `python code-receive-test.py` 都加载
`mailapi_shared.application`。入口文件只选择运行模式；登录、路由、网站账号、资源聚合、
邮件监听和授权流程由同一份应用实现。正式版默认端口 8000，测试版默认端口 8001。测试版
只监听回环地址，远程调试时用 SSH 端口转发访问。

测试版首次启动：

```bash
cp .env.test.example .env.test
chmod 600 .env.test
# 编辑 .env.test，填写 TEST_ADMIN_PASSWORD，至少 8 个字符。
python code-receive-test.py
```

访问 `http://localhost:8001/login`，用户名为 `admin`，密码为上述独立密码。正式版管理员
还需要验证器动态码，配置步骤见[迁移环境清单](environment.md#正式版-env)。
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
frontend/                   Tiptap 静态包构建、锁定依赖和真实编辑器 DOM 测试
tests/                      Python 与前端回归测试
docs/                       开发流程
runtime/                    正式运行数据（不提交）
  test/                     仅供显式 TEST_DATA_DIR 场景使用的测试数据（不提交）
```

两版的共用应用实现集中在 `mailapi_shared/`。根目录模块和 `test_app/services/` 只保留
原有导入路径的兼容入口。两版只通过运行模式选择数据目录、初始登录配置和外部监听开关；
页面、登录接口和业务路由不再分叉。

测试版从根目录 `.env.test` 加载环境配置。默认复制 `runtime/` 中的资源、邮箱、Codex、凭据、
历史和规则数据，跳过网站登录控制数据、`runtime/test/`、正式邮件中继密钥和符号链接。
Gmail、Outlook 的 token 和 Proton 会话文件使用测试目录内的路径。配置指向快照之外时，
测试版使用自己的默认授权文件路径。测试版首次启动自己的 admin，默认关闭外部监听。
设置 `TEST_EXTERNAL_WATCHERS=1` 时请使用独立测试账号。正式版默认
使用 `runtime/`，也可通过 `MAILAPI_DATA_DIR` 指定独立目录；正式外部监听默认开启，使用
`MAILAPI_EXTERNAL_WATCHERS=0` 可暂时关闭。
自动测试可通过 `TEST_DATA_DIR` 使用指定的独立目录，由测试调用方负责清理。启动时会检查
该路径是否与正式数据目录重叠，并拒绝目录内指向外部的符号链接。此检查只从正式 `.env`
读取 `MAILAPI_DATA_DIR` 设置用于比较路径。

## 网页外观

主页、控制台和账户与分组页顶部的会话栏提供圆形外观图标按钮，不固定在视口。
显示器、太阳、月亮分别表示跟随系统、浅色、深色，点击依此顺序循环切换。
悬停或键盘聚焦时显示当前模式与下一项，按钮也提供对应的无障碍名称。
支持 View Transition 的浏览器切换时淡入淡出 220 毫秒；旧浏览器直接切换，开启「减少动态效果」时不播放动画。
支持浅色、深色、跟随系统，现有账户和新账户均默认跟随系统。
`GET/PUT /api/auth/preferences` 只读取或修改当前登录账户的 `theme`，保存到 `site_accounts.json` 的对应用户记录，
不修改分组、资源或会话。刷新、重新登录和换设备后读取同一账户偏好；管理员预览其他账户时仍沿用管理员本人的外观。
登录页也可切换，但未登录时只影响当前页面，登录后以账户偏好为准。

页面响应直接带上已保存的 `data-theme`，避免等待账户请求后才换色。共用 `static/theme.css/js` 管理主题，
原有浅色值作为 CSS 变量的回退值保留，深色集中定义颜色。选择跟随系统时由 `prefers-color-scheme: dark` 实时响应系统变化，
明确选择浅色或深色时不随系统变化；未声明偏好时使用浅色。`color-scheme` 同步浏览器表单控件和滚动条。
只公开两份主题静态文件供登录页使用，账户偏好接口仍要求登录并检查同源写入。

## 页面刷新与性能

主页的 GPT 状态保持每 3 秒刷新，Coding Plan 和中转站列表每 30 秒刷新；外部额度查询仍受
服务端 5 分钟缓存控制。控制台首次加载所有模块，之后当前模块每 5 秒刷新，全部资源列表每
30 秒更新一次供跨模块搜索使用。切换控制台模块立即刷新该模块。页面隐藏时暂停这些轮询，
返回页面时立即更新；已打开的接码和历史窗口保留原来的秒级刷新。

上述浏览器轮询读取服务器缓存，不等于向模型平台查询。Codex 账号独立使用 3–8 分钟随机间隔，
调度线程每 5 秒检查到期账号；网络请求耗时可能使实际执行延后。主页每个 Codex 工作区提供
“刷新额度”，与控制台和自动查询共用 `codex_quota_schedule.py` 中按 Codex 资源 ID 管理的
单进程冷却状态：一分钟内不重复查询，在途请求直接复用现有快照，失败也占用冷却窗口。
每次实际尝试重新安排下次自动查询；启动时参考已保存的上次核对时间，避免立即重复请求。
授权导入时的初次验证和必要的 Token 续期保留原流程，普通额度刷新不再强制换 Token。
Coding Plan 的查询间隔和缓存不受这项调整影响。

`POST /api/accounts/gpt/{account_key}/codex/{account_id}/quota` 在查询前后都检查账号上架、
分组权限和 Codex 所属关系；返回脱敏账号快照。冷却期间返回相同数据，不更新真实额度的
`usage.captured_at`，也不显示冷却倒计时。测试版关闭外部监听时拒绝触发真实查询；测试须模拟上游。

`static/page-performance.js` 按稳定资源标识复用表格行。数据没有变化时不替换 DOM，变化时只
替换对应行，删除和排序仍以服务端返回结果为准。主页展开的账号详情作为独立行更新。
搜索文本按模块缓存，数据变化时失效；输入结束 200 毫秒后过滤，多个数据更新合并处理，
仍搜索全部模块的可见文字、备注和链接地址。

Tiptap 静态包和编辑器脚本在第一次打开提示编辑窗口时加载，成功后在当前页面内复用。
下载失败可关闭窗口后重试。正式版和测试版共用这些行为，不改变运行数据格式或权限校验。

## 共用帮助提示

页面引入 `/static/help-hints.css` 和 `/static/help-hints.js` 后，可以复用以下结构。
每条提示使用独立的 ID。悬停或键盘聚焦时临时显示，点击问号固定显示，
再次点击或按 Escape 关闭。

```html
<span class="help-hint">
  <button type="button" class="help-hint-trigger" aria-label="功能说明"
    aria-expanded="false" aria-pressed="false" aria-controls="feature-help"
    aria-describedby="feature-help">?</button>
  <span id="feature-help" class="help-hint-content" role="tooltip"
    popover="manual" hidden>这里填写提示内容。</span>
</span>
```

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
在「账户管理」点击「按此账户预览」可核对指定用户的分组视图。创建和编辑单个账户时可填写最多 80 字的备注，
清空即移除。账户管理显示「用户名（备注）」，组别总览优先显示备注，缺省时显示用户名。备注只用于管理展示，
网站会话和分组仍按账户 ID 识别。

`/admin` 用滑块切换 GPT账号池、邮箱池、国产模型 Coding Plan 和中转站。GPT 登录名不要求是邮箱，
密码、2FA、监听邮箱均可选。邮箱授权、启停和收件设置集中在邮箱池；新邮箱不自动生成 GPT 资源。
GPT 的“监听邮箱”支持搜索、同名推荐、多选和清空，但同一邮箱只能归属一个 GPT 账号。
推荐只影响排序和标签，不会自动选择。解除绑定后撤销由该 GPT 授权产生的验证码及历史访问。

邮箱池的“上架接码”对应独立 `mailbox:<resource_id>` 资源，按现有分组流程分配。`resource_id`
与 `listed` 保存在 `runtime/gmail_accounts.json`，首次升级为各邮箱生成随机稳定标识并默认下架，
重新授权保留标识，上下架不改变监听状态。删除后复用邮箱编号会生成新标识，旧分组不自动获得新邮箱；
读取历史还会核对记录中的实际邮箱地址，防止编号复用时读到其他地址的旧记录。

主页已下线独立的「邮箱接码」区域和列表轮询。`GET /api/mailboxes` 保留，普通用户必须同时满足邮箱已上架和分组授权，
管理员可查看全部。该接口不返回邮箱授权文件、GPT 凭据或其配置标记。原有 GPT 邮箱绑定权限保留；
两种有效授权取并集，但 GPT 接码及 `GET /api/codes` 只返回明确的 OpenAI 验证码。
`GET /api/history/{name}` 默认 `scope=gpt`；`scope=mailbox` 返回所有收件规则的捕获历史，
必须具备独立邮箱权限。用户预览执行同样的分组和上架检查。OpenAI 规则按 `rule_id=openai` 判断，
兼容没有规则 ID 但名称为“OpenAI 验证码”的旧记录；无来源标记的旧记录只保留在完整邮箱历史中。
接码接口沿用原有收件历史及容量限制。

Gmail 按页查询增量和未读补扫。增量读完所有页后才保存游标，失败时从上次保存的位置重试。
游标失效时先取新基准再补扫未读邮件，补扫成功后从该基准继续查询增量。

邮箱池每个邮箱的“接码说明”可由管理员编辑，支持最多 500 字文本，留空清除，保存在
`runtime/gmail_accounts.json` 的 `note` 字段中。控制台在邮箱地址下方展示，
按纯文本显示，支持换行。说明沿用邮箱上架和分组权限，不随 GPT 接码接口返回；说明仅提示用途，
不改变收件规则、上架状态或监听。Gmail、Outlook 重新授权保留最新说明。

中转站账号的密码和接码邮箱均选填，名称、网址和账号必填。编辑时密码留空保留原值。
管理员选择接码邮箱和对应平台的收件规则后，主页“登录方式”显示“验证码”按钮。后端用规则 ID 筛选
最新结果和历史，只显示该规则捕获的记录。已有邮箱绑定需补选规则，规则删除后需重新选择。
弹窗按中转站分组授权，无需邮箱独立上架或分配。GPT 账号和中转站继续通过各自入口提供接码。
邮箱和收件规则分别用 `mailbox_key` 和 `mail_rule_id` 保存。Coding Plan 的 API Key 仍必填。

两处管理页的滑块滚动到页面顶部后吸顶，组别总览仍在上方时保持原位。各功能面板分别记忆本次页面
打开期间的浏览位置，首次切换到新面板从内容顶部开始；刷新或重新打开页面后回到首个面板和页面顶部，
不在浏览器存储中保存位置。

两处管理页通过共享的 `management-search.js/css` 搜索本页全部功能面板，按文字和链接地址匹配，
不区分大小写，多个关键词用空格分隔并同时匹配。各模块显示命中数并支持点击跳转，清除后恢复
原列表和顺序；后台刷新列表后仍保留筛选。搜索只改变界面展示，不改变账号、分组或勾选项。
组别总览第二行显示最早到期的 GPT 账号及其剩余天数，日期均未知时显示首个账号，搜索仍包含其他账号名。
总览与编辑小组的剩余天数标签在 5 天及以内和已到期时标红。
总览格子的编辑窗口也可分配资源，表格仍只显示成员和 GPT 账号摘要。
分组配置在桌面上按成员、资源左右排列，手机上纵向排列。资源按 GPT、邮箱接码、Coding Plan、中转站分段；
GPT 候选项的 `x Group shared` 按已保存的分配计算，包含当前分组，不受搜索筛选影响。

Group 分组的“批量分配”以一个资源为单位：左侧选择资源，右侧自动勾选当前已分配的全部分组，
支持逐组调整、全选和全不选，点击“保存分配”统一生效。保存后窗口保留，可继续选择其他资源；
未保存就切换资源时提示是否放弃修改。候选包含下架资源，但分配不会自动上架。
左右候选栏分别支持资源和分组搜索，不区分大小写，空格分隔的关键词同时匹配；资源还可按类型查找。
筛选只隐藏候选项，保留当前资源、全部已勾选分组和未保存修改。全选／全不选仅作用于匹配分组，
保存时提交包含筛选外勾选项在内的完整分配；清空搜索可恢复全部候选，关闭后重新打开窗口会重置搜索。
后端在同一次原子写入中校验并调整所选资源的分组关系，保留分组顺序、名称、成员和其他资源，
也保留其他资源的旧失效引用。admin 和普通管理员均可操作，普通用户无权限。

GPT 订阅日期从现有授权文件导入的 `id_token` 中解析，由 `mailapi_shared/subscription_dates.py`
集中处理日期格式。读取 `https://api.openai.com/auth` 下的
`chatgpt_subscription_active_start`、`chatgpt_subscription_active_until`、
`chatgpt_subscription_last_checked`；截止日期必须有效，开始日期和核对时间可缺省。
解析前严格核对账号及工作区，不借用默认账号或其他工作区的日期。

启动时读取授权文件中的日期信息，即使文件日期比运行缓存新，也不覆盖当前 access/refresh token。
已有多个本地来源时保留核对时间较新的匹配记录；重新读取、文件修改、后台额度刷新均不改写核对时间。
授权更新产生新的 ID Token 时自然更新日期记录；不主动刷新凭据来填补订阅日期。
ID Token 的 `exp` 不是会员截止时间，也不阻止历史授权日期展示；这些数据仅用于信息展示。

页面使用首个有可用授权日期的关联，显示“订阅到期：`YY.MM.DD (X Day left)`”、来源“授权文件 ID Token（到期日）”
及授权上次核对时间。不限制授权或核对时间的长短，核对时间缺失或无法解析时显示“无记录”，
不影响有效到期日的采用。旧授权可能未反映后续续费；到期后显示原日期和“已到期”，不自动推算下一期。
只有截止日期缺失、日期无效或账号不匹配时才回退手填；原 7 天门槛及 `valid_until` 字段已移除。
订阅日期读取不发起联网请求，原网页订阅接口及其定时轮询已移除，
`CODEX_SUBSCRIPTION_REFRESH_INTERVAL_SECONDS` 不再使用；额度和授权刷新流程保留。

读取不到自动日期时，回退到手填订阅到期日（`subscription_expires_on`）。旧数据中的
`subscription_started_on` 仍按一个自然月派生 `subscription_ends_on`，月底取目标月的最后一个有效日期。
编辑框预填当前手动到期日。更新或清空手动到期日会替换原有手填记录。页面按浏览器本地日历显示剩余天数，
到期前 5 天及以内标红。

### GPT 共享编辑与修改记录

分组成员可从主页编辑已上架且分配给自己的 GPT 账号。保存立即生效，权限和表单版本在保存时重新核对。
登录账号、密码、2FA 和手填到期日一起保存。Codex `auth.json` 单独上传，校验 OpenAI 签名和目标身份，
已有 Codex 关联时核对对应工作区。接码绑定和上架状态由控制台管理。

账号控制台的「修改记录」按稳定的 GPT 账号标识查询，支持关键词和管理员、用户、系统、已审核、恢复标签。
记录长期存放在 `account_credentials.json` 的 `history` 字段，仅记录操作人、时间、日期或字段变化摘要。
订阅自动到期日发生实际变化时记一次，管理员复核和恢复分别追加独立事件。

管理员可逐个恢复登录账号、密码、2FA、手填到期日或单个工作区的 Codex 授权。每个字段只保留最近一次变动前的恢复点。
旧版记录、自动订阅日期与邮箱接码设置没有恢复点。恢复 Codex 授权只替换本地文件和运行记录，已失效的授权仍需重新授权。
恢复点加密存放在 `account_credentials.json` 的 `restore_points`，API 只返回可恢复的字段名称。
首次写入恢复点时创建同目录 `account_credentials.restore.key`（权限 `0600`）。迁移或备份运行数据时应受限保管该密钥，
并与 `account_credentials.json` 配套恢复。测试版的默认临时快照排除正式版恢复密钥，测试修改会生成独立的恢复点。

### Codex 使用提示

账号主页的目录 02 显示所有已登录用户可看的提示标题和首图缩略图，点击后在弹窗阅读图文正文。
管理员和普通管理员可在控制台的「Codex 使用提示」页签新增、修改和删除。新增提示排在最前，修改保留位置。
提示保存在当前运行模式的数据目录 `codex_tips.json`。文件尚未创建时显示原有设备数量提示，删除全部条目后显示空列表。

编辑器使用 Tiptap 3，顶部常驻工具栏支持段落、加粗、斜体、链接、小标题、有序／无序列表、代码块、图片和撤销／重做。
正文独立滚动，窄屏时工具栏换行。图片说明直接在图下编辑，回车进入图片后的正文，格式操作不会把图片替换成代码块或标题。
阅读详情继续由 PhotoSwipe 放大图片。
旧 `body` 仍按纯文本转成段落读取，不在加载时改写原文件；保存新内容时写入经过白名单校验的 `blocks`，
并保留纯文本 `body` 用于列表摘要和搜索。正文文字上限仍为 8000 字，最多 100 个内容块。
`codex-tip-format.js` 负责 Tiptap 文档和现有块格式互转，不保存另一份编辑器数据。列表保留可选的 `meta.start/type` 编号，
嵌套列表通过条目的可选 `style` 保存不同类型，旧条目缺省时沿用上层类型。
图片可从文件选择器上传或粘贴图片文件；不代取外部图片 URL，不接受任意 HTML。
上传期间可继续编辑文字，插入位置随文档变动同步；上传结束前禁止保存或关闭编辑窗口，失败时保留原正文并显示错误。

图片处理集中在 `mailapi_shared/codex_tip_content.py`：单张最多 8 MB、2400 万像素，接受 JPEG、PNG、WebP、GIF、HEIC/HEIF、AVIF，
校正方向、移除元数据后转为 WebP。正文图限制在 2560 × 12000 像素内，另生成 480 × 1200 内的缩略图，保持原比例。
PNG/GIF 使用无损 WebP，其他格式质量设为 85；动态图只保留首帧。图片保存在当前数据目录的 `codex_tip_images/`，
通过需要登录的 `/api/codex-tip-images/{filename}` 读取，上传仅限两类管理员。PhotoSwipe 在详情弹窗内提供放大浏览。
测试模式的图片和提示同属临时快照，退出时一起清理。删除提示或取消编辑不会自动删除已上传图片，避免破坏复用图片；
备份时需将 `codex_tips.json` 和 `codex_tip_images/` 一起保存。

浏览器依赖固定在 `mailapi_shared/static/vendor/`，不请求外部 CDN；版本、来源、完整性校验值及许可证见 `vendor/manifest.json`。
Tiptap 在 `frontend/package-lock.json` 锁定，构建包的全部运行依赖及许可证保存在 `vendor/tiptap/`。
日常修改界面只需修改 `static/codex-tip-editor.js`、`codex-tip-format.js` 和 CSS；升级 Tiptap 或重新生成包时执行：

```bash
npm --prefix frontend ci --ignore-scripts
npm --prefix frontend run build
npm --prefix frontend test
```

`frontend/test/` 使用真实 Tiptap 和 PhotoSwipe 的 jsdom 测试，不需要浏览器或正式数据。生成的静态包随代码提交并同步，服务器无需安装 npm 依赖。

### Coding Plan 配置资料

首页和控制台共用 `static/coding-plan-config.js`，按平台索引中文文档和国内套餐 Base URL。
API Key 仍保持脱敏，仅在点击旁边的复制按钮时通过原有鉴权接口读取；复制 Base URL 不发起模型调用。
表格将 API Key 和 Base URL 分成两列，Base URL 按协议列出，不能把所有工具配置成同一个地址。
以下官方资料核对于 2026-11-07，模型名单以文档为准，避免在页面固定过期的型号：

| 平台 | 官方资料 | 国内套餐 Base URL |
| --- | --- | --- |
| 小米 MiMo Token Plan | [模型与接入说明](https://mimo.mi.com/docs/zh-CN/tokenplan/Token%20Plan/subscription) | OpenAI：`https://token-plan-cn.xiaomimimo.com/v1`；Anthropic：`https://token-plan-cn.xiaomimimo.com/anthropic`。另有海外集群，以套餐控制台为准 |
| 智谱 GLM Coding Plan | [模型与调用说明](https://docs.bigmodel.cn/cn/coding-plan/latest-model)、[协议入口](https://docs.bigmodel.cn/cn/coding-plan/quick-start) | Chat Completions：`https://open.bigmodel.cn/api/coding/paas/v4`；Responses：`https://open.bigmodel.cn/api/v1`；Anthropic：`https://open.bigmodel.cn/api/anthropic` |
| Kimi Code | [模型与调用说明](https://www.kimi.com/code/docs/kimi-code/models.html) | OpenAI：`https://api.kimi.com/coding/v1`；Anthropic：`https://api.kimi.com/coding/` |

在 `/admin` 的国产模型 Coding Plan 区域编辑记录，选择智谱 GLM Coding Plan、Kimi Code 或小米
MiMo Token Plan 的国内站。已有记录首次设置平台时可沿用对应的套餐 API Key。小米需要个人版
Token Plan 的 `tp-` 专用密钥，页面显示周期剩余比例及周期结束时间。
首页和后台在可见期间自动查询已配置套餐。服务端按套餐共享查询结果，每 5 分钟最多向平台查询一次。
查询失败时显示错误及上次成功的结果。测试版默认关闭外部查询，避免使用快照中的正式套餐密钥。

账号、分组和会话写入临时目录中的 `site_accounts.json`，采用原子写入和文件权限 600。
网站密码使用带独立盐的 PBKDF2-SHA256 哈希，登录 cookie 只携带随机会话 token，
服务器只保存 token 摘要；会话有效期为 24 小时。退出、删除账号或修改密码会使相关旧会话失效，
分组和角色变化从下一次请求起生效。服务保持单进程运行；不要设置多个 Uvicorn worker。

管理员凭据校验失败时，在数据目录的 `logs/errors.log` 追加 `admin_login_failure` 和固定失败类别。
页面继续返回统一提示。诊断记录不含用户名、IP、密码、验证码或密钥。

主要接口：

| 接口 | 用途 |
| --- | --- |
| `POST /api/auth/login` | JSON `username`、`password`，正式版管理员还需 `totp_code`。响应设置会话 cookie |
| `GET /api/auth/me` | 当前账号和所属分组 |
| `POST /api/auth/logout`、`POST /api/auth/password` | 退出、修改本人密码 |
| `GET/POST /api/admin/users` | 列表、创建网站账号 |
| `POST /api/admin/users/batch` | 仅 admin 批量创建用户或普通管理员，共用一个初始密码 |
| `PUT/DELETE /api/admin/users/{id}` | 重置密码、调整角色、删除网站账号 |
| `GET/POST /api/admin/groups` | 列表、创建分组 |
| `PUT/DELETE /api/admin/groups/{id}` | 编辑、删除分组 |
| `GET /api/admin/resources` | 可分配资源目录（不包含凭据） |
| `PUT /api/admin/mail/accounts/{name}/note` | 管理员设置用户可见的邮箱接码说明，JSON `note`，最多 500 字，空字符串清除 |
| `GET /api/relay-accounts/{id}/mailbox` | 按中转站授权读取所绑定邮箱及收件规则的最新结果和历史 |
| `PUT /api/admin/resource-groups` | 批量设置一个资源的分组：`resource_key`、`group_ids`；空列表取消该资源的全部分配 |
| `POST /api/admin/gpt/accounts` | 创建独立 GPT 账号，`login` 必填，`password`、`secret`、`listed`、`mailbox_ids` 可选 |
| `PUT /api/admin/gpt/accounts/{key}` | 编辑 GPT 账号；密码和 2FA 留空保留原值，清除使用原有删除接口 |
| `POST /api/admin/gpt/accounts/{key}/changes/{event_id}/restore` | 管理员恢复该条记录的指定 `field`，字段已有更新时返回 `409` |
| `GET /api/admin/gpt/accounts/{key}/mailboxes` | 邮箱选项、推荐、选中及占用状态，仅管理员可读 |

GPT 资源 key 来自 `/api/admin/resources`，不应根据当前登录名自行构造。旧记录保留 `gpt:email:...`，
新记录使用 `gpt:account:...`；改名保持 key 不变，删除后重建会生成新的 key。

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
使用 SSH 转发时可以在 `.env.test` 设置 `TEST_GMAIL_OAUTH_REDIRECT_BASE` 和 `TEST_APP_BASE_URL`，
使回调地址与浏览器使用的本机端口匹配。
Gmail 自动回调使用 GET，根路径接收 `state` 加 `code` 或 `error`，旧版
`/oauth2callback/{account_name}` 仍兼容。桌面 OAuth 客户端返回 `localhost` 时，
远程使用可通过端口转发，或在控制台提交完整返回链接；手工提交接口仍要求管理员权限和同源请求。
Google Cloud 的 Web OAuth 客户端则可登记 HTTPS 公网回调并设置 `GMAIL_OAUTH_REDIRECT_BASE`。
Outlook 在 `.env.test` 配置 `OUTLOOK_CLIENT_ID`、`OUTLOOK_CLIENT_SECRET` 和独立的
`OUTLOOK_OAUTH_REDIRECT_URI`，指向测试服务的 `/api/admin/outlook/auth/callback`。
Microsoft 使用 `form_post` 跨站提交授权结果，因此仅该回调路径的 POST 不走普通写请求的
同源校验，也不要求携带网站登录 Cookie；回调必须匹配本站保存的一次性随机 `state`，
再由 MSAL 校验原始授权流程后换取令牌。启动授权及其他写接口仍受原有同源和权限校验保护。
若旧回调被“不允许跨站请求”错误拦截，Microsoft 端同意授权不代表邮箱已入池，
修复后需要重新点击“添加 Outlook”完成一次新的授权。

新增 Gmail、Outlook 时各自生成独立的随机内部编号及授权文件路径，互不复用待完成会话的编号；
已有邮箱的编号、资源标识、上架状态、接码说明和分组关联保持不变。重复添加同一邮箱更新原记录，
重新授权会核对邮箱地址（不区分大小写）。停用或移除邮箱会移除待处理的授权会话，之后收到的旧回调不能重新启用邮箱。
邮箱列表的快照和原子落盘使用同一把锁，避免并发保存时较旧列表覆盖较新列表。
`tests/test_mail_oauth_flows.py` 模拟上游令牌与邮箱资料，覆盖自动回调、手动提交、重复回调、
重新授权、错误账号、重复添加、多会话隔离、停用取消、保存失败和并发落盘；不访问真实邮箱。

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
MAILAPI_UI_ROOT=mailapi_shared/static node --test tests/test_access_ui.cjs
MAILAPI_UI_ROOT=mailapi_shared/static node --test tests/test_theme_ui.cjs
```
