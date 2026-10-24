# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 固定任务

- 每次修改完成后，都应创建一个对应的 Git commit，便于后续追踪和回滚。

## 项目概述

多 Agent 标书编写系统（Much Agents）：解析招标文件 PDF → 结构化抽取 → 入 Chroma 向量库 → 由 LangGraph 多 Agent 生成投标文件（Markdown + Word），前端为 React + Electron 桌面端。技术栈参考 [ZhiBiao](https://github.com/Foulereee/ZhiBiao)。

**当前状态**：已实现文档解析管线 `src/parsing/`、FastAPI 上传/任务接口 `api/`、Celery/Redis 异步任务后端（含 local 内存兜底）、多 Agent 投标文件编写 `src/bidding/`、招标文件片段检索、PostgreSQL 公司库与持久化投标 run/大纲/章节状态、可编辑大纲、章节级续跑与单节编辑/重写、服务端投标历史、独立 21 项合规检查、跨章节一致性台账与安全修复方案、导出前门禁、React/Electron 前端工作台、Nginx 前端托管/API 反向代理、Windows 一键启动/停止脚本与测试 `tests/`。用户/RBAC、项目实体、MinIO、PDF/DOC 导出与前端 IPC 仍未实现，后续阶段以本文件与代码为准。

## 常用命令

### 后端（项目根目录，uv 管理，Python 3.12）

```bash
uv sync                                      # 安装/同步依赖
uv run alembic upgrade head                  # 应用 PostgreSQL 迁移（API 默认启动时也会自动执行）
uv run alembic current                       # 查看当前数据库版本
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

### 一键启动（Windows）

项目根目录的 `start.bat`（双击或命令行）会拉起整套本地环境：

```bash
start.bat               # Docker 依赖 + 迁移 + Chroma + API + Celery worker + Vite(5173)，自动开浏览器
start.bat prod          # 构建前端并用 Nginx 托管到 8080（Nginx 固定反代 8001）
start.bat local         # TASK_BACKEND=local，跳过 Redis/worker
start.bat --no-chroma   # 不启动 Chroma（检索自动回退关键词）
start.bat --dry-run     # 只打印启动计划，不启动任何进程
stop.bat                # 停止由 start.bat 拉起的 Python/Node 进程
```

- 实现在 `scripts/start_all.py`（仅标准库）；Docker Desktop 未运行时会尝试启动并等待，失败则报错。
- 首次运行会从 `.env.example` 复制 `.env`；缺少 `DEEPSEEK_API_KEY`/`ZHIPU_API_KEY`/`MINERU_API_KEY` 时只警告，不阻塞启动。
- 启动/复用 `much-agents-postgres`、`much-agents-redis`（prod 还会启动 `much-agents-nginx`）；容器带 `--restart unless-stopped`，`stop.bat` 不会关闭容器。
- PID 记录在 `.run/start.json`（已 gitignore）；再次运行会先清理上次拉起的进程，`stop.bat` 在 Windows 用 `taskkill /T /F` 结束进程树。
- 端口已有 Much Agents API / Vite 时直接复用；被其他程序占用则报错。`--skip-docker` 可连接已有 PostgreSQL/Redis；本地 Redis 不可用时自动降级为 `TASK_BACKEND=local`。
- 每个服务使用独立控制台窗口；强制关闭 supervisor 窗口不会触发自动停止，改用 `stop.bat`。`--dry-run` 不写 `.env`、不启动进程。

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

浏览器访问 `http://127.0.0.1:8080`（Nginx）或 `http://127.0.0.1:5173`（Vite）时默认使用同源 `/api`；Electron `file://` 自动使用 `http://127.0.0.1:8001`。启动前需先运行后端 API（Celery 模式还需 Redis + worker，或使用 `TASK_BACKEND=local`）。

### 本地依赖服务

优先使用根目录 `start.bat` 统一启动/复用以下容器；以下命令用于手动排查。

- **Redis**：本机无原生 `redis-server.exe`，使用 Docker 容器 `much-agents-redis`（映射 6379，`--restart unless-stopped`）：`docker start much-agents-redis`。连接串 `redis://localhost:6379/0`。
- **PostgreSQL**：Docker 容器 `much-agents-postgres`（`postgres:16-alpine`，映射 5433，`--restart unless-stopped`，数据卷 `much-agents-postgres-data`）：`docker start much-agents-postgres`。连接串 `postgresql+asyncpg://much_agents:much_agents_dev@localhost:5433/much_agents`；表结构由 Alembic 管理，API 启动默认执行 `alembic upgrade head`（`DB_AUTO_MIGRATE=false` 可关闭）。
- **Nginx**：Docker 容器 `much-agents-nginx`（`nginx:alpine`，`127.0.0.1:8080->80`，`--restart unless-stopped`），配置 `deploy/nginx.conf`，静态目录挂载 `frontend/dist`；`http://127.0.0.1:8080` 提供前端页面，`/api/*`、`/docs` 反向代理到 `host.docker.internal:8001`。重建前端后无需重启容器。
- **Chroma**：`uv run chroma run --path ./chroma_db`，向量数据落在 `chroma_db/`（已 gitignore）。
- **MinerU**：云端解析，需要 `MINERU_API_KEY`（有效期 90 天）；单文件页数上限 200，超过需先分片。

## 架构要点

### 前端 / Electron

- Electron 主进程是 CommonJS：`frontend/electron/main.cjs`。带 `--dev` 时加载 `http://localhost:5173`，否则通过 `file://` 加载 `dist/index.html`。
- `vite.config.ts` 设置 `base: './'`，因为 Electron 生产环境用 `file://` 加载，资源路径必须相对——不要改成绝对路径。
- 渲染层：React 19 + TypeScript + TailwindCSS 4（通过 `@tailwindcss/vite` 插件，无 `tailwind.config.*`）；Markdown 预览使用 `react-markdown` + `remark-gfm`，默认不渲染原始 HTML。
- 工作台为左侧导航 + 六个视图：① 上传解析 ② 编写标书 ③ 公司库 ④ 文档检索 ⑤ 文档库 ⑥ 任务历史；包含 job 进度、profile 表单/JSON、公司选择、结果统计、产物预览与下载。
- 「编写标书」为分阶段流程：选择文档/公司/资料 → 创建 run 并编辑大纲（标题、目标字数、编写要点、写作重点；表单/附件锁定）→ 生成/续跑/取消 → 自动组装与门禁检查 → 严格导出 DOCX。已完成章节可编辑正文或重新生成本节；旧稿在新稿完成前保留。门禁下方显示「合规检查与一致性台账」：21 项检查、跨章节事实核对、确定性/AI 修复方案与一键应用（应用等同人工编辑，需重新组装）。
- 「任务历史」顶部显示服务端 `bid_runs`（可打开继续编辑/重写/组装），下方保留 localStorage 的解析/旧版任务记录；`LibraryView` 仍以磁盘产物为主。
- 公司库：完整 `BidderProfile` 以 PostgreSQL JSONB 保存；前端支持新建/编辑/删除/搜索公司，编写标书时可选择公司并自动载入资料，提交时附带 `company_id`。
- 文档检索：查询已解析招标文件 chunks，展示章节/页码/片段/相关度；语义优先，Chroma/智谱不可用或空结果时自动回退关键词，并提示回退原因。
- 支持深色/浅色主题切换（Header 按钮），偏好保存在 localStorage，首屏由 `index.html` 内联脚本恢复，避免闪烁。
- API base：http(s) 页面默认同源 `/api`（Vite 通过 `server.proxy`、Nginx 通过 `/api/` 反代）；Electron `file://` 默认 `http://127.0.0.1:8001`。可用 `VITE_API_BASE_URL` 或界面设置覆盖；留空恢复同源。解析/旧版任务历史存 localStorage，服务端投标 run 历史来自 `/api/bid-runs`，文档库来自后端扫描。
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
- `POST /api/search`：JSON `{query, doc_id?, top_k, mode: auto|semantic|keyword}`；语义优先，auto 在 Chroma/智谱不可用或空结果时回退关键词；返回片段、章节、页码、归一化相关度、warnings、elapsed_ms。semantic 不可用返回 503，未知 doc 404。
- `GET /api/jobs/{job_id}/artifacts`、`GET /api/documents/{doc_id}/artifacts`、`GET /api/documents/{doc_id}/bids/{run_id}/artifacts`：按 allowlist 列出/下载产物（投标产物含 `compliance.json`、`consistency.json`）；路径穿越返回 404，**`profile.json` 永不暴露**。
- `GET /api/bids/profile-template`：返回 `bidder_profile.example.json` 的 JSON 模板。
- `GET/POST /api/companies`、`GET/PUT/DELETE /api/companies/{company_id}`：PostgreSQL 公司库 CRUD；`profile` 为完整 BidderProfile；公司名称必填。
- `POST /api/bids` 支持可选 `company_id`：未提供 profile 时从公司库载入；提供 profile 时使用当前编辑内容，company_id 写入 job payload 便于追踪。

  ```bash
  curl -X POST http://127.0.0.1:8001/api/search \
    -H "Content-Type: application/json" \
    -d '{"query":"施工组织设计","doc_id":"6055594244948","top_k":5,"mode":"auto"}'
  ```
- 上传落盘 `upload/inbox/<job_id>/source.pdf`；`doc_id = <文件名>-<内容 sha256 前 12 位>`，相同内容重复上传可命中解析缓存。
- 默认 `TASK_BACKEND=celery`：任务状态保存在 Redis `much-agents:job:<job_id>` hash，Celery worker 消费 `parse` 队列（同时承载 parse 与 bid 任务，按 `Job.kind` 区分）；API 重启后仍可轮询，任务记录 24h TTL。
- Worker 与 API 必须同一 checkout、共享 `upload/` 与 `.env`；Windows 用 `--pool=solo`（单任务串行，保护 MinerU 配额），不自动重试（`acks_late=False`），`mark_running` 原子 claim 防重复解析。
- Redis 不可用时 `POST /api/parse` 与 `GET /api/jobs/{id}` 返回 503，health 为 `status=degraded, redis_ok=false`。
- `TASK_BACKEND=local` 使用进程内 `JobStore` + `ThreadJobRunner`，无需 Redis/worker，但 API 重启即丢任务；测试与本地离线开发用它。
- CORS 默认 `*`、无 credentials，兼容 Vite dev 与 Electron `file://`；对外暴露前需改成显式来源并加鉴权。`result.output_dir` 是绝对路径，仅适合本地桌面端。

### 多 Agent 投标文件编写（已实现，`src/bidding/`）

- 入口：CLI `uv run python -m src.cli write <doc_id>` 与 legacy `POST /api/bids`（一次性创建并立即生成），以及新的持久化流程 `/api/bid-runs`（见下）。两者复用 `GET /api/jobs/{id}` 轮询、Redis JobStore 与 Celery `parse` 队列（任务名 `bidding.write_bid`）。
- 产物：`upload/bids/<doc_id>/<run_id>/` 下的 `profile.json`、`outline.json`、`bid.md`、`bid.docx`、`report.json`、`gate.json`、`compliance.json`、`consistency.json`、`sections/*.md`。
- LangGraph 流程（legacy/CLI）：确定性 plan → 逐节检索（Chroma/关键词兜底）→ 并行 Writer → 并行 Reviewer → 条件 Reviser → 确定性 assemble；只有施工组织设计的 11 个 narrative 子章节经过 LLM，其余表单/附件为确定性模板。
- 投标人资料：`bidder_profile.example.json` 模板；缺失字段一律 `【待填写：...】`，严禁编造公司、人员、证书、业绩、报价、日期。Writer/Reviewer 不接收完整 profile；profile 快照只落本地 run 目录，不进入 Redis/Celery 消息。
- 评标覆盖：`report.json` 记录 55 条评标因素的 covered/partial/template/missing、占位符、缺失资料、审查问题与 token/耗时统计。
- `--max-sections N` 控制生成的 narrative 数量（0=全部 11 个）；表单骨架始终完整。Word 转换支持标题/段落/列表/表格/基本内联样式，复杂排版需人工调整。

### 持久化投标 run、可编辑大纲与单节编辑（P0/P1-A，`api/bid_runs.py`、`src/bidding/staged.py`）

- PostgreSQL 新增 3 张表：`bid_runs`、`bid_outline_sections`、`bid_section_states`（Alembic `0002`）；Redis 仍是 Celery broker/实时 job 状态，PostgreSQL 是 run/大纲/章节的事实来源。
- 新流程：`POST /api/bid-runs` 只创建 run/大纲并写 profile 快照，不调用生成 LLM；`PATCH /api/bid-runs/{id}/outline` 用 revision 乐观锁修改 narrative 的标题、目标字数、requirements、content_plan、writing_focus；`POST .../generate|resume|cancel` 入队/续跑/取消；`POST .../assemble` 组装并生成 `gate.json`；`GET .../gate`；`POST .../export?force=false` 严格门禁导出 DOCX。`GET /api/bid-runs?doc_id=&status=`、`GET /api/bid-runs/{id}`、`GET .../outline`、`GET .../sections/{section_id}` 提供查询。
- 单节编辑：`PATCH /api/bid-runs/{id}/sections/{section_id}` 保存人工正文（仅 final narrative），用 `deterministic_review` 刷新 issues，`final_source="manual"`，run 退回 `paused/edited` 并清空 gate；不调用 LLM、不静默 redact；下一次 assemble 会重新计算门禁。
- 单节重写：`POST /api/bid-runs/{id}/sections/{section_id}/regenerate` 将节状态置 `pending` 但保留旧 `final_markdown`，重置 run attempt/gate 后复用 staged 生成；成功覆盖，失败保留旧稿并标记 failed，gate 继续 block。入队同步失败时尽力把节恢复为 final。
- 章节级检查点：每完成 write/review/revise 就写 `bid_section_states`；worker 崩溃后 `resume` 只补 pending/failed/stale 章节，不重复已完成章节。DB lease + heartbeat + `bid_runs(doc_id)` 活跃唯一索引防重复生成；取消在章节边界生效。
- 内容计划在 P0 是确定性的（requirements + 评分因素 + 目标字数 + 来源页），不额外调用 LLM；Writer 收到 `【本节编写计划】` 与 `【编写重点】`。
- 大纲编辑只允许 narrative 的白名单字段；不允许增删/重排，`kind/template_key` 等不可篡改；表单/附件保持确定性模板。修改已生成章节的内容字段会把该节重置为 `pending`。
- 最小门禁：缺 selected 内容、失败章节、高危/redaction、估算页数 >150 为 block；占位符、缺资料、评分因素未覆盖为 warn。直接下载产物保持宽松，只有 `POST /export` 严格拦截；`force=true` 会记录 `forced=true`。
- profile 隐私不变：DB 只存 `profile_source/profile_sha256/profile_relpath` 与缺失字段标签；Redis/Celery payload 只有 `run_id/generation_token/section_ids/assemble/mode`；`profile.json` 继续不在 artifact allowlist。
- `POST /api/bids`、CLI、旧 artifact 接口保持兼容；staged job 的 artifact 目录通过 job payload 的 `run_id` 解析（`job_id != run_id`）。
- 仍未实现：PDF/DOC 导出、RBAC、MinIO、Celery beat、Docker Compose；失败章节用现有「重试失败章节」，不做任意章节增删。

### 合规检查、一致性台账与修复方案（P1-B，`src/bidding/compliance.py`、`consistency.py`、`fix_plan.py`）

- `assemble` 在组装后额外生成 `compliance.json`（21 项独立检查）与 `consistency.json`（跨章节事实台账），并把状态/summary 写入 `gate.json` 与 `report.json`；两文件也是 run 目录产物，可直接在 artifact 面板预览/下载。
- 21 项检查固定顺序：公司/项目/招标人/标段/工期/质量/有效期/截止时间/开竣工日期/联系方式/信用代码/法人/授权代理人/项目经理/证书/人员/业绩/财务/保证金/资格/章节完整性/评分覆盖。缺失资料和占位符是 warn，真实矛盾（如公司名、工期不一致）与失败章节是 block。
- 一致性台账覆盖 21 类事实，逐项记录 canonical 值、每处 occurrence（章节、引用、exact/conflict）与 `consistent|conflict|missing|unverified|not_found`；关键事实冲突使台账为 `conflict`，缺失/未核实为 `warning`。
- 门禁消费合规结果：任一 block 级检查失败 → gate 新增 `compliance_block` block；warn 级失败 → `compliance_warnings` warn。直接下载产物仍宽松，只有 `POST /export` 严格拦截。
- 新增接口：`GET /api/bid-runs/{id}/compliance`、`POST .../compliance/check`（重新计算并落盘，不改 run 状态）、`GET .../consistency`、`POST .../compliance/fix-plan`（`check_ids` + `max_items`）、`POST .../compliance/fixes/apply`。
- 修复方案先由一致性冲突生成确定性 replace 项（找不到唯一原文则不生成）；可选 LLM 只提供候选，所有候选都会重新校验 check id、章节可编辑性、`find` 唯一、`replace` 不含未授权金额/日期/证件与占位符。LLM 失败自动回退确定性方案。
- 应用修复要求 final 可编辑 narrative、`find` 恰好出现一次；替换后走 `edit_section` 语义（`final_source="manual"`、run `paused/edited`、清空 gate 与两份报告），下一次 assemble 重新计算。大纲失效、人工编辑、单节重写都会删除过期报告。

### 尚未实现（后续阶段）

- **待办**：前端 IPC（打开本地目录）、MinIO、PDF/DOC 导出、RBAC/项目实体、Docker Compose。

### 配置与数据

- `.env`（已 gitignore）由 `.env.example` 复制；关键变量：`DEEPSEEK_API_KEY`、`ZHIPU_API_KEY`、`MINERU_API_KEY`、`DATABASE_URL`、`REDIS_URL`，以及 API/Chroma/MinerU/投标参数（`API_HOST/API_PORT`、`UPLOAD_DIR`、`MAX_UPLOAD_MB`、`API_JOB_WORKERS`、`CORS_ORIGINS`、`TASK_BACKEND`、`CELERY_BROKER_URL`、`CELERY_WORKER_POOL`、`JOB_TTL_SECONDS`、`DB_AUTO_MIGRATE`、`BID_RUN_MAX_ATTEMPTS`、`BID_RUN_STALE_SECONDS`、`BIDS_DIR`、`BID_MAX_SECTIONS`、`BID_RETRIEVAL_TOP_K`、`BID_MAX_TOKENS`、`BID_MAX_REVIEW_REVISIONS`、`BID_LLM_CONCURRENCY`、`BID_USE_CHROMA`）。
- 前端 http(s) 页面默认同源 `/api`，Electron `file://` 默认 `http://127.0.0.1:8001`；可在 `frontend/.env` 设置 `VITE_API_BASE_URL`，或在界面「API 连接设置」中覆盖（保存到 localStorage；留空恢复同源）。
- PostgreSQL：`DATABASE_URL` 指向 `much-agents-postgres`；`companies` 表由 Alembic `0001` 基线创建，`bid_runs`/`bid_outline_sections`/`bid_section_states` 由 `0002` 创建。API 启动默认执行 `alembic upgrade head`（advisory lock 防并发迁移），worker 不迁移。数据库不可用时公司/run 接口返回 503，不影响解析与 legacy 标书任务；pytest 使用内存 fake，不连接真实数据库。已有只含 `companies` 的开发库先 `alembic stamp 0001` 再 `upgrade head`。
- LLM 默认 `LLM_PROVIDER=deepseek`、`DEEPSEEK_MODEL=deepseek-flash`；嵌入模型 `EMBEDDING_MODEL=embedding-3`（智谱）。
- 检索：`BID_USE_CHROMA=true` 且 `ZHIPU_API_KEY` 可用时启用语义；`auto` 不可用或空结果时回退关键词。`BID_RETRIEVAL_TOP_K` 只控制标书 Writer，搜索 top_k 由请求参数控制（1–30）。
- `pyproject.toml` 将阿里云 PyPI 镜像配置为默认 index；Python 固定 `>=3.12,<3.13`，由 `.python-version` 约束。
- 数据目录（均已 gitignore）：`chroma_db/` 向量库；`upload/` 是输入 PDF 与产物的根目录（`upload/inbox/<job_id>/source.pdf` 为 API 上传，`upload/parsed/<doc_id>/` 为解析输出，`upload/bids/<doc_id>/<run_id>/` 为投标文件产物）；`.run/` 保存一键启动的 PID 状态（POSIX 上还有各服务日志）。

## 注意

- Windows 环境开发；长任务（解析/抽取/编写）不要阻塞 HTTP 请求，走 Celery 或轮询任务模型。
- 不要提交 `.env`、`chroma_db/`、`upload/`、`node_modules/`、`dist/`、`release/`。
