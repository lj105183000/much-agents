# 贡献指南

感谢为本项目提交问题、文档或代码。

## 许可与贡献授权

本项目以 **GNU Affero General Public License v3.0 or later（AGPL-3.0-or-later）** 发布。提交贡献（Pull Request、补丁、文档等）即表示：

1. 你有权提交该内容，且该内容不侵犯第三方权利；
2. 你同意该贡献按 AGPL-3.0-or-later 授权给本项目及所有接收者；
3. 你理解 AGPL 的源码提供义务同样适用于包含你贡献的修改版部署。

当前**不要求**签署 CLA。若未来需要商业许可或双许可，维护者必须另行取得贡献者的明确同意；仅凭 AGPL 贡献不能由维护者单方面改为闭源商业许可。

## 第三方代码与依赖

- 不要提交与 AGPL-3.0 不兼容的第三方代码（例如仅允许闭源分发的组件）。
- 新增或升级依赖时：
  - 确认许可证与 AGPL-3.0 兼容（MIT、BSD、Apache-2.0、MPL-2.0、ISC、PSF 等通常可以）；
  - 更新根目录 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) 的组件表、版权行与许可证附录；
  - 如需新增许可证类型，补充该许可证全文与上游链接。
- PyMuPDF 以 AGPL-3.0 或 Artifex 商业许可双许可发布；不要假设可以闭源分发包含 PyMuPDF 的构建。

## 提交前检查

```bash
uv run ruff check .
uv run pytest
cd frontend && npm run lint && npm test && npm run build
```

涉及 Docker/部署的改动还应执行：

```bash
docker compose -f docker-compose.prod.yml config
```

## 数据与密钥

- 不要提交 `.env`、`deploy/env.production`、TLS 私钥/证书、备份、上传数据、`chroma_db/`、`node_modules/`、`dist/`、`release/`、真实 `mcp_servers.json` / `.mcp.json`。
- 不要提交真实招标文件、投标人资料、账号口令、token 或任何生产数据；测试使用合成数据。
- `profile.json` 只允许存在于本地 run 目录与 MinIO 私有 key，不得进入 artifact allowlist、MCP 输出、审计 detail、Redis/Celery payload 或下载 URL。

## 代码风格

- 后端：Python 3.12，遵循现有模块边界；长任务走 Celery/任务模型，不阻塞 HTTP 请求。
- 前端：React 19 + TypeScript + TailwindCSS 4；保持现有组件与状态管理模式。
- 不要批量添加版权头；版权与许可证以根目录 `LICENSE`、`NOTICE`、`THIRD_PARTY_NOTICES.md` 为准。

## 安全问题

不要通过公开 Issue 报告安全漏洞或粘贴密钥、真实数据。请通过仓库托管平台的安全报告渠道或维护者公开的联系方式私下联系。
