# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 固定任务

- 每次修改完成后，都应创建一个对应的 Git commit，便于后续追踪和回滚。

## 项目概述

多 Agent 标书编写系统（Much Agents）：解析招标文件 PDF → 结构化抽取 → 入 Chroma 向量库 → 由 LangGraph 多 Agent 生成投标文件（Markdown + Word），前端为 React + Electron 桌面端。技术栈参考 [ZhiBiao](https://github.com/Foulereee/ZhiBiao)。

**当前状态**：已实现文档解析管线 `src/parsing/` 与测试 `tests/`（MinerU/PyMuPDF 解析、结构化抽取、分块入库）。FastAPI、Celery、多 Agent 投标文件编写、PostgreSQL 持久化仍未实现；完整功能规划、需求决策和实现顺序见 `AI编程日志-标书编写.md`，该文件是项目路线图的权威来源。

## 常用命令

### 后端（项目根目录，uv 管理，Python 3.12）

```bash
uv sync                                      # 安装/同步依赖
uv run chroma run --path ./chroma_db         # 启动 Chroma，监听 8000
uv run pytest                                # 运行全部测试（全部 mock 网络）
uv run pytest tests/test_toc.py::test_hierarchy_and_end_pages   # 单个测试
uv run python -m src.parsing upload/6055594244948.pdf           # 解析 + 抽取 + 入库
uv run python -m src.parsing <pdf> --parser pymupdf --no-extract --no-ingest  # 离线仅解析
uv run python -m src.cli write <doc_id>      # CLI 运行多 Agent 编写（规划中）
```

FastAPI 服务规划监听 `API_PORT=8001`；Celery worker 在 Windows 上默认用 `solo` 池。

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

### 本地依赖服务

- **Redis**：本机无原生 `redis-server.exe`，使用 Docker 容器 `much-agents-redis`（映射 6379，`--restart unless-stopped`）：`docker start much-agents-redis`。连接串 `redis://localhost:6379/0`。
- **PostgreSQL**：`postgresql+asyncpg://postgres:123456@localhost:5432/bidding`（公司信息入库为待办）。
- **Chroma**：`uv run chroma run --path ./chroma_db`，向量数据落在 `chroma_db/`（已 gitignore）。
- **MinerU**：云端解析，需要 `MINERU_API_KEY`（有效期 90 天）；单文件页数上限 200，超过需先分片。

## 架构要点

### 前端 / Electron

- Electron 主进程是 CommonJS：`frontend/electron/main.cjs`。带 `--dev` 时加载 `http://localhost:5173`，否则通过 `file://` 加载 `dist/index.html`。
- `vite.config.ts` 设置 `base: './'`，因为 Electron 生产环境用 `file://` 加载，资源路径必须相对——不要改成绝对路径。
- 渲染层：React 19 + TypeScript + TailwindCSS 4（通过 `@tailwindcss/vite` 插件，无 `tailwind.config.*`，`src/index.css` 仅 `@import "tailwindcss"`）。
- 目前没有 preload/IPC；`contextIsolation: true`、`nodeIntegration: false`。规划中的“在资源管理器中打开产物”需要新增 IPC。
- 界面规划为左侧步骤导航 + 工作区：① 上传解析 ② 编写标书 ③ 文档库 ④ 历史产物；后端 base URL 为 `http://localhost:8001`。

### 文档解析管线（已实现，`src/parsing/`）

入口 `uv run python -m src.parsing <pdf>`，`parse_document()` 可被后续 API 复用。阶段顺序：解析 → 抽取 → 分块 → 入库；产物在 `upload/parsed/<doc_id>/`（`full.md`、`content_list.json`、`toc.json`、`meta.json`、`extraction.json`、`chunks.jsonl`、`ingest.json`）。

1. **解析**：`parsers/mineru/` 走 v4 批量 API（签名 URL 上传 → 轮询 → 下载 zip），`parsers/pymupdf_parser.py` 兜底。MinerU 实际限制 200 页/文件（文档写 600，接口会拒绝），超过时用 PyMuPDF 按平衡页数分片、同批上传后按页码偏移合并。
2. **结构对齐**：以 `content_list` 的 `page_idx` + PDF 书签为准；`toc.py` 的 `end_page` 按“下一条同级或更高级目录”计算，避免章级范围被小节截断。页脚哈希水印和页码在 `parsers/footer.py` 清理，不要用 redaction。
3. **抽取**：`extraction/` 按章节裁剪成 ≤24k 字的 payload，逐段调用 DeepSeek（OpenAI 兼容，JSON 模式），Pydantic 校验；失败或缺少 key 时降级到正则抽取，`method` 会记录 `llm` / `llm+regex` / `regex_fallback`。
4. **分块与入库**：`chunking.py` 以 TOC section 为边界，默认 700 字/120 重叠，id 形如 `<doc_id>:<section_id>:<ordinal>`；`ingest.py` 用智谱 `embedding-3` 生成向量，`HttpClient` 写入 Chroma 集合 `bidding_documents`，upsert + 清理旧 id。
5. **缓存**：`meta.json` 记录源文件 sha256；解析/抽取/入库均有缓存，`--force` 强制重跑。退出码：0 成功、2 输入错误、3 解析失败、4 抽取为空、5 入库失败、6 缺少 key 跳过。

### 尚未实现（依据 AI 编程日志的规划）

- **API**：FastAPI；`POST /api/parse` 返回 202 + `job_id`，`GET /api/jobs/{id}` 轮询；`POST /api/bids` 接收 profile JSON。`TASK_BACKEND=celery` 默认，Redis 不可用返回 503，保留内存后端。
- **多 Agent 编写**：LangGraph，输入 `bidder_profile.json`；缺失字段生成【待填写】，绝不编造；输出 Markdown + docx。
- **待办**：PostgreSQL 公司信息、检索接口、MinIO、Nginx。

### 配置与数据

- `.env`（已 gitignore）由 `.env.example` 复制；关键变量：`DEEPSEEK_API_KEY`、`ZHIPU_API_KEY`、`MINERU_API_KEY`、`DATABASE_URL`、`REDIS_URL`，以及 API/Chroma/MinerU 参数。
- LLM 默认 `LLM_PROVIDER=deepseek`、`DEEPSEEK_MODEL=deepseek-flash`；嵌入模型 `EMBEDDING_MODEL=embedding-3`（智谱）。
- `pyproject.toml` 将阿里云 PyPI 镜像配置为默认 index；Python 固定 `>=3.12,<3.13`，由 `.python-version` 约束。
- 数据目录（均已 gitignore）：`chroma_db/` 向量库、`upload/` 解析与标书产物（规划 `upload/parsed/`、`upload/bids/`）；`招标文件pdf/` 是示例招标文件。

## 注意

- Windows 环境开发；长任务（解析/抽取/编写）不要阻塞 HTTP 请求，走 Celery 或轮询任务模型。
- 不要提交 `.env`、`chroma_db/`、`upload/`、`node_modules/`、`dist/`、`release/`。
