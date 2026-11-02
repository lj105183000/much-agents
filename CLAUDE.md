# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 固定任务

- 每次修改完成后，都应创建一个对应的 Git commit，便于后续追踪和回滚。

## 项目概述

多 Agent 标书编写系统（Much Agents）：解析招标文件 PDF → 结构化抽取 → 入 Chroma 向量库 → 由 LangGraph 多 Agent 生成投标文件（Markdown + Word），前端为 React + Electron 桌面端。技术栈参考 [ZhiBiao](https://github.com/Foulereee/ZhiBiao)。

**当前状态**：已实现文档解析管线 `src/parsing/`（含附件上传 API/UI、附件证据追加、MinerU OCR 回退与 BOQ 专项检索）、FastAPI 上传/任务接口 `api/`、Celery/Redis 异步任务后端（含 local 内存兜底）、多 Agent 投标文件编写 `src/bidding/`、招标文件片段检索、PostgreSQL 公司库与持久化投标 run/大纲/章节状态、可编辑大纲、章节级续跑与单节编辑/重写、服务端投标历史、评审评论/@提及/指派与 SSE 实时进度、站内通知/Webhook/SMTP、Playwright 浏览器 E2E（隔离 PostgreSQL 栈）与只读 HTTP/SSE 压测、独立 21 项合规检查、跨章节一致性台账与安全修复方案、导出前门禁、确定性 AI 配图（组织架构/工艺流程/进度横道/施工总平面并真实嵌入 Markdown/DOCX）、MCP 服务端（stdio + 可选 HTTP）与白名单 MCP 客户端（外部证据/企业资料库/外部绘图）、可选 MinIO 对象存储（`STORAGE_BACKEND=minio|local`，默认 minio；artifact 流式下载与私有 profile）、本地账号/可撤销会话/5 角色 RBAC/管理员用户管理/MCP 服务账号（P0-A）、项目注册表与对象级 ACL（P0-B）、append-only 审计哈希链（P0-C）、React/Electron 前端登录/项目切换/审计/自助改密与角色感知工作台、Nginx 前端托管/API 反向代理、Windows 一键启动/停止脚本、生产 Docker Compose/TLS/CI/备份恢复脚本（P0-D）与测试 `tests/`。生产就绪增强已落地：L0 指标与离线基线 `scripts/baseline_metrics.py`、私有评测集构建 `scripts/eval_dataset.py`、LLM 调用账本与成本计量（`llm_call_log`/`run_usage`）、JSON 日志与 request_id 贯穿 API→Celery、`/api/metrics` 核心指标、Chroma readiness、stale reaper/章节 generation_token/独立心跳/取消终态/DLQ、加密异地备份与 systemd timer、assembled `content_sha256` 导出校验、审批 API 与 `BID_REQUIRE_APPROVAL`、DOCX TOC/页码/DPI 质量检查、MinIO/Chroma/Postgres/Redis digest pin 与 CI Trivy/Syft SBOM、L2 judge/校准工具与可选夜间回归 workflow；迁移 0005→0009、临时库往返与生产 Compose 备份/恢复演练已在本地 Docker Desktop 验证；`monitoring` profile 提供固定 digest 的 Prometheus/Alertmanager/Grafana（Alertmanager 通知渠道为占位配置）；每个章节的检索 query/mode/hit 摘要写入 `retrieval_snapshots`（迁移 `0010`）；prompt 文件哈希与默认模型/温度登记到 `prompt_registry`（迁移 `0011`，`scripts/seed_prompts.py`）；章节版本历史/diff/恢复（迁移 `0012`，`bid_section_versions`；`0013` 增加 outline revision/审查摘要）与经 Headless LibreOffice 的 PDF 导出（`POST /api/bid-runs/{id}/export/pdf`）已实现；需求登记/覆盖矩阵（`requirements.json`）与证据引用契约（`citations.json`，生成时标注、确定性校验、导出前剥离标记）也已落地。DOC（97-2003）导出、评论/SSE、文生图与前端 IPC 仍未实现；后续阶段以本文件与代码为准。

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
uv run python -m api.projects list                                            # 列出项目
uv run python -m api.projects create --name 项目名 --slug demo-2026            # 创建项目
uv run python -m api.projects add-member --project-id <uuid> --username zhangsan --role writer  # 项目成员
uv run python -m api.projects backfill-documents                              # 把已解析文档注册到默认项目（启用 ACL 前必跑）
uv run python -m api.audit verify                                             # 校验审计哈希链
uv run chroma run --path ./chroma_db         # 启动 Chroma，监听 8000
uv run python -m src.storage check           # 检查 MinIO 连通性与各前缀对象数
uv run python -m src.storage sync            # 把本地存量 upload/ 增量同步到 MinIO
uv run pytest                                # 运行全部测试（全部 mock 网络）
uv run pytest tests/test_toc.py::test_hierarchy_and_end_pages   # 单个测试
uv run python scripts/run_e2e.py            # 重建隔离 e2e 库 + 启动 API/Vite + Playwright
uv run python scripts/run_e2e.py --reuse-db --setup-only --keep-stack  # 只准备环境供调试
uv run python -m scripts.load_test --endpoint health --endpoint runs    # 只读 HTTP/SSE 压测
cd frontend && npx playwright install chromium                          # 首次安装浏览器
uv run python -m src.parsing upload/6055594244948.pdf           # 解析 + 抽取 + 入库
uv run python -m src.parsing <pdf> --parser pymupdf --no-extract --no-ingest  # 离线仅解析
uv run python -m scripts.attach_evidence --doc-id <doc_id> --file <boq.pdf|csv>  # 追加工程量清单/补充资料（BOQ 专项检索）
uv run python -m scripts.attach_evidence --doc-id <doc_id> --list                # 列出附件
uv run python -m scripts.attach_evidence --doc-id <doc_id> --remove <att_id>     # 删除附件及 chunks/Chroma 向量
uv run uvicorn api.main:app --host 0.0.0.0 --port 8001          # 启动 API（单进程）
uv run python -m api                                            # 读取 API_HOST/API_PORT
uv run celery -A api.celery_app:celery_app worker -Q parse,bid --pool=solo --loglevel=INFO  # 开发单 worker 同时消费 parse 与 bid
docker compose -f docker-compose.prod.yml up -d --build                      # 生产单机部署（Nginx TLS + 全部依赖）
scripts/backup.sh && scripts/restore.sh backups/<timestamp>                  # 备份 / 恢复（bash，生产主机）
uv run python -m scripts.worker_healthcheck parse                             # 检查是否有 worker 消费指定队列
uv run python -m scripts.baseline_metrics                                    # 从已有 parsed/bids 产物生成 L0 离线基线
uv run python -m scripts.eval_dataset build --all                            # 构建私有评测集到 eval-data/（gitignored）
uv run python -m scripts.eval_dataset validate                               # 校验评测集结构并提示待人工标注项
uv run python -m scripts.eval_review                                         # 导出人工复核 Markdown 清单到 eval-data/reviews/
uv run python -m scripts.eval_judge --input section.json                      # 对单节运行盲评 LLM-as-judge
uv run python -m scripts.eval_calibrate --input pairs.json                    # judge 与人工标签的一致率/kappa
uv run python -m scripts.eval_generate --dry-run                              # 打印夜间样本生成命令
uv run python -m scripts.eval_regression                                      # 最新 run 与 L0 基线比较
uv run python -m scripts.backfill_section_versions --dry-run                # 检查缺少版本历史的老章节
uv run python -m scripts.backfill_section_versions                          # 为老章节补一条版本记录
uv run python -m scripts.seed_prompts --dry-run                              # 查看当前 prompt 注册条目
uv run python -m scripts.seed_prompts                                       # 写入 prompt_registry
uv run python -m scripts.dlq --limit 20                                      # 查看 Redis 死信列表
uv run python -m scripts.check_image_pins docker-compose.prod.yml            # 检查生产镜像是否仍使用 :latest
uv run python scripts/acceptance_check.py --config-only                     # 生产配置离线验收（无网络）
DEPLOY_TEST_USERNAME=admin DEPLOY_TEST_PASSWORD=... uv run python scripts/acceptance_check.py \
  --base-url https://much-agents.internal --cafile deploy/nginx/certs/fullchain.pem  # 生产主机验收
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
- MinIO 容器使用固定 digest 的 `minio/minio` 镜像、数据卷 `much-agents-minio-data`，映射 `MINIO_ENDPOINT` 端口到 9000、`MINIO_CONSOLE_PORT` 到同名控制台端口；启动脚本等待 `/minio/health/live`，并把 `STORAGE_BACKEND` 传给 API/worker。端口被占用时会提示修改 `MINIO_ENDPOINT`/`MINIO_CONSOLE_PORT`。
- PID 记录在 `.run/start.json`（已 gitignore）；再次运行会先清理上次拉起的进程，`stop.bat` 在 Windows 用 `taskkill /T /F` 结束进程树。
- 端口已有 Much Agents API 时，仅当 `GET /api/live` 返回 2xx 才复用；health 正常但 `/api/live` 404 说明是旧版本 API，脚本会提示先 `stop.bat`。Vite 端口已有可访问页面时直接复用；被其他程序占用则报错。`--skip-docker` 可连接已有 PostgreSQL/Redis/MinIO；本地 Redis 不可用时自动降级为 `TASK_BACKEND=local`，MinIO 不可用时降级为 `STORAGE_BACKEND=local`。
- 每个服务使用独立控制台窗口；强制关闭 supervisor 窗口不会触发自动停止，改用 `stop.bat`。`--dry-run` 不写 `.env`、不启动进程。

### 前端（`frontend/`）

```bash
npm install
npm run dev            # Vite 开发服务器 http://localhost:5173
npm run build          # tsc -b && vite build，产物到 frontend/dist/
npm run lint           # oxlint
npm test               # Vitest 单元测试
npm run test:e2e       # Playwright（需先由 scripts/run_e2e.py 准备栈）
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
- **MinIO**：Docker 容器 `much-agents-minio`（固定 digest 的 `minio/minio`，数据卷 `much-agents-minio-data`，`--restart unless-stopped`）；`start.bat` 自动启动/复用，`stop.bat` 不关闭。端口以 `.env` 的 `MINIO_ENDPOINT` 为准（默认 9000/9001；本机因 `bidding-agent` 占用改为 9010/9011），凭据来自 `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`（缺失时回退 `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`）。`STORAGE_BACKEND=local` 时不需要。详细说明见 `docs/minio-storage.md`。
- **Chroma**：`uv run chroma run --path ./chroma_db`，向量数据落在 `chroma_db/`（已 gitignore）。
- **MinerU**：云端解析，需要 `MINERU_API_KEY`（有效期 90 天）；单文件页数上限 200，超过需先分片。

## 架构要点

### 前端 / Electron

- Electron 主进程是 CommonJS：`frontend/electron/main.cjs`。带 `--dev` 时加载 `http://localhost:5173`，否则通过 `file://` 加载 `dist/index.html`。
- `vite.config.ts` 设置 `base: './'`，因为 Electron 生产环境用 `file://` 加载，资源路径必须相对——不要改成绝对路径。
- 渲染层：React 19 + TypeScript + TailwindCSS 4（通过 `@tailwindcss/vite` 插件，无 `tailwind.config.*`）；Markdown 预览使用 `react-markdown` + `remark-gfm`，默认不渲染原始 HTML。
- 工作台为左侧导航 + 六个业务视图：① 上传解析 ② 编写标书 ③ 公司库 ④ 文档检索 ⑤ 文档库 ⑥ 任务历史；管理员额外看到 ⑦ 用户管理。包含 job 进度、profile 表单/JSON、公司选择、结果统计、产物预览与下载。「文档库」每个文档新增「附件证据」面板：上传 PDF/CSV/XLSX/XLSM/TXT/MD、查看 attach job 进度、列出/删除附件；写操作按角色禁用，后端仍是权威。
- 「编写标书」为分阶段流程：选择文档/公司/资料 → 创建 run 并编辑大纲（标题、目标字数、编写要点、写作重点；表单/附件锁定）→ 生成/续跑/取消 → 自动组装与门禁检查 → 严格导出 DOCX。已完成章节可编辑正文或重新生成本节；旧稿在新稿完成前保留。生成进行中通过 SSE 显示「实时」徽章，进度/阶段/章节状态由 `GET /api/bid-runs/{id}/events` 推送，断线自动重连并以 2 秒轮询兜底。「评审评论与指派」面板位于生成工作台与门禁之间：可针对整份标书或单个章节发表评论、@项目成员、指派处理人、解决/重开；只读角色可查看，复核角色可发表与处理。Header 的通知铃铛显示未读数（25 秒轮询 + 聚焦刷新），点击评论提及/指派或 run 组装/门禁通知可直接打开对应 run。门禁下方显示「合规检查与一致性台账」：21 项检查、跨章节事实核对、确定性/AI 修复方案与一键应用（应用等同人工编辑，需重新组装）。
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

### 附件证据（`src/parsing/attachments.py`、`scripts/attach_evidence.py`）

- 把工程量清单/补充资料以 PDF/CSV/XLSX/XLSM/TXT/MD 追加到已解析文档：chunk id `<doc_id>:att-<sha12>:<ordinal>`，写入 `chunks.jsonl` 与 `attachments.json`，以 `prune=False` upsert Chroma（不删除主文档 chunks），minio 模式同步 `parsed/<doc_id>/` 对象。
- 检索时若文档含 `附件：` chunk，每节额外执行一次 BOQ 专项 query 并把附件命中前置合并（主证据按 chunk id 去重后跟进，仍受 `BID_MAX_EVIDENCE_CHARS` 预算限制）。
- PDF 用 PyMuPDF 提取文本与 `find_tables()` 表格；无文本层或存在无文本页时自动回退 MinerU OCR（需 `MINERU_API_KEY`，`--no-ocr` 可关闭；已验证扫描件 OCR），CSV/XLSX/XLSM 作为表格 chunk（XLSX 用 openpyxl）。旧 `.xls` 未支持，先另存为 `.xlsx`/CSV/PDF。
- 上传校验：PDF 拒绝加密与超过 `MAX_PDF_PAGES`；XLSX 校验 zip 结构、条目 ≤2000、解压 ≤100MB（防 zip bomb）；CSV/TXT/MD 仅接受 UTF-8/GB18030 文本、单文件 ≤20MB 并拒绝 NUL；解析上限 20,000 行/500,000 字符。`validate_attachment_file` 在 API 上传与解析前各执行一次。
- `--list` 列出附件；`--remove <attachment_id>` 删除 `chunks.jsonl`/`attachments.json` 中的附件记录与 Chroma 向量（`--no-chroma-delete` 可保留向量），minio 模式同步对象。合成测试附件必须明确标注，并在真实使用前用 `--remove` 清理。

### 对象存储（`src/storage/`，默认 MinIO）

- `STORAGE_BACKEND=local|minio`（默认 minio）。`local` 与旧版本完全一致；`minio` 模式下 `inbox/`、`parsed/`、`bids/` 对象是权威来源，本地 `upload/` 只是任务工作缓存。`profile.json` 只存私有 key `private/profiles/<doc_id>/<run_id>.json`。
- `Storage` 协议 + `LocalStorage`/`MinioStorage`（官方 `minio` SDK）；`StorageLayout` 把 key 映射到 `UPLOAD_DIR`/`PARSED_DIR`/`BIDS_DIR`，**不假设 parsed/bids 位于 upload 下**。`build_storage()` 在 local 模式不 import `minio`。
- 读取兜底：远程缺对象时回退本地缓存，`list()` 合并远程与本地 key（远程优先）；`sync_up` 按 `(size, mtime_ns)` 增量上传，`delete_missing` 只用于 `figures/`、`sections/` 等窄子树。
- 流程边界：`hydration.py` 提供 `ensure_document_cached`、`ensure_run_cached`、`ensure_private_profile`、`sync_document`、`sync_run`、`sync_run_derived`；异步服务统一用 `asyncio.to_thread` 调用同步存储 API。
- artifact 下载由 `api/streaming.py` 流式返回，支持 `Range`(206/416)、`HEAD`、`ETag` 与 RFC 5987 中文文件名，**不使用 presigned 重定向**；`profile.json` 不在 allowlist。MinIO 不可用时 artifact/上传/目录/搜索/run 接口返回 503；health 仍 200 但 `status=degraded`、`storage_ok=false`。
- `uv run python -m src.storage check|sync` 用于连通性排障与存量迁移；启动时不做自动全量迁移。详见 `docs/minio-storage.md`。

### 认证、会话与 RBAC（P0-A，`api/auth.py`、`api/users.py`）

- 默认拒绝：`build_router()` 把业务路由挂在 `Depends(require_user)` 下，公开例外仅 `GET /api/health`、`GET /api/live`、`GET /api/ready` 与 `POST /api/auth/login`。`DOCS_ENABLED=false` 或 `ENVIRONMENT=production` 时 `/docs`、`/redoc`、`/openapi.json` 不注册（生产 Nginx 也返回 404）。`POST /api/search` 是需认证的只读 POST，不能按 HTTP 方法一刀切。
- PostgreSQL `users`/`user_sessions`（Alembic `0003`）是账号与会话的事实来源；密码 Argon2，数据库只存会话 token 的 SHA-256。登录、登出、改密、重置密码、停用账号、修改角色均有撤销语义；默认 12h TTL、5 次失败锁 15 分钟。最后一个启用管理员不可停用/降级。
- 角色：`admin`、`bid_manager`、`writer`、`reviewer`、`viewer`，外加不可登录的 `service` 主体。`require_write` = admin/bid_manager/writer；`require_review` 再加 reviewer；`require_manage` = admin/bid_manager；`require_admin` 仅交互式 admin（service 拒绝）。`service` 可通过 write/review/manage，但不能管理用户、删除公司或强制导出。
- 端点：`POST /api/auth/login`、`GET /api/auth/me`、`POST /api/auth/logout`、`POST /api/auth/password`；管理员 `GET/POST /api/users`、`PATCH /api/users/{user_id}`、`POST /api/users/{user_id}/password`。守卫映射：parse/bids/bid-runs 创建与所有写操作 `require_write`；合规 check/fix-plan `require_review`；公司新增/编辑与普通导出 `require_manage`；用户管理、公司删除、`force=true` 导出 `require_admin`。
- 公司数据脱敏：非 manage/admin 的 `GET /api/companies` 去掉电话、开户行、银行账号，`GET /api/companies/{id}` 还返回空 `profile`；完整 `BidderProfile` 仅 admin/bid_manager 可见。
- CLI `uv run python -m api.users create-admin|create-user|reset-password|list`；交互式 getpass 或 `--password-stdin`，不提供 `--password`。密码 12–128 字符、不得等于用户名、拒绝少量常见弱口令。
- `AUTH_ENABLED=false` 是仅限本机的逃生口：所有请求以本地 admin 处理，`/api/auth/me` 返回 `auth_enabled=false`，登录接口 400；禁止在监听 `0.0.0.0`/内网时使用。`AUTH_ENABLED=true` 但数据库不可用时受保护接口 503（fail closed），不会匿名放行。
- `MCP_API_TOKEN` 是 MCP 服务端调用业务 API 的 service token（与入站 `MCP_HTTP_TOKEN` 无关）；认证开启时必须设置，否则 MCP 调用 401。轮换需同时重启 API 与 MCP。
- 生产加固：`ENVIRONMENT=production` 启动时 fail-fast（拒绝关闭认证、开放 CORS、`/docs`、自动迁移、非 celery/minio、开发凭据、过短 MCP token 等）；`API_HOST` 默认 `127.0.0.1`；生产 `DOCS_ENABLED=false` 且 Nginx 对 `/docs` 返回 404；CORS 显式 methods/headers；登录按 IP 失败窗口限流（`AUTH_LOGIN_IP_*`，仅信任 `TRUSTED_PROXIES` 的 `X-Forwarded-For`）；Argon2 并发受 `AUTH_HASH_CONCURRENCY` 限制；最后管理员检查使用 `pg_advisory_xact_lock` 消除 TOCTOU；SQL 引擎 `hide_parameters=True`，错误日志不含 SQL 参数。
- 当前边界：没有 MFA/OIDC/LDAP、没有 IP 白名单；token 仍存 localStorage（依赖 CSP/HTTPS/Electron 守卫）；`MCP_API_TOKEN` 为静态共享 secret。详见 `docs/authentication.md`。

### 项目、审计与生产部署（P0-B/C/D）

- **项目隔离（P0-B）**：Alembic `0004` 新增 `projects`、`project_members`、`documents`（PK `(project_id, doc_id)`），`bid_runs.project_id` 非空；默认项目 `00000000-0000-0000-0000-000000000001`。业务请求必须带 `X-Project-Id`（缺失 400，非成员/不存在 404）；有效权限 = 全局角色 AND 项目角色（`manager|writer|reviewer|viewer`）。`require_project_read/write/review/manage` 和 `require_run_*` 是统一守卫；文档/检索/任务/产物/run 全部按项目过滤。公司库保持全局共享。CLI `python -m api.projects` 管理项目、成员与 `backfill-documents`；首次启用 ACL 前必须回填历史解析文档。详见 `docs/projects.md`。
- **审计（P0-C）**：Alembic `0005` 新增 `audit_events`，数据库触发器禁止 UPDATE/DELETE/TRUNCATE；追加时用 `pg_advisory_xact_lock` 串行化并计算 `sha256(prev_hash + canonical_json)` 链。中间件校验/生成 `X-Request-ID`，所有写请求有显式事件或 `http.request` 兜底；敏感操作 `required=True`，审计失败返回 503。管理员接口 `GET /api/audit`、`/api/audit/verify`、`/api/audit/export.csv`（CSV 公式转义），CLI `python -m api.audit verify`。审计只记元数据，不记密码/token/profile/正文。详见 `docs/audit.md`。
- **生产部署（P0-D）**：多阶段 `Dockerfile`（`api-base`/`api`/`worker`/`migrate`/`web-build`/`web`）+ `docker-compose.prod.yml`，仅 Nginx 发布 80/443，Postgres/Redis/MinIO/Chroma 仅内部；`migrate` 成功后才启动 API/worker；API/worker 非 root、只读根文件系统。Nginx TLS 1.2/1.3、HSTS、CSP（内联主题脚本 SHA-256 固定）、登录/API `limit_req`、`/docs` 404；`deploy/env.production.example` 为占位模板（真实文件已 gitignore）。Celery 拆分 `parse`/`bid` 队列（开发单 worker 用 `-Q parse,bid`），`acks_late`/`reject_on_worker_lost`/prefetch 1/time limits。健康检查 `GET /api/live`（进程）与 `GET /api/ready`（依赖，5s 缓存，失败 503）。`scripts/backup.sh`（pg_dump + MinIO mirror + 可选 Chroma + SHA256SUMS）与 `scripts/restore.sh`；`.github/workflows/ci.yml` 覆盖后端/迁移/前端/E2E/Docker/安全扫描。`scripts/acceptance_check.py`（仅标准库）在目标主机检查 env/证书/备份、DNS、内部端口暴露、TLS 剩余天数、`/healthz`/`/api/live`/`/api/ready`、`/docs` 与 `/api/metrics` 404、HSTS/CSP 等安全头、HTTP→HTTPS 跳转和可选登录冒烟；`--config-only` 可离线检查配置。详见 `docs/deployment.md` 与 `docs/production-acceptance.md`。

### FastAPI 上传接口（已实现，`api/`）

- `POST /api/parse`：multipart `file`，查询参数 `parser=auto|mineru|pymupdf`、`extract=true`、`ingest=true`；**默认执行完整流程**；返回 202 + `job_id`/`poll_url`/`doc_id`。
- `POST /api/documents/{doc_id}/attachments`：multipart 附件（PDF/CSV/XLSX/XLSM/TXT/MD），查询参数 `ingest=true`、`ocr=true`；创建 `kind=attach` job，复用 parse 队列/worker（`execute_parse_job` 按 kind 分支），返回 202 + `poll_url`；`GET /api/documents/{doc_id}/attachments` 列出附件；`DELETE /api/documents/{doc_id}/attachments/{attachment_id}` 删除 chunks/Chroma/MinIO 对象。三者均挂 `require_document_read|write` 并按项目隔离；上传/删除写审计且 `required=True`。
- `GET /api/jobs/{job_id}`：200 返回状态/进度/result（失败任务也 200）；未知 404。
- `GET /api/health`：返回 `storage_backend`/`storage_ok`；MinIO 不可达时 `status=degraded` 但仍 200。`GET /api/live` 始终 200；`GET /api/ready` 检查 Redis/DB/storage/Chroma，失败 503，结果缓存 `READINESS_CACHE_SECONDS`；`READINESS_STRICT=false` 时 Chroma 失败返回 200+degraded。`GET /api/metrics` 返回 Prometheus 文本指标，`METRICS_TOKEN` 可要求 Bearer；生产 Nginx 对 `/api/metrics` 返回 404，由内网 Prometheus 直接抓取。
- `GET /api/documents`：只列出当前项目 `documents` 注册表内的文档（minio 模式结合对象列表），返回项目名、页数、chunks、bid_ready 与历史 bid run。
- `POST /api/search`：JSON `{query, doc_id?, top_k, mode: auto|semantic|keyword}`；只检索当前项目允许的文档；语义优先，auto 在 Chroma/智谱不可用或空结果时回退关键词；返回片段、章节、页码、归一化相关度、warnings、elapsed_ms。semantic 不可用返回 503，项目外/未知 doc 404。minio 模式的关键词检索通过 `iter_lines` 直接读 `chunks.jsonl`。
- `GET /api/jobs/{job_id}/artifacts`、`GET /api/documents/{doc_id}/artifacts`、`GET /api/documents/{doc_id}/bids/{run_id}/artifacts`：先校验项目归属，再按 allowlist 列出/下载产物（投标产物含 `compliance.json`、`consistency.json`、`figures/manifest.json` 与 `figures/*.png|jpg|jpeg`）；minio 模式流式返回对象字节并支持 `GET`/`HEAD`/`Range`；路径穿越返回 404，**`profile.json` 永不暴露**；投标产物还包含 `requirements.json`（需求登记/覆盖矩阵）与 `citations.json`（证据引用校验）；`bid.docx`/`bid.pdf` 已从 allowlist 移除，DOCX/PDF 只能走严格门禁导出。
- `GET/POST/PATCH/DELETE /api/projects*`：项目列表（普通用户只看自己的项目）、创建/修改/删除、成员管理（写操作仅 admin）；`GET /api/audit`、`/api/audit/verify`、`/api/audit/export.csv` 仅 admin。
- `GET /api/bids/profile-template`：返回 `bidder_profile.example.json` 的 JSON 模板。
- `GET/POST /api/companies`、`GET/PUT/DELETE /api/companies/{company_id}`：PostgreSQL 公司库 CRUD；`profile` 为完整 BidderProfile；公司名称必填。
- `POST /api/bids` 与 `/api/bid-runs*` 需要 `X-Project-Id`；`POST /api/bids` 支持可选 `company_id`：未提供 profile 时从公司库载入；提供 profile 时使用当前编辑内容，company_id 写入 job payload 便于追踪。
- 评论与实时进度：`GET /api/project-members` 返回当前项目成员摘要；`GET/POST /api/bid-runs/{run_id}/comments`、`PATCH /api/bid-runs/{run_id}/comments/{comment_id}` 管理评论/@提及/指派/解决；`GET /api/bid-runs/{run_id}/events` 返回 `text/event-stream` 实时快照（需 Bearer 头，token 不进 URL）。
- 通知：`GET /api/notifications`（当前用户、当前项目，`unread_only/limit/offset` 与未读数）、`GET /api/notifications/unread`、`POST /api/notifications/{id}/read`、`POST /api/notifications/read-all`；全部挂 `require_project_read`，只能读写当前用户自己的通知。

  ```bash
  curl -X POST http://127.0.0.1:8001/api/search \
    -H "Content-Type: application/json" \
    -d '{"query":"施工组织设计","doc_id":"6055594244948","top_k":5,"mode":"auto"}'
  ```
- 上传落盘 `upload/inbox/<job_id>/source.pdf`；minio 模式还会先写 `inbox/<job_id>/source.pdf` 对象（失败返回 503），worker 从对象存储重建本地 source，解析成功后同步 parsed 树并尽力删除 inbox 对象。`doc_id = <文件名>-<内容 sha256 前 12 位>`，相同内容重复上传可命中解析缓存。
- 默认 `TASK_BACKEND=celery`：任务状态保存在 Redis `much-agents:job:<job_id>` hash；生产由 `worker-parse` 消费 `parse` 队列、`worker-bid` 消费 `bid` 队列，开发单 worker 用 `-Q parse,bid` 同时消费（按 `Job.kind` 区分）；API 重启后仍可轮询，任务记录 24h TTL。
- Worker 与 API 必须同一 checkout、共享 `.env`；local 模式还需共享 `upload/`，minio 模式通过对象存储交换输入/产物。Windows 用 `--pool=solo`（单任务串行，保护 MinerU 配额），不自动重试（`acks_late=False`），`mark_running` 原子 claim 防重复解析。
- Redis 不可用时 `POST /api/parse` 与 `GET /api/jobs/{id}` 返回 503，health 为 `status=degraded, redis_ok=false`。
- `TASK_BACKEND=local` 使用进程内 `JobStore` + `ThreadJobRunner`，无需 Redis/worker，但 API 重启即丢任务；测试与本地离线开发用它。
- CORS 默认 `*`、无 credentials，兼容 Vite dev 与 Electron `file://`；认证已加（Bearer token），但对外暴露前仍需改成显式来源、加 TLS/安全头并做生产加固。`result.output_dir` 是绝对路径，仅适合本地桌面端。

### 多 Agent 投标文件编写（已实现，`src/bidding/`）

- 入口：CLI `uv run python -m src.cli write <doc_id>` 与 legacy `POST /api/bids`（一次性创建并立即生成），以及新的持久化流程 `/api/bid-runs`（见下）。两者复用 `GET /api/jobs/{id}` 轮询、Redis JobStore 与 Celery `bid` 队列（任务名 `bidding.write_bid`；开发单 worker 同时消费两个队列）。
- 产物：`upload/bids/<doc_id>/<run_id>/` 下的 `profile.json`、`outline.json`、`bid.md`、`bid.docx`、`report.json`、`gate.json`、`compliance.json`、`consistency.json`、`sections/*.md`，按需导出 `bid.pdf`（不在 artifact allowlist）；minio 模式同前缀对象 + 私有 `private/profiles/<doc_id>/<run_id>.json`。
- LangGraph 流程（legacy/CLI）：确定性 plan → 逐节检索（Chroma/关键词兜底）→ 并行 Writer → 并行 Reviewer → 条件 Reviser → 确定性 assemble；只有施工组织设计的 11 个 narrative 子章节经过 LLM，其余表单/附件为确定性模板。
- 投标人资料：`bidder_profile.example.json` 模板；缺失字段一律 `【待填写：...】`，严禁编造公司、人员、证书、业绩、报价、日期。Writer/Reviewer 不接收完整 profile；只接收 `BidderProfile.brief()` 生成的非敏感摘要（公司名称/地址/电话、人员姓名/岗位/证书编号、业绩、财务、投标信息），身份证号、银行账号、统一社会信用代码与 `other` 原始映射不进入摘要；profile 快照只落本地 run 目录与私有对象 key，不进入 Redis/Celery 消息。摘要中的类似业绩仅用于业绩描述，不得作为本工程范围或设计依据。Writer 可提出明确标注“拟投入/拟安排”的设备、劳动力、周转材料与相对阶段方案；公司、人员、证书、业绩、报价和绝对日期仍必须来自资料。
- 评标覆盖：`report.json` 记录 55 条评标因素的 covered/partial/template/missing、占位符、缺失资料、审查问题与 token/耗时统计。
- `--max-sections N` 控制生成的 narrative 数量（0=全部 11 个）；表单骨架始终完整。Word 转换支持标题/段落/列表/表格/基本内联样式，复杂排版需人工调整。

### 持久化投标 run、可编辑大纲与单节编辑（P0/P1-A，`api/bid_runs.py`、`src/bidding/staged.py`）

- PostgreSQL 新增 4 张表：`bid_runs`、`bid_outline_sections`、`bid_section_states`、`bid_section_versions`（Alembic `0002`/`0012`）；Redis 仍是 Celery broker/实时 job 状态，PostgreSQL 是 run/大纲/章节的事实来源。
- 新流程：`POST /api/bid-runs` 只创建 run/大纲并写 profile 快照，不调用生成 LLM；`PATCH /api/bid-runs/{id}/outline` 用 revision 乐观锁修改 narrative 的标题、目标字数、requirements、content_plan、writing_focus；`POST .../generate|resume|cancel` 入队/续跑/取消；`POST .../assemble` 组装并生成 `gate.json`；`GET .../gate`；`GET .../approval|approvals`（后者含审批历史与 `current_content_sha256`）；`POST .../approve|reject`；`POST .../export?force=false` 严格门禁导出 DOCX，`POST .../export/pdf?force=false` 经 Headless LibreOffice 将同一 DOCX 转为 `bid.pdf` 并流式返回。`BidRunSummary` 暴露 `content_sha256`，前端审批面板据此提示“审批对应当前内容/内容已变化需重新审批”。`GET /api/bid-runs?doc_id=&status=`、`GET /api/bid-runs/{id}`、`GET .../outline`、`GET .../sections/{section_id}` 提供查询。
- 单节编辑：`PATCH /api/bid-runs/{id}/sections/{section_id}` 保存人工正文（仅 final narrative），必须携带 `expected_sha256`（即 `GET .../sections/{section_id}` 返回的 `content_sha256`）；内容已变化时返回 409，前端重新加载最新正文并提示重新应用修改。保存后用 `deterministic_review` 刷新 issues，`final_source="manual"`，run 退回 `paused/edited` 并清空 gate；不调用 LLM、不静默 redact；下一次 assemble 会重新计算门禁。
- 章节版本历史：每次生成/重写/人工编辑/恢复/应用修复写入 `bid_section_versions`（内容、来源、作者、sha256、时间、大纲 revision、审查摘要；相同正文不重复写入）。每节默认保留最近 `BID_SECTION_VERSION_KEEP`（默认 50）个版本，旧版本在同一事务内清理；老 run 可用 `python -m scripts.backfill_section_versions` 补齐。`GET .../sections/{id}/versions`（列表，旧 run 首次访问会以当前正文惰性补一条）、`GET .../versions/{version_id}`（正文）、`GET .../versions/{version_id}/diff?against=`（默认与当前正文做 unified diff）、`POST .../versions/{version_id}/restore?expected_sha256=`（走人工编辑语义：清 gate/合规/一致性/配图与 `bid.pdf`，写审计，生成新的 manual 版本；哈希不匹配返回 409）。修复方案 item 带 `section_sha256`，`POST .../compliance/fixes/apply` 必须回传，防止基于过期正文应用替换。
- 版本与 PDF 失效：人工编辑、单节重写、大纲失效、应用修复/重新配图都会删除本地与远程 `bid.pdf`；PDF 导出复用已有文件时按 DOCX mtime 与 `%PDF-` 头校验，DOCX 变化后自动重新转换。`SOFFICE_PATH` 可指定 LibreOffice 可执行文件，未安装时导出返回 503 并提示安装。生产 Dockerfile 的 `api` 阶段安装 LibreOffice Writer（worker/migrate 不装）；每个 API 进程用 `Semaphore(1)` 串行转换，记录 `much_agents_pdf_exports_total`（cache/converted/unavailable/failed）与 `much_agents_pdf_export_duration_seconds`；`GET /api/health` 返回 `pdf_export_available`，前端据此禁用 PDF 按钮并提示。
- 单节重写：`POST /api/bid-runs/{id}/sections/{section_id}/regenerate` 将节状态置 `pending` 但保留旧 `final_markdown`，重置 run attempt/gate 后复用 staged 生成；成功覆盖，失败保留旧稿并标记 failed，gate 继续 block。入队同步失败时尽力把节恢复为 final。
- 章节级检查点：每完成 write/review/revise 就写 `bid_section_states`；worker 崩溃后 `resume` 只补 pending/failed/stale 章节，不重复已完成章节。DB lease + heartbeat + `bid_runs(doc_id)` 活跃唯一索引防重复生成；取消在章节边界生效。
- 内容计划在 P0 是确定性的（requirements + 评分因素 + 目标字数 + 来源页），不额外调用 LLM；Writer 收到 `【本节编写计划】` 与 `【编写重点】`。
- 大纲编辑只允许 narrative 的白名单字段；不允许增删/重排，`kind/template_key` 等不可篡改；表单/附件保持确定性模板。修改已生成章节的内容字段会把该节重置为 `pending`。
- 最小门禁：缺 selected 内容、失败章节、高危/redaction、估算页数 >150 为 block；占位符、缺资料、评分因素未覆盖为 warn。直接下载产物保持宽松，只有 `POST /export` 严格拦截；`force=true` 会记录 `forced=true`。
- profile 隐私不变：DB 只存 `profile_source/profile_sha256/profile_relpath` 与缺失字段标签；Redis/Celery payload 只有 `run_id/generation_token/section_ids/assemble/mode`；`profile.json` 只存私有对象 key，不在 artifact allowlist、MCP 输出、下载 URL 或 Redis/Celery payload 中。
- minio 模式在 create/generate/assemble/edit/regenerate/export 前 hydration parsed 与 run，写后同步产物；人工编辑/重写/大纲失效会清理远程过期报告、门禁、合规、一致性、配图与 `bid.pdf` 对象，但保留 `bid.md` 与私有 profile。
- `POST /api/bids`、CLI、旧 artifact 接口保持兼容；staged job 的 artifact 目录通过 job payload 的 `run_id` 解析（`job_id != run_id`）。
- 仍未实现：DOC（97-2003）导出、Celery beat；失败章节用现有「重试失败章节」，不做任意章节增删。

### 评审评论、指派与 SSE（P2，`api/bid_runs.py`、`api/sse.py`、迁移 `0014`）

- Alembic `0014` 新增 `bid_comments`：run 级评论，可选 `section_id` 定位章节；正文 1–4000 字；`mentions`/`assignee` 保存 `{user_id, display_name}` 快照；`status=open|resolved` 并记录解决人/时间。作者与处理人刻意不加外键（本地合成用户 `local`、用户删除后保留讨论），项目/run 仍级联删除。
- 接口：`GET /api/project-members`（当前项目成员摘要，供 @ 与指派选择）；`GET /api/bid-runs/{id}/comments`（`status`/`section_id`/`limit`）；`POST .../comments`；`PATCH .../comments/{comment_id}`（正文仅作者或 admin，可解决/重开/指派/取消指派）。读需项目只读，写需全局与项目 review。提及/处理人必须是项目成员；成员目录不可用时不带提及/指派的评论仍可创建，带提及/指派返回 503。审计只记录 id/状态/处理人等元数据，不记录正文。
- 前端 `BidCommentsPanel` 支持筛选（待处理/已解决/全部）、章节与处理人下拉、@成员多选、作者编辑正文、指派给我与取消指派；只读角色隐藏写操作。
- SSE：`GET /api/bid-runs/{id}/events` 每 1.5 秒推送快照（run 状态/阶段/百分比/门禁/内容哈希、章节计数与逐节状态、Redis/Celery job 视图、`terminal`），每 15 秒心跳，单连接最长 30 分钟。前端用 `fetch` + `ReadableStream` 消费而不是 `EventSource`，因为后者无法携带 `Authorization`；token 绝不进入 URL。`useBidRunEvents` 断线 3 秒重连，`useBidRun` 轮询仍为事实来源与兜底。响应带 `X-Accel-Buffering: no`，避免 Nginx 缓冲。
- 当前边界：无站内通知/SMTP/Webhook、评论删除/线程回复、截止时间、项目看板、编辑租约；SSE 是快照流，没有 `run_events` 追加表或 `Last-Event-ID` 续传。详见 `docs/collaboration.md`。

### 浏览器 E2E 与压测（`scripts/run_e2e.py`、`frontend/e2e/`、`scripts/load_test.py`）

- `scripts/run_e2e.py` 编排隔离测试栈：重建名称含 `e2e` 的 PostgreSQL 库（默认 `much_agents_e2e`，非 e2e 名拒绝 DROP）→ `alembic upgrade head` → `scripts/e2e_seed.py` 写入 `e2e-admin`/`e2e-viewer`、默认项目成员、`e2e-doc` 解析 fixture 与 `.e2e/sample.pdf` → 启动 API（`127.0.0.1:8021`，`AUTH_ENABLED=true`、`TASK_BACKEND=local`、`STORAGE_BACKEND=local`、无 LLM/Chroma/MinIO）→ Vite（`127.0.0.1:5174`，`E2E_API_URL` 代理到 8021）→ Playwright。所有产物在 `.e2e/`（gitignored）；`--reuse-db`、`--setup-only`、`--keep-stack` 便于调试。
- `frontend/vite.config.ts` 的代理目标支持 `E2E_API_URL`，默认仍是 `http://127.0.0.1:8001`，不影响开发/生产。`frontend/playwright.config.ts` 固定单 worker、失败保留 trace/screenshot。
- 覆盖：错误密码拒绝；管理员登录→选文档→创建大纲→评论→标记解决→登出；只读用户写操作禁用；PyMuPDF 上传解析（关闭抽取/入库）。不覆盖真实 LLM、Celery/Redis、MinIO、多项目切换与浏览器 SSE 重连。
- `scripts/load_test.py` 只读 HTTP/SSE 压测：`health/live/ready/runs/comments/search`（search 是只读 POST）与并发 SSE `snapshot` 帧统计；token/密码只从环境变量读取（`LOAD_TEST_TOKEN` 或 `LOAD_TEST_USERNAME`/`LOAD_TEST_PASSWORD`），不打印也不进 URL；输出 RPS、错误率、p50/p95/p99 与按端点统计，支持错误率/p95/SSE 失败退出码。
- CI `.github/workflows/ci.yml` 新增 `e2e` job：PostgreSQL 16 service + Chromium，运行 `scripts/run_e2e.py`，失败时上传 Playwright 报告、`test-results` 与 `.e2e/logs`。单元测试 `tests/test_load_test_script.py` 覆盖百分位、请求构造与报告汇总。详见 `docs/e2e-and-load-testing.md`。

### 站内通知、Webhook 与 SMTP（P2，`api/notifications.py`、迁移 `0015`）

- Alembic `0015` 新增 `notifications`：项目/用户/kind/title/body/资源 id/run/doc/comment/section/actor/`dedupe_key`/`read_at`；唯一约束 `(user_id, dedupe_key)` 防重复，索引 `(user_id, read_at, created_at)`、`(project_id, created_at)`，项目删除级联。`user_id` 不加外键，删除账号保留历史。
- 事件：`comment_mention`、`comment_assigned`（评论创建/改派，收件人为被提及/新处理人，排除作者）；`run_assembled`、`gate_blocked`、`approval_pending`（assemble 完成，收件人为项目 manager + reviewer）。同一评论/同一内容哈希的 run 事件按 dedupe key 去重；重复 assemble 不刷屏。
- 触发点：评论路由在审计后调用 `NotificationService.notify_comment`；`BidRunService.assemble` 在写入 assembled/gate 后调用 `notify_run_assembled`，API 与 Celery worker 的 inline assembler 都注入 notifier。通知失败只 warning，不影响主流程。
- 出站：`NotificationDispatcher` 在 API event loop 中用 `asyncio.to_thread`、在 worker 中同步发送；Webhook `POST` JSON，可带 `X-Much-Agents-Signature: sha256=HMAC-SHA256(secret, raw_body)`，载荷只含事件/id/门禁/操作人，不含评论正文、profile、文档文本、token；SMTP 用全局 `NOTIFY_EMAIL_TO`（用户表暂无 email 字段），正文只含元数据。超时默认 5 秒，无重试/DLQ。
- 接口：`GET /api/notifications`、`GET /api/notifications/unread`、`POST /api/notifications/{id}/read`、`POST /api/notifications/read-all`，全部挂项目只读守卫且只操作本人通知。前端 Header 通知铃铛 25 秒轮询 + 聚焦刷新，点击带 `run_id` 的通知直接打开标书 run。
- 边界：无通知偏好/用户级邮箱/资质到期提醒/项目看板；未对 Webhook 域名做白名单校验。详见 `docs/notifications.md`。

### 合规检查、一致性台账与修复方案（P1-B，`src/bidding/compliance.py`、`consistency.py`、`fix_plan.py`）

- `assemble` 在组装后额外生成 `compliance.json`（21 项独立检查）与 `consistency.json`（跨章节事实台账），并把状态/summary 写入 `gate.json` 与 `report.json`；两文件也是 run 目录产物，可直接在 artifact 面板预览/下载。
- 21 项检查固定顺序：公司/项目/招标人/标段/工期/质量/有效期/截止时间/开竣工日期/联系方式/信用代码/法人/授权代理人/项目经理/证书/人员/业绩/财务/保证金/资格/章节完整性/评分覆盖。缺失资料和占位符是 warn，真实矛盾（如公司名、工期不一致）与失败章节是 block。
- 一致性台账覆盖 21 类事实，逐项记录 canonical 值、每处 occurrence（章节、引用、exact/conflict）与 `consistent|conflict|missing|unverified|not_found`；关键事实冲突使台账为 `conflict`，缺失/未核实为 `warning`。
- 门禁消费合规结果：任一 block 级检查失败 → gate 新增 `compliance_block` block；warn 级失败 → `compliance_warnings` warn。直接下载产物仍宽松，只有 `POST /export` 严格拦截。
- 新增接口：`GET /api/bid-runs/{id}/compliance`、`POST .../compliance/check`（重新计算并落盘，不改 run 状态）、`GET .../consistency`、`POST .../compliance/fix-plan`（`check_ids` + `max_items`）、`POST .../compliance/fixes/apply`。
- 修复方案先由一致性冲突生成确定性 replace 项（找不到唯一原文则不生成）；可选 LLM 只提供候选，所有候选都会重新校验 check id、章节可编辑性、`find` 唯一、`replace` 不含未授权金额/日期/证件与占位符。LLM 失败自动回退确定性方案。
- 应用修复要求 final 可编辑 narrative、`find` 恰好出现一次；替换后走 `edit_section` 语义（`final_source="manual"`、run `paused/edited`、清空 gate 与两份报告），下一次 assemble 重新计算。大纲失效、人工编辑、单节重写都会删除过期报告。

### 需求覆盖矩阵与引用契约（`src/bidding/requirements.py`、`citations.py`）

- 组装时从 `extraction.json` 生成 `requirements.json`：评分因素 `REQ-EVAL-*`（权重/原文/来源页/映射章节/状态）与资格要求 `REQ-QUAL-*`（强制属性、对应资格审查表）；状态为 `covered|partial|missing|not_applicable`，资格表有 `【待填写】` 视为 partial。`report.json` 只写 summary（总数、加权覆盖率、强制缺失）。
- 引用契约：Writer 提示词要求金额/日期/证件号等关键事实句末标注 `[E编号]`；编号对应生成时实际提供给 Writer 的检索证据（`retrieval_snapshots.hits`）。组装时用 `citations.json` 校验编号存在、范围、页码/章节与引用证据，统计关键事实覆盖率、无效编号与未引用主张；导出前从 `bid.md`、`sections/*.md` 与 DOCX 中剥离 `[E…]` 标记，交付文件不含内部标记。
- 门禁：`requirements` 默认 warn（`BID_REQUIREMENTS_BLOCK=true` 时强制需求缺失 block）；`citations` warn（无效编号或覆盖率低于 `BID_CITATION_MIN_COVERAGE`）。21 项合规中的 `scoring_coverage` 优先消费需求矩阵。
- 接口：`GET /api/bid-runs/{id}/requirements`、`GET /api/bid-runs/{id}/citations`；前端「需求覆盖与引用契约」面板展示缺失需求、加权覆盖率、引用覆盖率与未引用关键事实。
- 人工编辑/重写/大纲失效/应用修复会删除 `requirements.json`/`citations.json`，需重新组装生成。

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
- 工具覆盖 health/文档检索/公司列表/run/大纲/章节/门禁/合规/一致性/需求覆盖/引用/job/artifact/配图查询，以及 create/patch/generate/resume/cancel/assemble/edit/regenerate/fix-plan/apply-fix/regenerate-figures 等写操作。长任务只返回 `job_id`，由调用方轮询 `get_job`。`create_bid_run` 不接受 inline profile；`read_artifact_text` 强制 allowlist 且拒绝 `profile.json`；`list_companies` 只返回 id/名称/信用代码。
- **客户端**默认关闭（`MCP_CLIENT_ENABLED=false`），只连接 `mcp_servers.json` 中声明的服务器与 `allowed_tools`；参数只能使用固定占位符（`${query}`、`${doc_id}`、`${top_k}`、`${company_name}`、`${project_name}`、`${section_id}`、`${section_title}`、`${figure_spec}`），未知占位符启动报错。LLM 不能发现或调用任意 MCP 工具。
- 支持 `stdio` 与 `streamable_http`；命令/参数/URL/headers/env 中的 `${VAR}` 从环境展开（如 `${DRAWING_TOKEN}`），缺失变量报错。真实 `mcp_servers.json` 已 gitignore，示例为 `mcp_servers.example.json`；`.mcp.json` 也已 gitignore，示例为 `.mcp.json.example`。
- `purpose=evidence|company` 的工具在每节本地检索后并发调用，结果合并为带 `source="mcp"` / `MCP/<server>/<tool>` 标记的不可信证据；外部证据预算最多为总预算 1/3，未用满回补本地。单服务器失败/超时只 warning，不阻断章节。
- `purpose=figure` 仅在 `BID_FIGURES_EXTERNAL=true` 时用于 `site_plan` / `process_flow`；接收 ImageContent/内嵌 blob/resource link/图片 URL，校验 MIME、base64、大小、Pillow 解码与尺寸，失败自动回退内置 Pillow。发送的 spec 不含 profile。
- CLI：`uv run python -m src.mcp_client list|check|call`；`check` 只读，`call` 仅允许配置内工具。详细配置、隐私边界与手工测试见 `docs/ai-figures-and-mcp.md`。

### 尚未实现（后续阶段）

- **待办**：前端 IPC（打开本地目录）、DOC（97-2003）导出、评论线程/回复/删除与截止时间/项目看板、模型网关/数据边界重构、替换 Alertmanager 占位 webhook、L1 人工复核与 L2 judge 校准数据、启用 Cosign 签名、文生图/配图审批流、MCP OAuth 与前端 MCP 配置 UI。

### 配置与数据

- `.env`（已 gitignore）由 `.env.example` 复制；关键变量：`DEEPSEEK_API_KEY`、`ZHIPU_API_KEY`、`MINERU_API_KEY`、`DATABASE_URL`、`REDIS_URL`，以及 API/Chroma/MinerU/投标参数（`ENVIRONMENT`、`API_HOST/API_PORT`、`DOCS_ENABLED`、`TRUSTED_PROXIES`、`READINESS_CACHE_SECONDS`、`UPLOAD_DIR`、`MAX_UPLOAD_MB`、`MAX_PDF_PAGES`、`API_JOB_WORKERS`、`CORS_ORIGINS`、`TASK_BACKEND`、`CELERY_BROKER_URL`、`CELERY_WORKER_POOL`、`CELERY_SOFT_TIME_LIMIT_SECONDS`、`CELERY_TIME_LIMIT_SECONDS`、`JOB_TTL_SECONDS`、`DB_AUTO_MIGRATE`、`BID_RUN_MAX_ATTEMPTS`、`BID_RUN_STALE_SECONDS`、`BIDS_DIR`、`BID_MAX_SECTIONS`、`BID_RETRIEVAL_TOP_K`、`BID_MAX_TOKENS`、`BID_MAX_REVIEW_REVISIONS`、`BID_LLM_CONCURRENCY`、`BID_USE_CHROMA`）。
- 生产就绪与运维：`READINESS_STRICT`、`METRICS_ENABLED`、`METRICS_TOKEN`、`LOG_FORMAT`、`BID_SECTION_MAX_ATTEMPTS`、`BID_REQUIRE_APPROVAL`、`BID_HEARTBEAT_SECONDS`、`DEEPSEEK_ALLOW_FALLBACK`、`LLM_PRICE_TABLE`，以及 `BACKUP_ENCRYPT`/`BACKUP_AGE_RECIPIENT`/`BACKUP_GPG_RECIPIENT`/`BACKUP_OFFSITE_TARGET`/`BACKUP_METRICS_DIR`。
- 认证：`AUTH_ENABLED`（默认 true）、`AUTH_SESSION_TTL_HOURS`、`AUTH_LOGIN_MAX_FAILURES`、`AUTH_LOCKOUT_MINUTES`、`AUTH_HASH_CONCURRENCY`、`AUTH_LOGIN_IP_MAX_FAILURES`、`AUTH_LOGIN_IP_WINDOW_SECONDS`、`TRUSTED_PROXIES`、`MCP_API_TOKEN`、`MCP_DEFAULT_PROJECT_ID`。`AUTH_ENABLED=false` 仅用于无数据库的本机开发，所有请求按本地 admin 处理。
- 对象存储：`STORAGE_BACKEND`（默认 minio）、`MINIO_ENDPOINT`、`MINIO_SECURE`、`MINIO_BUCKET`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_ROOT_USER`、`MINIO_ROOT_PASSWORD`、`MINIO_CONSOLE_PORT`（仅 start.bat）、`MINIO_TIMEOUT_SECONDS`。
- AI 配图：`BID_FIGURES_ENABLED`、`BID_FIGURES_MAX`、`BID_FIGURES_USE_LLM`、`BID_FIGURES_FONT`、`BID_FIGURES_WIDTH_PX`、`BID_FIGURES_IMAGE_WIDTH_CM`、`BID_FIGURES_PAGE_ESTIMATE`、`BID_FIGURES_EXTERNAL`。
- 章节版本：`BID_SECTION_VERSION_KEEP`（默认 50，每节保留的版本数）。
- 需求与引用：`BID_REQUIREMENTS_BLOCK`（默认 false；true 时强制需求缺失阻断导出）、`BID_CITATION_MIN_COVERAGE`（默认 0.8，低于则门禁警告）。
- PDF 导出：`SOFFICE_PATH`（可选，指定 `soffice`/`libreoffice` 可执行文件；留空自动探测 PATH 与 Windows 默认安装目录）。未安装 LibreOffice 时 `POST .../export/pdf` 返回 503。
- 通知：`NOTIFY_WEBHOOK_URL`、`NOTIFY_WEBHOOK_SECRET`、`NOTIFY_WEBHOOK_TIMEOUT_SECONDS`、`NOTIFY_EMAIL_ENABLED`、`SMTP_HOST`、`SMTP_PORT`、`SMTP_USERNAME`、`SMTP_PASSWORD`、`SMTP_FROM`、`SMTP_STARTTLS`、`SMTP_USE_SSL`、`NOTIFY_EMAIL_TO`。站内通知不需要配置；Webhook/SMTP 未配置时只写库。
- MCP 服务端：`MCP_API_BASE_URL`、`MCP_HTTP_HOST`、`MCP_HTTP_PORT`、`MCP_HTTP_TOKEN`、`MCP_API_TOKEN`（调用业务 API 的 service token）。MCP 客户端：`MCP_CLIENT_ENABLED`、`MCP_CLIENT_CONFIG`、`MCP_CLIENT_MAX_SERVERS`、`MCP_CLIENT_MAX_TOOLS`、`MCP_CLIENT_MAX_CHARS`、`MCP_CLIENT_MAX_IMAGE_BYTES`、`MCP_CLIENT_MAX_IMAGE_DIMENSION`、`MCP_CLIENT_TIMEOUT_SECONDS`。
- 生产部署：`deploy/env.production` 从 `deploy/env.production.example` 复制（真实文件已 gitignore），其中 `ENVIRONMENT=production` 触发 fail-fast；TLS 证书放在 `deploy/nginx/certs/{fullchain.pem,privkey.pem}`（已 gitignore，可用 `generate-self-signed.sh` 生成）。生产只通过 `docker compose -f docker-compose.prod.yml` 启动，详见 `docs/deployment.md`。
- 前端 http(s) 页面默认同源 `/api`，Electron `file://` 默认 `http://127.0.0.1:8001`；可在 `frontend/.env` 设置 `VITE_API_BASE_URL`，或在界面「API 连接设置」中覆盖（保存到 localStorage；留空恢复同源）。
- PostgreSQL：`DATABASE_URL` 指向 `much-agents-postgres`；`companies` 表由 Alembic `0001` 基线创建，`bid_runs`/`bid_outline_sections`/`bid_section_states` 由 `0002` 创建，`users`/`user_sessions` 由 `0003` 创建，项目/文档注册表由 `0004` 创建，`audit_events` 由 `0005` 创建，`llm_call_log`/`run_usage` 由 `0006` 创建，章节 `generation_token`/`last_heartbeat_at` 由 `0007` 增加，`bid_runs.content_sha256` 由 `0008` 增加，`bid_approvals` 由 `0009` 创建，`retrieval_snapshots` 由 `0010` 创建，`prompt_registry` 由 `0011` 创建，`bid_section_versions` 由 `0012` 创建，`outline_revision`/`review_summary` 由 `0013` 增加，`bid_comments` 由 `0014` 创建，`notifications` 由 `0015` 创建。API 启动默认执行 `alembic upgrade head`（advisory lock 防并发迁移），worker 不迁移。数据库不可用时公司/run/用户/认证接口返回 503，不影响解析与 legacy 标书任务；pytest 使用内存 fake，不连接真实数据库。已有只含 `companies` 的开发库先 `alembic stamp 0001` 再 `upgrade head`。
- LLM 默认 `LLM_PROVIDER=deepseek`、`DEEPSEEK_MODEL=deepseek-flash`；嵌入模型 `EMBEDDING_MODEL=embedding-3`（智谱）。
- 检索：`BID_USE_CHROMA=true` 且 `ZHIPU_API_KEY` 可用时启用语义；`auto` 不可用或空结果时回退关键词。`BID_RETRIEVAL_TOP_K` 只控制标书 Writer，搜索 top_k 由请求参数控制（1–30）。
- `pyproject.toml` 将阿里云 PyPI 镜像配置为默认 index；Python 固定 `>=3.12,<3.13`，由 `.python-version` 约束。
- 数据目录（均已 gitignore）：`chroma_db/` 向量库；`upload/` 是本地输入/产物目录（`upload/inbox/<job_id>/source.pdf` 为 API 上传，`upload/parsed/<doc_id>/` 为解析输出，`upload/bids/<doc_id>/<run_id>/` 为投标文件产物，配图在 `.../figures/`）——minio 模式下它是工作缓存，权威对象在 MinIO bucket；Docker 数据卷 `much-agents-minio-data` 保存 MinIO 对象；`.run/` 保存一键启动的 PID 状态（POSIX 上还有各服务日志）；`.e2e/` 保存 E2E 隔离上传/解析/产物、seed.json 与 API/Vite 日志，`frontend/playwright-report/`、`frontend/test-results/` 保存 Playwright 报告（均已 gitignore）；`backups/` 保存 `scripts/backup.sh` 输出；`deploy/nginx/certs/` 下的证书与私钥永不入库。
- MCP 配置文件：真实 `mcp_servers.json` 与 `.mcp.json` 已 gitignore；提交的模板为 `mcp_servers.example.json`、`.mcp.json.example`。

## 注意

- Windows 环境开发；长任务（解析/抽取/编写）不要阻塞 HTTP 请求，走 Celery 或轮询任务模型。
- API 默认开启认证；新增 `/api/*` 业务路由必须挂 `require_user` 或更严格的角色依赖，只有 health/live/ready/login 可以公开。不要用「所有 POST 都算写」的中间件，因为 `/api/search` 是需认证的只读 POST。`AUTH_ENABLED=false` 只能用于本机开发。
- 新增业务路由还必须挂项目守卫（`require_project_*` / `require_run_*` / `require_document_*`），并带 `X-Project-Id`；资源查询必须按 `project_id` 过滤，跨项目按 404 处理。写操作同时满足全局角色与项目角色。敏感写操作要写审计（`required=True`）。
- `ENVIRONMENT=production` 会在启动时拒绝不安全配置；不要绕过 fail-fast。生产部署只用 `docker-compose.prod.yml`，不要直接用 `start.bat`。
- 不要提交 `.env`、`deploy/env.production`、`backups/`、`deploy/nginx/certs/*.pem|key|crt`、`chroma_db/`、`upload/`、`node_modules/`、`dist/`、`release/`、真实的 `mcp_servers.json` 或 `.mcp.json`。
- `profile.json` 永不进入 artifact allowlist、MCP 工具输出、Redis/Celery payload、审计 detail 或任何下载 URL；只存 MinIO 私有 key `private/profiles/<doc_id>/<run_id>.json`。外部证据默认不可信，外部绘图默认关闭。认证 token、密码也不得进入 URL、日志、审计 detail 或 Redis/Celery payload。
- 项目以 **AGPL-3.0-or-later** 发布（根目录 `LICENSE` / `NOTICE` / `THIRD_PARTY_NOTICES.md`）。新增或升级依赖必须与 AGPL-3.0 兼容，并更新 `THIRD_PARTY_NOTICES.md` 的组件表、版权行与所需许可证全文；PyMuPDF 为 AGPL-3.0 或 Artifex 商业许可双许可，闭源使用需购买商业许可。
- 不要移除前端源码链接（`SourceLink`、`VITE_SOURCE_URL`）或 Docker/CI 中的许可证文件断言。修改版部署必须让 `VITE_SOURCE_URL` 指向自己发布的对应源码；这是 AGPL §13 的网络源码提供义务，不是可选装饰。
- 许可证与源码链接属于公开分发面：不要把 `profile.json`、真实招标/投标数据、账号口令、token 或私钥带入 `LICENSE` / `NOTICE` / `THIRD_PARTY_NOTICES.md` / README / 前端构建产物。
- pip-audit 的例外只允许按 GHSA ID 精确豁免，并记录在 `docs/dependency-security.md`；禁止包级忽略或关闭审计。例外失效条件（上游修复、暴露 Chroma、改用嵌入式服务端等）出现时必须重新评审。
