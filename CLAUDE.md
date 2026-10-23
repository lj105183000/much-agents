# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 固定任务

- 每次修改完成后，都应创建一个对应的 Git commit，便于后续追踪和回滚。

## 项目概述

多 Agent 标书编写系统（Much Agents）：解析招标文件 PDF → 结构化抽取 → 入 Chroma 向量库 → 由 LangGraph 多 Agent 生成投标文件（Markdown + Word），前端为 React + Electron 桌面端。技术栈参考 [ZhiBiao](https://github.com/Foulereee/ZhiBiao)。

**当前状态**：已实现文档解析管线 `src/parsing/`、FastAPI 上传/任务接口 `api/`、Celery/Redis 异步任务后端（含 local 内存兜底）、多 Agent 投标文件编写 `src/bidding/`、React/Electron 前端工作台与测试 `tests/`。PostgreSQL 持久化、MinIO/Nginx、前端 IPC 与对外检索接口仍未实现，后续阶段以本文件与代码为准。

## 常用命令

### 后端（项目根目录，uv 管理，Python 3.12）

```bash
uv sync                                      # 安装/同步依赖
uv run chroma run --path ./chroma_db         # 启动 Chroma，监听 8000
uv run pytest                                # 运行全部测试（全部 mock 网络）
uv run pytest tests/test_toc.py::test_hierarchy_and_end_pages   # 单个测试
uv run python -m src.parsing upload/6055594244948.pdf           # 解析 + 抽取 + 入库
uv run python -m src.parsing <pdf> --parser pymupdf --no-extract --no-ingest  # 离线仅解析
uv run uvicorn api.main:app --host 0.0.0.0 --port 8001          # 启动 API（单进程）
uv run python -m api                                            # 读取 API_HOST/API_PORT
uv run celery -A api.celery_app:celery_app worker -Q parse --pool=solo --loglevel=INFO  # parse 队列同时消费 parse 与 bidding.write_bid
TASK_BACKEND=local uv run uvicorn api.main:app --port 8001      # 无 Redis/worker 的本地模式
uv run python -m src.cli write 6055594244948 --profile bidder_profile.example.json --max-sections 2  # 多 Agent 编写投标文件
```

FastAPI 服务监听 `API_PORT=8001`（`/docs` 为 OpenAPI 文档）；默认使用 Celery + Redis，Windows worker 必须 `--pool=solo`。

### 前端（`frontend/`）

```bash
npm install
npm run dev            # Vite 开发服务器 http://localhost:5173
npm run build          # tsc -b && vite build，产物到 frontend/dist/
npm run lint           # oxlint
npm run electron       # 先 build，再用 Electron 打开生产构建
npm run electron:dev   # 并行启动 Vite 与 Electron（--dev 模式）
npm run dist           # electron-builder 打包，输出 frontend/release/
```

前端默认连接 `http://127.0.0.1:8001`；启动前需先运行后端 API（Celery 模式还需 Redis + worker，或使用 `TASK_BACKEND=local`）。

### 本地依赖服务

- **Redis**：本机无原生 `redis-server.exe`，使用 Docker 容器 `much-agents-redis`（映射 6379，`--restart unless-stopped`）：`docker start much-agents-redis`。连接串 `redis://localhost:6379/0`。
- **PostgreSQL**：`postgresql+asyncpg://postgres:123456@localhost:5432/bidding`（公司信息入库为待办）。
- **Chroma**：`uv run chroma run --path ./chroma_db`，向量数据落在 `chroma_db/`（已 gitignore）。
- **MinerU**：云端解析，需要 `MINERU_API_KEY`（有效期 90 天）；单文件页数上限 200，超过需先分片。

## 架构要点

### 前端 / Electron

- Electron 主进程是 CommonJS：`frontend/electron/main.cjs`。带 `--dev` 时加载 `http://localhost:5173`，否则通过 `file://` 加载 `dist/index.html`。
- `vite.config.ts` 设置 `base: './'`，因为 Electron 生产环境用 `file://` 加载，资源路径必须相对——不要改成绝对路径。
- 渲染层：React 19 + TypeScript + TailwindCSS 4（通过 `@tailwindcss/vite` 插件，无 `tailwind.config.*`）；Markdown 预览使用 `react-markdown` + `remark-gfm`，默认不渲染原始 HTML。
- 工作台为左侧导航 + 四个视图：① 上传解析 ② 编写标书 ③ 文档库 ④ 任务历史；包含 job 进度、profile 表单/JSON、结果统计、产物预览与下载。
- 支持深色/浅色主题切换（Header 按钮），偏好保存在 localStorage，首屏由 `index.html` 内联脚本恢复，避免闪烁。
- API base 默认 `http://127.0.0.1:8001`，可通过 `VITE_API_BASE_URL` 或界面设置覆盖；Electron 生产环境从 `file://` 加载，因此必须使用完整 http 地址，不能依赖相对 `/api`。任务历史存 localStorage，文档库来自后端扫描。
- 目前没有 preload/IPC；`contextIsolation: true`、`nodeIntegration: false`。产物通过后端 artifact 接口访问，浏览器与 Electron 均可用；`profile.json` 不在产物 allowlist 中。

### 文档解析管线（已实现，`src/parsing/`）

入口 `uv run python -m src.parsing <pdf>`，`parse_document()` 可被后续 API 复用。阶段顺序：解析 → 抽取 → 分块 → 入库；产物在 `upload/parsed/<doc_id>/`（`full.md`、`content_list.json`、`toc.json`、`meta.json`、`extraction.json`、`chunks.jsonl`、`ingest.json`）。

1. **解析**：`parsers/mineru/` 走 v4 批量 API（签名 URL 上传 → 轮询 → 下载 zip），`parsers/pymupdf_parser.py` 兜底。MinerU 实际限制 200 页/文件（文档写 600，接口会拒绝），超过时用 PyMuPDF 按平衡页数分片、同批上传后按页码偏移合并。
2. **结构对齐**：以 `content_list` 的 `page_idx` + PDF 书签为准；`toc.py` 的 `end_page` 按“下一条同级或更高级目录”计算，避免章级范围被小节截断。页脚哈希水印和页码在 `parsers/footer.py` 清理，不要用 redaction。
3. **抽取**：`extraction/` 按章节裁剪成 ≤24k 字的 payload，逐段调用 DeepSeek（OpenAI 兼容，JSON 模式），Pydantic 校验；失败或缺少 key 时降级到正则抽取，`method` 会记录 `llm` / `llm+regex` / `regex_fallback`。
4. **分块与入库**：`chunking.py` 以 TOC section 为边界，默认 700 字/120 重叠，id 形如 `<doc_id>:<section_id>:<ordinal>`；`ingest.py` 用智谱 `embedding-3` 生成向量，`HttpClient` 写入 Chroma 集合 `bidding_documents`，upsert + 清理旧 id。
5. **缓存**：`meta.json` 记录源文件 sha256；解析/抽取/入库均有缓存，`--force` 强制重跑。退出码：0 成功、2 输入错误、3 解析失败、4 抽取为空、5 入库失败、6 缺少 key 跳过。

### FastAPI 上传接口（已实现，`api/`）

- `POST /api/parse`：multipart `file`，查询参数 `parser=auto|mineru|pymupdf`、`extract=true`、`ingest=true`；**默认执行完整流程**；返回 202 + `job_id`/`poll_url`/`doc_id`。
- `GET /api/jobs/{job_id}`：200 返回状态/进度/result（失败任务也 200）；未知 404。
- `GET /api/health`。
- `GET /api/documents`：扫描 `upload/parsed/*`，返回项目名、页数、chunks、bid_ready 与历史 bid run。
- `GET /api/jobs/{job_id}/artifacts`、`GET /api/documents/{doc_id}/artifacts`、`GET /api/documents/{doc_id}/bids/{run_id}/artifacts`：按 allowlist 列出/下载产物；路径穿越返回 404，**`profile.json` 永不暴露**。
- `GET /api/bids/profile-template`：返回 `bidder_profile.example.json` 的 JSON 模板。
- 上传落盘 `upload/inbox/<job_id>/source.pdf`；`doc_id = <文件名>-<内容 sha256 前 12 位>`，相同内容重复上传可命中解析缓存。
- 默认 `TASK_BACKEND=celery`：任务状态保存在 Redis `much-agents:job:<job_id>` hash，Celery worker 消费 `parse` 队列（同时承载 parse 与 bid 任务，按 `Job.kind` 区分）；API 重启后仍可轮询，任务记录 24h TTL。
- Worker 与 API 必须同一 checkout、共享 `upload/` 与 `.env`；Windows 用 `--pool=solo`（单任务串行，保护 MinerU 配额），不自动重试（`acks_late=False`），`mark_running` 原子 claim 防重复解析。
- Redis 不可用时 `POST /api/parse` 与 `GET /api/jobs/{id}` 返回 503，health 为 `status=degraded, redis_ok=false`。
- `TASK_BACKEND=local` 使用进程内 `JobStore` + `ThreadJobRunner`，无需 Redis/worker，但 API 重启即丢任务；测试与本地离线开发用它。
- CORS 默认 `*`、无 credentials，兼容 Vite dev 与 Electron `file://`；对外暴露前需改成显式来源并加鉴权。`result.output_dir` 是绝对路径，仅适合本地桌面端。

### 多 Agent 投标文件编写（已实现，`src/bidding/`）

- 入口：CLI `uv run python -m src.cli write <doc_id>`，以及 `POST /api/bids`（JSON `{doc_id, profile, max_sections}`），复用 `GET /api/jobs/{id}` 轮询、Redis JobStore 与 Celery `parse` 队列（任务名 `bidding.write_bid`）。

  ```bash
  curl -X POST http://127.0.0.1:8001/api/bids \
    -H "Content-Type: application/json" \
    -d '{"doc_id":"6055594244948","profile":{},"max_sections":1}'
  ```
- 产物：`upload/bids/<doc_id>/<run_id>/` 下的 `profile.json`、`outline.json`、`bid.md`、`bid.docx`、`report.json`、`sections/*.md`。
- LangGraph 流程：确定性 plan → 逐节检索（Chroma/关键词兜底）→ 并行 Writer → 并行 Reviewer → 条件 Reviser → 确定性 assemble；只有施工组织设计的 11 个 narrative 子章节经过 LLM，其余表单/附件为确定性模板。
- 投标人资料：`bidder_profile.example.json` 模板；缺失字段一律 `【待填写：...】`，严禁编造公司、人员、证书、业绩、报价、日期。Writer/Reviewer 不接收完整 profile；profile 快照只落本地 run 目录，不进入 Redis/Celery 消息。
- 评标覆盖：`report.json` 记录 55 条评标因素的 covered/partial/template/missing、占位符、缺失资料、审查问题与 token/耗时统计。
- `--max-sections N` 控制生成的 narrative 数量（0=全部 11 个）；表单骨架始终完整。Word 转换支持标题/段落/列表/表格/基本内联样式，复杂排版需人工调整。

### 尚未实现（后续阶段）

- **待办**：PostgreSQL 公司信息、前端 IPC（打开本地目录）、对外检索 API、MinIO、Nginx。

### 配置与数据

- `.env`（已 gitignore）由 `.env.example` 复制；关键变量：`DEEPSEEK_API_KEY`、`ZHIPU_API_KEY`、`MINERU_API_KEY`、`DATABASE_URL`、`REDIS_URL`，以及 API/Chroma/MinerU/投标参数（`API_HOST/API_PORT`、`UPLOAD_DIR`、`MAX_UPLOAD_MB`、`API_JOB_WORKERS`、`CORS_ORIGINS`、`TASK_BACKEND`、`CELERY_BROKER_URL`、`CELERY_WORKER_POOL`、`JOB_TTL_SECONDS`、`BIDS_DIR`、`BID_MAX_SECTIONS`、`BID_RETRIEVAL_TOP_K`、`BID_MAX_TOKENS`、`BID_MAX_REVIEW_REVISIONS`、`BID_LLM_CONCURRENCY`、`BID_USE_CHROMA`）。
- 前端默认 API base 为 `http://127.0.0.1:8001`；可在 `frontend/.env` 设置 `VITE_API_BASE_URL`，或在界面「API 连接设置」中覆盖（保存到浏览器 localStorage）。
- LLM 默认 `LLM_PROVIDER=deepseek`、`DEEPSEEK_MODEL=deepseek-flash`；嵌入模型 `EMBEDDING_MODEL=embedding-3`（智谱）。
- `pyproject.toml` 将阿里云 PyPI 镜像配置为默认 index；Python 固定 `>=3.12,<3.13`，由 `.python-version` 约束。
- 数据目录（均已 gitignore）：`chroma_db/` 向量库；`upload/` 是输入 PDF 与产物的根目录（`upload/inbox/<job_id>/source.pdf` 为 API 上传，`upload/parsed/<doc_id>/` 为解析输出，`upload/bids/<doc_id>/<run_id>/` 为投标文件产物）。

## 注意

- Windows 环境开发；长任务（解析/抽取/编写）不要阻塞 HTTP 请求，走 Celery 或轮询任务模型。
- 不要提交 `.env`、`chroma_db/`、`upload/`、`node_modules/`、`dist/`、`release/`。
