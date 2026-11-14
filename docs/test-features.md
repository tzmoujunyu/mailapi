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

## 财务申请

用户在测试版页面提交报销申请。项目和金额必填，发票必须上传，支付记录截图可选。允许 PDF、JPG、PNG 和 WebP，单个文件不超过 10 MB。提交时保存用户名备注和当前分组名称快照，后续修改网站账户备注或分组不会改变历史申请的归属信息。

管理员可以审核、驳回、置顶公告，并批量下载申请。下载规则如下：

- 单文件名为 `分组_用户名备注_报销项目_发票/支付记录.扩展名`。
- 压缩包内按用户放置目录，目录名为 `分组_用户名备注`。
- 压缩包名称为 `YYYYMMDD报销申请.zip`。
- 单个申请同时提供压缩包和单文件下载。

申请元数据、审核记录和公告写入测试功能 SQLite，上传文件使用随机存储名并置为私有权限，下载时才生成用户可读文件名。

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
