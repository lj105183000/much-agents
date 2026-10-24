# Much Agents

多 Agent 标书编写系统：把招标文件 PDF 解析为结构化知识，检索相关章节，由多个 Agent 生成投标文件，并完成合规检查、跨章节一致性核对、AI 配图与严格门禁导出。

> 本项目以 GNU Affero General Public License v3.0 or later（AGPL-3.0-or-later）发布。
> 源码：<https://github.com/lj105183000/much-agents>。
> 生产部署与本轮验收边界见 [`docs/m1-acceptance-report.md`](docs/m1-acceptance-report.md)。

## 主要能力

- **文档解析**：MinerU 云端解析（PyMuPDF 兜底）、目录/页码对齐、表格与页脚清理、DeepSeek 结构化抽取、向量入库。
- **检索**：Chroma 语义检索，Chroma/智谱不可用时自动回退关键词检索。
- **多 Agent 写作**：确定性大纲 → 逐节检索 → Writer/Reviewer/Reviser → 组装；支持可编辑大纲、章节续跑、单节编辑与重写。
- **质量与门禁**：21 项合规检查、跨章节一致性台账、确定性/LLM 修复方案、导出前门禁与严格 DOCX 导出。
- **AI 配图**：组织架构、工艺流程、进度横道、施工总平面；只使用招标事实与已脱敏正文，不文生图、不编造事实。
- **认证与隔离**：本地账号、可撤销会话、5 角色 RBAC、项目级 ACL、append-only 审计哈希链。
- **桌面与 Web**：React 19 + Vite + Electron 工作台；Nginx 生产托管与 API 反向代理。
- **MCP**：MCP 服务端（stdio/HTTP）与白名单客户端（外部证据、企业资料、外部绘图）。
- **生产运维**：Docker Compose、TLS、健康检查、备份/恢复脚本、CI 与安全扫描。

## 架构

```text
PDF ──► 解析/抽取 ──► Chroma 向量库 ──► 多 Agent 写作 ──► 合规/一致性/门禁 ──► DOCX
                                      │
                    FastAPI + Celery + PostgreSQL + Redis + MinIO
                                      │
                              React / Electron 工作台
```

| 目录 | 内容 |
|---|---|
| `api/` | FastAPI 路由、认证/RBAC、投标 run、审计、MCP 服务端 |
| `src/parsing/` | PDF 解析、结构对齐、抽取、分块、入库 |
| `src/bidding/` | LangGraph 写作、大纲、合规、一致性、修复方案、配图 |
| `src/storage/` | 本地 / MinIO 对象存储与 hydration |
| `src/mcp_client/` | 白名单 MCP 客户端 |
| `migrations/` | Alembic 迁移（`0001`–`0005`） |
| `frontend/` | React 19 + TypeScript + TailwindCSS 4 + Electron |
| `deploy/` | Nginx/TLS、生产 Compose 环境模板 |
| `scripts/` | 一键启动、备份/恢复、worker 健康检查 |
| `docs/` | 认证、项目 ACL、审计、对象存储、部署、AI 配图/MCP |
| `tests/` | 后端单元/接口测试（全部 mock 网络） |

## 快速开始

### 依赖

- Python 3.12（由 `uv` 管理）
- Node.js 22+
- Docker Desktop（PostgreSQL/Redis/MinIO；Chroma 可本机运行）
- 模型密钥：`DEEPSEEK_API_KEY`、`ZHIPU_API_KEY`、`MINERU_API_KEY`

### 后端

```bash
uv sync
cp .env.example .env            # 填写模型密钥；.env 永不入库
uv run alembic upgrade head     # 应用 PostgreSQL 迁移
uv run chroma run --path ./chroma_db
uv run uvicorn api.main:app --host 0.0.0.0 --port 8001
```

默认使用 Celery + Redis；Windows worker 必须 `--pool=solo`：

```bash
uv run celery -A api.celery_app:celery_app worker -Q parse,bid --pool=solo --loglevel=INFO
```

无 Redis/worker 的本机模式：

```bash
TASK_BACKEND=local uv run uvicorn api.main:app --port 8001
```

首次使用需创建管理员：

```bash
uv run python -m api.users create-admin --username admin --display-name 管理员
```

### 前端

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
npm run electron:dev # Electron + Vite
```

Windows 一键启动（Docker 依赖 + 迁移 + Chroma + API + worker + Vite）：

```text
start.bat            # 完整本地环境
start.bat local      # 无 Redis/worker/MinIO 的本地模式
start.bat prod       # 构建前端并由 Nginx 托管到 8080
stop.bat             # 停止 start.bat 拉起的进程
```

### 生产部署

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

生产只通过 Nginx 暴露 80/443；API/worker 非 root、只读根文件系统。部署前请阅读
[`docs/deployment.md`](docs/deployment.md)，并使用 `deploy/env.production.example`
与真实 TLS 证书。`ENVIRONMENT=production` 会在启动时拒绝不安全配置。

## 安全边界

- `.env`、`deploy/env.production`、TLS 私钥、备份与上传数据均在 `.gitignore` 中，禁止提交。
- 默认 `AUTH_ENABLED=true`；仅 `GET /api/health`、`/api/live`、`/api/ready` 与登录接口公开。
- 业务请求必须带 `X-Project-Id`；跨项目资源按 404 处理，不泄露项目存在性。
- `profile.json` 只存私有对象 key，永不进入 artifact allowlist、MCP 输出、审计 detail、Redis/Celery payload 或下载 URL。
- 外部 MCP 证据默认不可信；外部绘图默认关闭。
- 审计只记录元数据，不记录密码、token、投标人资料或正文。

## 测试与检查

```bash
uv run ruff check .
uv run pytest
cd frontend && npm run lint && npm test && npm run build
npm audit --omit=dev --audit-level=high
```

## 文档

- [认证、会话与 RBAC](docs/authentication.md)
- [项目与对象级 ACL](docs/projects.md)
- [审计哈希链](docs/audit.md)
- [MinIO 对象存储](docs/minio-storage.md)
- [生产部署与运维](docs/deployment.md)
- [AI 配图与 MCP](docs/ai-figures-and-mcp.md)
- [产品化路线图](docs/productization-roadmap.md)
- [M1 生产验收报告](docs/m1-acceptance-report.md)
- [贡献指南](CONTRIBUTING.md)
- [第三方组件与许可证](THIRD_PARTY_NOTICES.md)

## 状态与未实现项

P0 内网小团队上线闭环已实现，并完成本地 Docker Desktop 生产验收（认证/RBAC、项目隔离、审计、MinIO 降级、重启持久化、备份恢复）。尚未完成：干净 Linux VM 与真实 DNS/CA 验收、远程 CI 结果确认、完整 LLM 正向生成/组装/导出演练、PDF/DOC 导出、审批流与版本 diff、评论/SSE、监控告警、前端 E2E 与压测、MCP OAuth 与前端配置 UI。

## License

Copyright (C) 2026 lj105183000。

本项目以 **GNU Affero General Public License v3.0 or later（AGPL-3.0-or-later）** 发布：

- 许可证全文：[`LICENSE`](LICENSE)（GNU 官方 AGPL-3.0 原文）。
- 版权与源码地址：[`NOTICE`](NOTICE)。
- 第三方组件归属：[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

AGPL §13 要求通过网络向用户提供服务时提供对应源代码。本项目在登录页与已登录页脚展示源码链接，默认指向 <https://github.com/lj105183000/much-agents>；修改版部署必须通过 `VITE_SOURCE_URL` 指向自己发布的对应源码（见 [`docs/deployment.md`](docs/deployment.md) 第 13 节）。

**PyMuPDF 双许可提示**：本项目硬依赖 PyMuPDF 2.0，它以 AGPL-3.0 或 Artifex 商业许可双许可发布。本项目按 AGPL-3.0 使用；如需闭源或商业授权分发，必须向 Artifex 购买商业许可。

以上内容不构成法律意见；商业闭源托管或分发前请咨询专业律师。
