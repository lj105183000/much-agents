# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 固定任务

- 每次修改完成后，都应创建一个对应的 Git commit，便于后续追踪和回滚。

## 项目概述

多 Agent 标书编写系统（Much Agents）：解析招标文件 PDF → 结构化抽取 → 入 Chroma 向量库 → 由 LangGraph 多 Agent 生成投标文件（Markdown + Word），前端为 React + Electron 桌面端。技术栈参考 [ZhiBiao](https://github.com/Foulereee/ZhiBiao)。

**当前状态**：已实现文档解析管线 `src/parsing/`、FastAPI 上传/任务接口 `api/`、Celery/Redis 异步任务后端（含 local 内存兜底）、多 Agent 投标文件编写 `src/bidding/`、招标文件片段检索、PostgreSQL 公司库与持久化投标 run/大纲/章节状态、可编辑大纲、章节级续跑与单节编辑/重写、服务端投标历史、独立 21 项合规检查、跨章节一致性台账与安全修复方案、导出前门禁、确定性 AI 配图（组织架构/工艺流程/进度横道/施工总平面并真实嵌入 Markdown/DOCX）、MCP 服务端（stdio + 可选 HTTP）与白名单 MCP 客户端（外部证据/企业资料库/外部绘图）、可选 MinIO 对象存储（`STORAGE_BACKEND=minio|local`，默认 minio；artifact 流式下载与私有 profile）、本地账号/可撤销会话/5 角色 RBAC/管理员用户管理/MCP 服务账号（P0-A）、React/Electron 前端登录与角色感知工作台、Nginx 前端托管/API 反向代理、Windows 一键启动/停止脚本与测试 `tests/`。项目实体与项目级数据隔离、审计日志、生产部署/备份、PDF/DOC 导出、文生图与前端 IPC 仍未实现；P0-A 不是内网多人上线版本（登录用户仍可见全部数据），后续阶段以本文件与代码为准。

## 常用命令

### 后端（项目根目录，uv 管理，Python 3.12）

```bash
uv sync                                      # 安装/同步依赖
uv run alembic upgrade head                  # 应用 PostgreSQL 迁移（API 默认启动时也会自动执行）
uv run alembic current                       # 查看当前数据库版本
uv run python -m api.users create-admin --username admin --display-name 管理员  # 交互式创建首个管理员
uv run python -m api.users create-user --username zhangsan --role writer      # 创建普通用户
uv run python -m api.users reset-password --username admin                    # 重置密码并撤销会话
uv run python -m api.users list                                              # 列出用户
uv run chroma run --path ./chroma_db         # 启动 Chroma，监听 8000
uv run python -m src.storage check           # 检查 MinIO 连通性与各前缀对象数
uv run python -m src.storage sync            # 把本地存量 upload/ 增量同步到 MinIO
uv run pytest                                # 运行全部测试（全部 mock 网络）
uv run pytest tests/test_toc.py::test_hierarchy_and_end_pages   # 单个测试
uv run python -m src.parsing upload/6055594244948.pdf           # 解析 + 抽取 + 入库
uv run python -m src.parsing <pdf> --parser pymupdf --no-extract --no-ingest  # 离线仅解析
uv run uvicorn api.main:app --host 0.0.0.0 --port 8001          # 启动 API（单进程）
uv run python -m api                                            # 读取 API_HOST/API_PORT
uv run celery -A api.celery_app:celery_app worker -Q parse --pool=solo --loglevel=INFO  # parse 队列同时消费 parse 与 bidding.write_bid
TASK_BACKEND=local uv run uvicorn api.main:app --port 8001      # 无 Redis/worker 的本地模式
uv run python -m src.cli write 6055594244948 --profile bidder_profile.example.json --max-sections 2  # 多 Agent 编写投标文件
uv run python -m api.mcp_server --check      # 列出 MCP 服务端工具后退出
uv run python -m api.mcp_server              # stdio MCP 服务端（需先启动 API）
uv run python -m api.mcp_server --transport http --host 127.0.0.1 --port 8765  # 可选 HTTP
uv run python -m src.mcp_client list        # 列出白名单 MCP 客户端配置
uv run python -m src.mcp_client check       # 连接并核对白名单工具（只读）
uv run python -m src.mcp_client call company_kb search '{"query":"类似业绩"}'  # 排障调用
```

FastAPI 服务监听 `API_PORT=8001`（`/docs` 为 OpenAPI 文档）；默认使用 Celery + Redis，Windows worker 必须 `--pool=solo`。API 默认开启认证（`AUTH_ENABLED=true`），首次使用先创建管理员，否则只有 `/api/health` 与登录接口可访问。

### 一键启动（Windows）

项目根目录的 `start.bat`（双击或命令行）会拉起整套本地环境：

```bash
start.bat               # Docker 依赖（含 MinIO）+ 迁移 + Chroma + API + Celery worker + Vite(5173)，自动开浏览器
start.bat prod          # 构建前端并用 Nginx 托管到 8080（Nginx 固定反代 8001）
start.bat local         # TASK_BACKEND=local + STORAGE_BACKEND=local，跳过 Redis/worker/MinIO
start.bat --no-minio    # 不启动 MinIO，强制 STORAGE_BACKEND=local
start.bat --no-chroma   # 不启动 Chroma（检索自动回退关键词）
start.bat --dry-run     # 只打印启动计划，不启动任何进程
stop.bat                # 停止由 start.bat 拉起的 Python/Node 进程
```

- 实现在 `scripts/start_all.py`（仅标准库）；Docker Desktop 未运行时会尝试启动并等待，失败则报错。
- 首次运行会从 `.env.example` 复制 `.env`；缺少 `DEEPSEEK_API_KEY`/`ZHIPU_API_KEY`/`MINERU_API_KEY` 时只警告，不阻塞启动。
- 启动/复用 `much-agents-postgres`、`much-agents-redis`、`much-agents-minio`（prod 还会启动 `much-agents-nginx`）；容器带 `--restart unless-stopped`，`stop.bat` 不会关闭容器。
- MinIO 容器使用 `minio/minio:latest`、数据卷 `much-agents-minio-data`，映射 `MINIO_ENDPOINT` 端口到 9000、`MINIO_CONSOLE_PORT` 到同名控制台端口；启动脚本等待 `/minio/health/live`，并把 `STORAGE_BACKEND` 传给 API/worker。端口被占用时会提示修改 `MINIO_ENDPOINT`/`MINIO_CONSOLE_PORT`。
- PID 记录在 `.run/start.json`（已 gitignore）；再次运行会先清理上次拉起的进程，`stop.bat` 在 Windows 用 `taskkill /T /F` 结束进程树。
- 端口已有 Much Agents API / Vite 时直接复用；被其他程序占用则报错。`--skip-docker` 可连接已有 PostgreSQL/Redis/MinIO；本地 Redis 不可用时自动降级为 `TASK_BACKEND=local`，MinIO 不可用时降级为 `STORAGE_BACKEND=local`。
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
- **MinIO**：Docker 容器 `much-agents-minio`（`minio/minio:latest`，映射 9000/9001，数据卷 `much-agents-minio-data`，`--restart unless-stopped`）；`start.bat` 自动启动/复用，`stop.bat` 不关闭。控制台 `http://127.0.0.1:9001`，凭据来自 `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`（缺失时回退 `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`）。`STORAGE_BACKEND=local` 时不需要。详细说明见 `docs/minio-storage.md`。
- **Chroma**：`uv run chroma run --path ./chroma_db`，向量数据落在 `chroma_db/`（已 gitignore）。
- **MinerU**：云端解析，需要 `MINERU_API_KEY`（有效期 90 天）；单文件页数上限 200，超过需先分片。

## 架构要点

### 前端 / Electron

- Electron 主进程是 CommonJS：`frontend/electron/main.cjs`。带 `--dev` 时加载 `http://localhost:5173`，否则通过 `file://` 加载 `dist/index.html`。
- `vite.config.ts` 设置 `base: './'`，因为 Electron 生产环境用 `file://` 加载，资源路径必须相对——不要改成绝对路径。
- 渲染层：React 19 + TypeScript + TailwindCSS 4（通过 `@tailwindcss/vite` 插件，无 `tailwind.config.*`）；Markdown 预览使用 `react-markdown` + `remark-gfm`，默认不渲染原始 HTML。
- 工作台为左侧导航 + 六个业务视图：① 上传解析 ② 编写标书 ③ 公司库 ④ 文档检索 ⑤ 文档库 ⑥ 任务历史；管理员额外看到 ⑦ 用户管理。包含 job 进度、profile 表单/JSON、公司选择、结果统计、产物预览与下载。
- 「编写标书」为分阶段流程：选择文档/公司/资料 → 创建 run 并编辑大纲（标题、目标字数、编写要点、写作重点；表单/附件锁定）→ 生成/续跑/取消 → 自动组装与门禁检查 → 严格导出 DOCX。已完成章节可编辑正文或重新生成本节；旧稿在新稿完成前保留。门禁下方显示「合规检查与一致性台账」：21 项检查、跨章节事实核对、确定性/AI 修复方案与一键应用（应用等同人工编辑，需重新组装）。
- 「AI 配图」面板位于合规台账与产物面板之间：缩略图/放大预览、图号/图题/章节/来源/免责声明、跳过与警告，以及「重新生成配图」；重新生成会清空 gate 并要求重新组装。产物图片与 Markdown 内图片通过 `AuthenticatedImage` 带 `Authorization` 取 Blob 后渲染（不能直接用 `<img src>` 指向 artifact URL）；`MarkdownPreview` 把 `bid.md`/`sections/*.md` 中的相对图片路径重写到 artifact URL 后再交给该组件（Electron `file://` 走 `apiUrl()`）。
- 「任务历史」顶部显示服务端 `bid_runs`（可打开继续编辑/重写/组装），下方保留 localStorage 的解析/旧版任务记录；`LibraryView` 仍以磁盘产物为主。
- 公司库：完整 `BidderProfile` 以 PostgreSQL JSONB 保存；前端支持新建/编辑/删除/搜索公司，编写标书时可选择公司并自动载入资料，提交时附带 `company_id`。
- 文档检索：查询已解析招标文件 chunks，展示章节/页码/片段/相关度；语义优先，Chroma/智谱不可用或空结果时自动回退关键词，并提示回退原因。
- 支持深色/浅色主题切换（Header 按钮），偏好保存在 localStorage，首屏由 `index.html` 内联脚本恢复，避免闪烁。
- API base：http(s) 页面默认同源 `/api`（Vite 通过 `server.proxy`、Nginx 通过 `/api/` 反代）；Electron `file://` 默认 `http://127.0.0.1:8001`。可用 `VITE_API_BASE_URL` 或界面设置覆盖；留空恢复同源。切换 API base 会清除已保存会话并重新加载，避免把 A 后端的 token 发给 B 后端。解析/旧版任务历史按用户 id 存 localStorage，服务端投标 run 历史来自 `/api/bid-runs`，文档库来自后端扫描。
- 认证：`AuthProvider` 挂载时总是探测 `GET /api/auth/me`（支持 `AUTH_ENABLED=false` 本地模式）；token 存在 `localStorage` 键 `much-agents:auth:v1`，与 API base、过期时间绑定；任意 401 清除会话回登录页。登录页、管理员「用户管理」、Header/Sidebar 用户卡片与登出均已实现；角色感知用 `usePermissions` 隐藏/禁用写操作，后端仍是权威。
- 目前没有 preload/IPC；`contextIsolation: true`、`nodeIntegration: false`。产物通过带认证头的 Blob 请求访问（下载转 object URL，预览用 `AuthenticatedImage`），浏览器与 Electron 均可用；`profile.json` 不在产物 allowlist 中。

### 文档解析管线（已实现，`src/parsing/`）

入口 `uv run python -m src.parsing <pdf>`，`parse_document()` 可被后续 API 复用。阶段顺序：解析 → 抽取 → 分块 → 入库；产物在 `upload/parsed/<doc_id>/`（`full.md`、`content_list.json`、`toc.json`、`meta.json`、`extraction.json`、`chunks.jsonl`、`ingest.json`）。

1. **解析**：`parsers/mineru/` 走 v4 批量 API（签名 URL 上传 → 轮询 → 下载 zip），`parsers/pymupdf_parser.py` 兜底。MinerU 实际限制 200 页/文件（文档写 600，接口会拒绝），超过时用 PyMuPDF 按平衡页数分片、同批上传后按页码偏移合并。
2. **结构对齐**：以 `content_list` 的 `page_idx` + PDF 书签为准；`toc.py` 的 `end_page` 按“下一条同级或更高级目录”计算，避免章级范围被小节截断。页脚哈希水印和页码在 `parsers/footer.py` 清理，不要用 redaction。
3. **抽取**：`extraction/` 按章节裁剪成 ≤24k 字的 payload，逐段调用 DeepSeek（OpenAI 兼容，JSON 模式），Pydantic 校验；失败或缺少 key 时降级到正则抽取，`method` 会记录 `llm` / `llm+regex` / `regex_fallback`。
4. **分块与入库**：`chunking.py` 以 TOC section 为边界，默认 700 字/120 重叠，id 形如 `<doc_id>:<section_id>:<ordinal>`；`ingest.py` 用智谱 `embedding-3` 生成向量，`HttpClient` 写入 Chroma 集合 `bidding_documents`，upsert + 清理旧 id。
5. **缓存**：`meta.json` 记录源文件 sha256；解析/抽取/入库均有缓存，`--force` 强制重跑。退出码：0 成功、2 输入错误、3 解析失败、4 抽取为空、5 入库失败、6 缺少 key 跳过。

### 对象存储（`src/storage/`，默认 MinIO）

- `STORAGE_BACKEND=local|minio`（默认 minio）。`local` 与旧版本完全一致；`minio` 模式下 `inbox/`、`parsed/`、`bids/` 对象是权威来源，本地 `upload/` 只是任务工作缓存。`profile.json` 只存私有 key `private/profiles/<doc_id>/<run_id>.json`。
- `Storage` 协议 + `LocalStorage`/`MinioStorage`（官方 `minio` SDK）；`StorageLayout` 把 key 映射到 `UPLOAD_DIR`/`PARSED_DIR`/`BIDS_DIR`，**不假设 parsed/bids 位于 upload 下**。`build_storage()` 在 local 模式不 import `minio`。
- 读取兜底：远程缺对象时回退本地缓存，`list()` 合并远程与本地 key（远程优先）；`sync_up` 按 `(size, mtime_ns)` 增量上传，`delete_missing` 只用于 `figures/`、`sections/` 等窄子树。
- 流程边界：`hydration.py` 提供 `ensure_document_cached`、`ensure_run_cached`、`ensure_private_profile`、`sync_document`、`sync_run`、`sync_run_derived`；异步服务统一用 `asyncio.to_thread` 调用同步存储 API。
- artifact 下载由 `api/streaming.py` 流式返回，支持 `Range`(206/416)、`HEAD`、`ETag` 与 RFC 5987 中文文件名，**不使用 presigned 重定向**；`profile.json` 不在 allowlist。MinIO 不可用时 artifact/上传/目录/搜索/run 接口返回 503；health 仍 200 但 `status=degraded`、`storage_ok=false`。
- `uv run python -m src.storage check|sync` 用于连通性排障与存量迁移；启动时不做自动全量迁移。详见 `docs/minio-storage.md`。

### 认证、会话与 RBAC（P0-A，`api/auth.py`、`api/users.py`）

- 默认拒绝：`build_router()` 把业务路由挂在 `Depends(require_user)` 下，公开例外仅 `GET /api/health` 与 `POST /api/auth/login`；`/docs` 与 `/openapi.json` 仍未保护。`POST /api/search` 是需认证的只读 POST，不能按 HTTP 方法一刀切。
- PostgreSQL `users`/`user_sessions`（Alembic `0003`）是账号与会话的事实来源；密码 Argon2，数据库只存会话 token 的 SHA-256。登录、登出、改密、重置密码、停用账号、修改角色均有撤销语义；默认 12h TTL、5 次失败锁 15 分钟。最后一个启用管理员不可停用/降级。
- 角色：`admin`、`bid_manager`、`writer`、`reviewer`、`viewer`，外加不可登录的 `service` 主体。`require_write` = admin/bid_manager/writer；`require_review` 再加 reviewer；`require_manage` = admin/bid_manager；`require_admin` 仅交互式 admin（service 拒绝）。`service` 可通过 write/review/manage，但不能管理用户、删除公司或强制导出。
- 端点：`POST /api/auth/login`、`GET /api/auth/me`、`POST /api/auth/logout`、`POST /api/auth/password`；管理员 `GET/POST /api/users`、`PATCH /api/users/{user_id}`、`POST /api/users/{user_id}/password`。守卫映射：parse/bids/bid-runs 创建与所有写操作 `require_write`；合规 check/fix-plan `require_review`；公司新增/编辑与普通导出 `require_manage`；用户管理、公司删除、`force=true` 导出 `require_admin`。
- 公司数据脱敏：非 manage/admin 的 `GET /api/companies` 去掉电话、开户行、银行账号，`GET /api/companies/{id}` 还返回空 `profile`；完整 `BidderProfile` 仅 admin/bid_manager 可见。
- CLI `uv run python -m api.users create-admin|create-user|reset-password|list`；交互式 getpass 或 `--password-stdin`，不提供 `--password`。密码 12–128 字符、不得等于用户名、拒绝少量常见弱口令。
- `AUTH_ENABLED=false` 是仅限本机的逃生口：所有请求以本地 admin 处理，`/api/auth/me` 返回 `auth_enabled=false`，登录接口 400；禁止在监听 `0.0.0.0`/内网时使用。`AUTH_ENABLED=true` 但数据库不可用时受保护接口 503（fail closed），不会匿名放行。
- `MCP_API_TOKEN` 是 MCP 服务端调用业务 API 的 service token（与入站 `MCP_HTTP_TOKEN` 无关）；认证开启时必须设置，否则 MCP 调用 401。轮换需同时重启 API 与 MCP。
- 当前边界：没有项目隔离（任何登录用户可见所有 run/文档/产物）、没有审计表、没有 IP 级限流、CORS 仍为 `*`、无 TLS、token 存 localStorage、`/docs` 公开。P0-A 不是可上线里程碑；详见 `docs/authentication.md`。

### FastAPI 上传接口（已实现，`api/`）

- `POST /api/parse`：multipart `file`，查询参数 `parser=auto|mineru|pymupdf`、`extract=true`、`ingest=true`；**默认执行完整流程**；返回 202 + `job_id`/`poll_url`/`doc_id`。
- `GET /api/jobs/{job_id}`：200 返回状态/进度/result（失败任务也 200）；未知 404。
- `GET /api/health`：返回 `storage_backend`/`storage_ok`；MinIO 不可达时 `status=degraded` 但仍 200。
- `GET /api/documents`：扫描解析产物（minio 模式读对象列表），返回项目名、页数、chunks、bid_ready 与历史 bid run。
- `POST /api/search`：JSON `{query, doc_id?, top_k, mode: auto|semantic|keyword}`；语义优先，auto 在 Chroma/智谱不可用或空结果时回退关键词；返回片段、章节、页码、归一化相关度、warnings、elapsed_ms。semantic 不可用返回 503，未知 doc 404。minio 模式的关键词检索通过 `iter_lines` 直接读 `chunks.jsonl`。
- `GET /api/jobs/{job_id}/artifacts`、`GET /api/documents/{doc_id}/artifacts`、`GET /api/documents/{doc_id}/bids/{run_id}/artifacts`：按 allowlist 列出/下载产物（投标产物含 `compliance.json`、`consistency.json`、`figures/manifest.json` 与 `figures/*.png|jpg|jpeg`）；minio 模式流式返回对象字节并支持 `GET`/`HEAD`/`Range`；路径穿越返回 404，**`profile.json` 永不暴露**。
- `GET /api/bids/profile-template`：返回 `bidder_profile.example.json` 的 JSON 模板。
- `GET/POST /api/companies`、`GET/PUT/DELETE /api/companies/{company_id}`：PostgreSQL 公司库 CRUD；`profile` 为完整 BidderProfile；公司名称必填。
- `POST /api/bids` 支持可选 `company_id`：未提供 profile 时从公司库载入；提供 profile 时使用当前编辑内容，company_id 写入 job payload 便于追踪。

  ```bash
  curl -X POST http://127.0.0.1:8001/api/search \
    -H "Content-Type: application/json" \
    -d '{"query":"施工组织设计","doc_id":"6055594244948","top_k":5,"mode":"auto"}'
  ```
- 上传落盘 `upload/inbox/<job_id>/source.pdf`；minio 模式还会先写 `inbox/<job_id>/source.pdf` 对象（失败返回 503），worker 从对象存储重建本地 source，解析成功后同步 parsed 树并尽力删除 inbox 对象。`doc_id = <文件名>-<内容 sha256 前 12 位>`，相同内容重复上传可命中解析缓存。
- 默认 `TASK_BACKEND=celery`：任务状态保存在 Redis `much-agents:job:<job_id>` hash，Celery worker 消费 `parse` 队列（同时承载 parse 与 bid 任务，按 `Job.kind` 区分）；API 重启后仍可轮询，任务记录 24h TTL。
- Worker 与 API 必须同一 checkout、共享 `.env`；local 模式还需共享 `upload/`，minio 模式通过对象存储交换输入/产物。Windows 用 `--pool=solo`（单任务串行，保护 MinerU 配额），不自动重试（`acks_late=False`），`mark_running` 原子 claim 防重复解析。
- Redis 不可用时 `POST /api/parse` 与 `GET /api/jobs/{id}` 返回 503，health 为 `status=degraded, redis_ok=false`。
- `TASK_BACKEND=local` 使用进程内 `JobStore` + `ThreadJobRunner`，无需 Redis/worker，但 API 重启即丢任务；测试与本地离线开发用它。
- CORS 默认 `*`、无 credentials，兼容 Vite dev 与 Electron `file://`；认证已加（Bearer token），但对外暴露前仍需改成显式来源、加 TLS/安全头并做生产加固。`result.output_dir` 是绝对路径，仅适合本地桌面端。

### 多 Agent 投标文件编写（已实现，`src/bidding/`）

- 入口：CLI `uv run python -m src.cli write <doc_id>` 与 legacy `POST /api/bids`（一次性创建并立即生成），以及新的持久化流程 `/api/bid-runs`（见下）。两者复用 `GET /api/jobs/{id}` 轮询、Redis JobStore 与 Celery `parse` 队列（任务名 `bidding.write_bid`）。
- 产物：`upload/bids/<doc_id>/<run_id>/` 下的 `profile.json`、`outline.json`、`bid.md`、`bid.docx`、`report.json`、`gate.json`、`compliance.json`、`consistency.json`、`sections/*.md`；minio 模式同前缀对象 + 私有 `private/profiles/<doc_id>/<run_id>.json`。
- LangGraph 流程（legacy/CLI）：确定性 plan → 逐节检索（Chroma/关键词兜底）→ 并行 Writer → 并行 Reviewer → 条件 Reviser → 确定性 assemble；只有施工组织设计的 11 个 narrative 子章节经过 LLM，其余表单/附件为确定性模板。
- 投标人资料：`bidder_profile.example.json` 模板；缺失字段一律 `【待填写：...】`，严禁编造公司、人员、证书、业绩、报价、日期。Writer/Reviewer 不接收完整 profile；profile 快照只落本地 run 目录与私有对象 key，不进入 Redis/Celery 消息。
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
- profile 隐私不变：DB 只存 `profile_source/profile_sha256/profile_relpath` 与缺失字段标签；Redis/Celery payload 只有 `run_id/generation_token/section_ids/assemble/mode`；`profile.json` 只存私有对象 key，不在 artifact allowlist、MCP 输出、下载 URL 或 Redis/Celery payload 中。
- minio 模式在 create/generate/assemble/edit/regenerate/export 前 hydration parsed 与 run，写后同步产物；人工编辑/重写/大纲失效会清理远程过期报告、门禁、合规、一致性与配图对象，但保留 `bid.md` 与私有 profile。
- `POST /api/bids`、CLI、旧 artifact 接口保持兼容；staged job 的 artifact 目录通过 job payload 的 `run_id` 解析（`job_id != run_id`）。
- 仍未实现：PDF/DOC 导出、项目级数据隔离、审计日志、Celery beat、Docker Compose；失败章节用现有「重试失败章节」，不做任意章节增删。

### 合规检查、一致性台账与修复方案（P1-B，`src/bidding/compliance.py`、`consistency.py`、`fix_plan.py`）

- `assemble` 在组装后额外生成 `compliance.json`（21 项独立检查）与 `consistency.json`（跨章节事实台账），并把状态/summary 写入 `gate.json` 与 `report.json`；两文件也是 run 目录产物，可直接在 artifact 面板预览/下载。
- 21 项检查固定顺序：公司/项目/招标人/标段/工期/质量/有效期/截止时间/开竣工日期/联系方式/信用代码/法人/授权代理人/项目经理/证书/人员/业绩/财务/保证金/资格/章节完整性/评分覆盖。缺失资料和占位符是 warn，真实矛盾（如公司名、工期不一致）与失败章节是 block。
- 一致性台账覆盖 21 类事实，逐项记录 canonical 值、每处 occurrence（章节、引用、exact/conflict）与 `consistent|conflict|missing|unverified|not_found`；关键事实冲突使台账为 `conflict`，缺失/未核实为 `warning`。
- 门禁消费合规结果：任一 block 级检查失败 → gate 新增 `compliance_block` block；warn 级失败 → `compliance_warnings` warn。直接下载产物仍宽松，只有 `POST /export` 严格拦截。
- 新增接口：`GET /api/bid-runs/{id}/compliance`、`POST .../compliance/check`（重新计算并落盘，不改 run 状态）、`GET .../consistency`、`POST .../compliance/fix-plan`（`check_ids` + `max_items`）、`POST .../compliance/fixes/apply`。
- 修复方案先由一致性冲突生成确定性 replace 项（找不到唯一原文则不生成）；可选 LLM 只提供候选，所有候选都会重新校验 check id、章节可编辑性、`find` 唯一、`replace` 不含未授权金额/日期/证件与占位符。LLM 失败自动回退确定性方案。
- 应用修复要求 final 可编辑 narrative、`find` 恰好出现一次；替换后走 `edit_section` 语义（`final_source="manual"`、run `paused/edited`、清空 gate 与两份报告），下一次 assemble 重新计算。大纲失效、人工编辑、单节重写都会删除过期报告。

### AI 配图（`src/bidding/figures/`）

- 只在**组装时**生成，不新增数据库表；产物为 run 目录下的 `figures/manifest.json` + `figures/fig-*.png`，已加入 artifact allowlist，`profile.json` 仍排除。
- 四类固定图与插入位置（`rules.py`）：组织架构图 `top-07`（章节末尾）、进度横道图 `att-06-04`（替换 `【待填写：施工进度网络图/横道图】`）、施工总平面图 `att-06-05`（替换 `【待填写：施工总平面图】`）、工艺流程图 `narr-06-02`（章节末尾）；`BID_FIGURES_MAX` 默认 4，按大纲顺序截断。
- 数据只来自招标事实、投标人资料与已 redaction 的章节正文；人员/公司/日期/工期缺失时跳过或使用明确「【待填写】」节点，**严禁文生图或编造事实**。工艺步骤可选 LLM 补足，但每一步必须能在正文找到原文引用。
- 采用两遍组装：基础组装与正文 redaction → 规划+渲染 → 插入 figure blocks → 最终组装 → 合规/一致性/门禁。DOCX 真实嵌入图片（`asset_dir` + 宽度限制），`sections/*.md` 图片路径改为 `../figures/...`，`bid.md` 保持 `figures/...`。
- manifest 以 `source_hash`（正文+图数据+版本+宽度）复用未变图片；人工编辑、单节重写、大纲失效、应用修复都会删除 manifest 与图片。`POST /api/bid-runs/{id}/figures/regenerate` 强制重建但需重新组装；active run 返回 409。
- 门禁新增 warn 级 `figures` 检查（失败/跳过提示人工补充），不新增 block，不改变 21 项合规检查。新增 `GET /api/bid-runs/{id}/figures` 与 `POST .../figures/regenerate`；前端「AI 配图」面板支持预览、来源/声明/警告与重新生成。
- 需要 Pillow 与 CJK 字体；找不到字体时该图 failed + warning，不阻断组装。详细说明见 `docs/ai-figures-and-mcp.md`。

### MCP 服务端与客户端（`api/mcp_server.py`、`src/mcp_client/`）

- **服务端**是本地 FastAPI 的薄代理，不直接访问 DB/Redis/Celery/文件系统，复用 `BidRunService` 的守卫、revision CAS、artifact allowlist 与 profile 排除。默认 stdio：`uv run python -m api.mcp_server`；HTTP：`--transport http --host 127.0.0.1 --port 8765`。HTTP 默认仅回环，非回环绑定必须 `MCP_HTTP_TOKEN`，请求需 `Authorization: Bearer`。服务端调用业务 API 时另用 `MCP_API_TOKEN`（service 身份）；`AUTH_ENABLED=true` 且未设置时调用 401，启动日志会警告。
- 工具覆盖 health/文档检索/公司列表/run/大纲/章节/门禁/合规/一致性/job/artifact/配图查询，以及 create/patch/generate/resume/cancel/assemble/edit/regenerate/fix-plan/apply-fix/regenerate-figures 等写操作。长任务只返回 `job_id`，由调用方轮询 `get_job`。`create_bid_run` 不接受 inline profile；`read_artifact_text` 强制 allowlist 且拒绝 `profile.json`；`list_companies` 只返回 id/名称/信用代码。
- **客户端**默认关闭（`MCP_CLIENT_ENABLED=false`），只连接 `mcp_servers.json` 中声明的服务器与 `allowed_tools`；参数只能使用固定占位符（`${query}`、`${doc_id}`、`${top_k}`、`${company_name}`、`${project_name}`、`${section_id}`、`${section_title}`、`${figure_spec}`），未知占位符启动报错。LLM 不能发现或调用任意 MCP 工具。
- 支持 `stdio` 与 `streamable_http`；命令/参数/URL/headers/env 中的 `${VAR}` 从环境展开（如 `${DRAWING_TOKEN}`），缺失变量报错。真实 `mcp_servers.json` 已 gitignore，示例为 `mcp_servers.example.json`；`.mcp.json` 也已 gitignore，示例为 `.mcp.json.example`。
- `purpose=evidence|company` 的工具在每节本地检索后并发调用，结果合并为带 `source="mcp"` / `MCP/<server>/<tool>` 标记的不可信证据；外部证据预算最多为总预算 1/3，未用满回补本地。单服务器失败/超时只 warning，不阻断章节。
- `purpose=figure` 仅在 `BID_FIGURES_EXTERNAL=true` 时用于 `site_plan` / `process_flow`；接收 ImageContent/内嵌 blob/resource link/图片 URL，校验 MIME、base64、大小、Pillow 解码与尺寸，失败自动回退内置 Pillow。发送的 spec 不含 profile。
- CLI：`uv run python -m src.mcp_client list|check|call`；`check` 只读，`call` 仅允许配置内工具。详细配置、隐私边界与手工测试见 `docs/ai-figures-and-mcp.md`。

### 尚未实现（后续阶段）

- **待办**：前端 IPC（打开本地目录）、PDF/DOC 导出、项目实体与项目级 ACL（P0-B）、审计日志（P0-C）、生产部署/CI/备份/TLS/限流（P0-D）、文生图/配图审批流、MCP OAuth 与前端 MCP 配置 UI。

### 配置与数据

- `.env`（已 gitignore）由 `.env.example` 复制；关键变量：`DEEPSEEK_API_KEY`、`ZHIPU_API_KEY`、`MINERU_API_KEY`、`DATABASE_URL`、`REDIS_URL`，以及 API/Chroma/MinerU/投标参数（`API_HOST/API_PORT`、`UPLOAD_DIR`、`MAX_UPLOAD_MB`、`API_JOB_WORKERS`、`CORS_ORIGINS`、`TASK_BACKEND`、`CELERY_BROKER_URL`、`CELERY_WORKER_POOL`、`JOB_TTL_SECONDS`、`DB_AUTO_MIGRATE`、`BID_RUN_MAX_ATTEMPTS`、`BID_RUN_STALE_SECONDS`、`BIDS_DIR`、`BID_MAX_SECTIONS`、`BID_RETRIEVAL_TOP_K`、`BID_MAX_TOKENS`、`BID_MAX_REVIEW_REVISIONS`、`BID_LLM_CONCURRENCY`、`BID_USE_CHROMA`）。
- 认证：`AUTH_ENABLED`（默认 true）、`AUTH_SESSION_TTL_HOURS`、`AUTH_LOGIN_MAX_FAILURES`、`AUTH_LOCKOUT_MINUTES`、`MCP_API_TOKEN`。`AUTH_ENABLED=false` 仅用于无数据库的本机开发，所有请求按本地 admin 处理。
- 对象存储：`STORAGE_BACKEND`（默认 minio）、`MINIO_ENDPOINT`、`MINIO_SECURE`、`MINIO_BUCKET`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_ROOT_USER`、`MINIO_ROOT_PASSWORD`、`MINIO_CONSOLE_PORT`（仅 start.bat）、`MINIO_TIMEOUT_SECONDS`。
- AI 配图：`BID_FIGURES_ENABLED`、`BID_FIGURES_MAX`、`BID_FIGURES_USE_LLM`、`BID_FIGURES_FONT`、`BID_FIGURES_WIDTH_PX`、`BID_FIGURES_IMAGE_WIDTH_CM`、`BID_FIGURES_PAGE_ESTIMATE`、`BID_FIGURES_EXTERNAL`。
- MCP 服务端：`MCP_API_BASE_URL`、`MCP_HTTP_HOST`、`MCP_HTTP_PORT`、`MCP_HTTP_TOKEN`、`MCP_API_TOKEN`（调用业务 API 的 service token）。MCP 客户端：`MCP_CLIENT_ENABLED`、`MCP_CLIENT_CONFIG`、`MCP_CLIENT_MAX_SERVERS`、`MCP_CLIENT_MAX_TOOLS`、`MCP_CLIENT_MAX_CHARS`、`MCP_CLIENT_MAX_IMAGE_BYTES`、`MCP_CLIENT_MAX_IMAGE_DIMENSION`、`MCP_CLIENT_TIMEOUT_SECONDS`。
- 前端 http(s) 页面默认同源 `/api`，Electron `file://` 默认 `http://127.0.0.1:8001`；可在 `frontend/.env` 设置 `VITE_API_BASE_URL`，或在界面「API 连接设置」中覆盖（保存到 localStorage；留空恢复同源）。
- PostgreSQL：`DATABASE_URL` 指向 `much-agents-postgres`；`companies` 表由 Alembic `0001` 基线创建，`bid_runs`/`bid_outline_sections`/`bid_section_states` 由 `0002` 创建，`users`/`user_sessions` 由 `0003` 创建。API 启动默认执行 `alembic upgrade head`（advisory lock 防并发迁移），worker 不迁移。数据库不可用时公司/run/用户/认证接口返回 503，不影响解析与 legacy 标书任务；pytest 使用内存 fake，不连接真实数据库。已有只含 `companies` 的开发库先 `alembic stamp 0001` 再 `upgrade head`。
- LLM 默认 `LLM_PROVIDER=deepseek`、`DEEPSEEK_MODEL=deepseek-flash`；嵌入模型 `EMBEDDING_MODEL=embedding-3`（智谱）。
- 检索：`BID_USE_CHROMA=true` 且 `ZHIPU_API_KEY` 可用时启用语义；`auto` 不可用或空结果时回退关键词。`BID_RETRIEVAL_TOP_K` 只控制标书 Writer，搜索 top_k 由请求参数控制（1–30）。
- `pyproject.toml` 将阿里云 PyPI 镜像配置为默认 index；Python 固定 `>=3.12,<3.13`，由 `.python-version` 约束。
- 数据目录（均已 gitignore）：`chroma_db/` 向量库；`upload/` 是本地输入/产物目录（`upload/inbox/<job_id>/source.pdf` 为 API 上传，`upload/parsed/<doc_id>/` 为解析输出，`upload/bids/<doc_id>/<run_id>/` 为投标文件产物，配图在 `.../figures/`）——minio 模式下它是工作缓存，权威对象在 MinIO bucket；Docker 数据卷 `much-agents-minio-data` 保存 MinIO 对象；`.run/` 保存一键启动的 PID 状态（POSIX 上还有各服务日志）。
- MCP 配置文件：真实 `mcp_servers.json` 与 `.mcp.json` 已 gitignore；提交的模板为 `mcp_servers.example.json`、`.mcp.json.example`。

## 注意

- Windows 环境开发；长任务（解析/抽取/编写）不要阻塞 HTTP 请求，走 Celery 或轮询任务模型。
- API 默认开启认证；新增 `/api/*` 业务路由必须挂 `require_user` 或更严格的角色依赖，只有 health/login 可以公开。不要用「所有 POST 都算写」的中间件，因为 `/api/search` 是需认证的只读 POST。`AUTH_ENABLED=false` 只能用于本机开发。
- 不要提交 `.env`、`chroma_db/`、`upload/`、`node_modules/`、`dist/`、`release/`、真实的 `mcp_servers.json` 或 `.mcp.json`。
- `profile.json` 永不进入 artifact allowlist、MCP 工具输出、Redis/Celery payload 或任何下载 URL；只存 MinIO 私有 key `private/profiles/<doc_id>/<run_id>.json`。外部证据默认不可信，外部绘图默认关闭。认证 token 也不得进入 URL、日志或 Redis/Celery payload。
