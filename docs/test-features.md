# 测试版 API2API 与财务原型

这两个功能只在 `MAILAPI_MODE=test` 下注册，数据写入测试数据目录中的
`test_features.sqlite3` 和 `finance_files/`。正式版不会注册 `/test-features`、
`/api2api/v1/` 和这些财务接口。

## API2API

管理员在测试版页面配置一个 NewAPI OpenAI 兼容 Base URL，例如
`https://newapi.example.com/v1`，并保存上游 API Key。测试版要求配置独立的测试凭据密钥，
上游 Key 以现有 AES-GCM 密钥封装后写入测试功能数据库。网站用户可以创建自己的本地 API Key，
原始 Key 只在创建成功时显示一次，数据库只保存哈希和前缀。

用户客户端使用下面的地址调用：

```text
Base URL: https://网站地址/api2api/v1
Authorization: Bearer <网站生成的 API Key>
```

请求会异步转发到 NewAPI，使用共享连接池，最多保留 200 个并发连接。非流式响应从 `usage` 读取输入、输出和总 Token，流式响应会解析 SSE 中的 usage 片段。每个网站 API Key 关联网站账户，SQLite WAL 表记录用户、模型、状态码、耗时和 Token 汇总。当前原型只统计 Token，不做计费、余额或用户额度限制。

NewAPI 本身提供 OpenAI 兼容接口、API Key、模型组和用量日志，适合作为上游网关。[NewAPI README](https://github.com/unorouter/new-api/blob/main/README.en.md)、[API Key 文档](https://github.com/QuantumNous/new-api-docs-v1/blob/main/content/docs/en/guide/feature-guide/user/token.mdx)、[Token 用量接口](https://github.com/QuantumNous/new-api-docs/blob/main/docs/api/token-usage.md)

### NewAPI 引擎接入验证

目标是先接入独立 NewAPI 网关的功能，再改进本站 API 工作台，不直接嵌入整套 NewAPI 界面。现有网站转发仍为上文的共享上游密钥原型；原生 NewAPI 密钥与网站用户的关联方式尚待确认，不能把基础引擎验证视为网站已完成接入。

`scripts/verify_newapi_engine.py` 使用官方 macOS arm64 `v1.0.0-rc.51` 发行文件，并在执行前校验固定 SHA-256。它仅在回环地址启动网关和本地确定性上游，创建独立临时数据库及随机测试管理员，验证原生密钥、普通请求参数和响应内容，以及响应结束前的流式交付。退出时关闭两个服务并清理临时数据库；不访问网站运行数据，不连接真实模型或进行真实计费。该检查不代表真实上游的性能或生成质量基准，测试发行候选版也不自动成为正式部署版本。

本地验证命令（先自行下载官方发行文件）：

```bash
uv run --offline --python .venv/bin/python python scripts/verify_newapi_engine.py \
  --binary /path/to/new-api-macos-v1.0.0-rc.51
```

## 财务申请

用户在测试版页面提交报销申请。项目和金额必填，项目最多 20 个字符，发票必须上传，支付记录截图可选。允许 PDF、JPG、PNG 和 WebP，单个文件不超过 10 MB。上传接口允许两个文件同时提交，文件名不设应用层长度截断。

“我的申请”始终只显示当前网站账户提交的申请，管理员也遵循此规则。待审核申请可修改或撤销；已驳回申请可以修改后重新提交，生成新版本并回到待审核。已通过及已撤销申请锁定。驳回必须填写原因，编辑和审核携带版本号，旧页面的操作发生冲突时返回 409，不覆盖新版本。

账户备注和当前分组在读取、页面显示及普通下载时动态查询。费用归属另行保存：未分组账户记为“未分组”，单分组自动填写，多分组必须从本人所属分组选择一个。历史费用归属不随日后换组而改变，金额只计入一个费用组。已有申请迁移时只有一个历史组名的保留该名称，多分组但缺少费用归属信息的记为“待确认”，不猜测归属。

管理员（含普通管理员）使用“申请审核 / 统计分析 / 审批记录 / 公告管理 / 我的申请”五个页签。审核默认全部时间、待审核，统计默认本月；支持日、周、月、自定义范围，关键词、状态、费用组、付款人、审核人及金额筛选。审核历史使用审核时间，其他列表与金额统计使用提交时间。金额以整数分计算，申请总额排除已撤销项。通过仅表示审核通过，不表示付款。表格每页最多显示 25 行，图表在本地绘制，无外部图表加载依赖。

公告支持标题阅读、历史弹窗、发布、编辑、删除和置顶，与顶栏网站公告独立。用户文本及附件名按纯文本显示。

### 版本记录和附件下载

- `finance_applications` 保存当前状态、版本号、费用归属及当前资料；`finance_files` 关联当前附件。
- `finance_versions` 保存每个版本的申请和附件元数据快照；`finance_events` 追加记录提交、修改、重提、撤销、审核的操作者、时间及意见。应用接口不覆盖旧快照或删除历史事件。
- 替换附件保留旧物理文件，使旧版本仍可下载。历史附件暂不自动清理，随隔离测试目录整体清理。
- 单文件名为 `分组_用户名备注_报销项目_发票/支付记录.扩展名`，普通下载按当前账户信息命名。
- 审批记录只可勾选已通过记录，最多一次 500 条，导出该次审核对应版本的发票和支付记录，并附 `审批清单.json`（版本、审核人、审核时间、金额、归属和意见）。历史导出按当时的申请身份命名，不用后来替换的文件。
- 缺失附件返回 410，整包取消，不静默生成不完整的压缩包。普通用户只能读取本人申请及其版本、附件；管理员可审核及查看全部记录。

以上均为测试版实现。申请、历史、公告写入隔离的 `test_features.sqlite3`，上传附件使用随机存储名和私有权限 `0600`，目录 `0700`。它们不复制回正式数据目录，也不改变正式版数据库结构。测试启动仍从当前本地正式数据创建临时快照；API2API 与财务没有正式对应数据，初始为空。验证或预览生成的数据仅保存在测试目录，正常退出时自动清除。

## 测试方式

测试版需要独立数据目录和测试密钥。API2API 配置要求同时启用测试 OAuth 加密：

```bash
TEST_DATA_DIR=/path/to/test-data \
TEST_CREDENTIAL_KEY_FILE=/path/to/test-key.json \
TEST_OAUTH_ENCRYPTION=1 \
TEST_SITE_ACCOUNTS_BACKEND=sqlite \
TEST_RESOURCE_BACKEND=sqlite \
TEST_EXTERNAL_WATCHERS=0 \
python code-receive-test.py
```

登录后打开 `/test-features`。测试进程退出后，临时测试目录按现有隔离规则清理。
