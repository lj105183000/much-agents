# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 固定任务

- 每次修改完成后，都应创建一个对应的 Git commit，便于后续追踪和回滚。

## 项目概述

多 Agent 标书编写系统（Much Agents）：解析招标文件 PDF → 结构化抽取 → 入 Chroma 向量库 → 由 LangGraph 多 Agent 生成投标文件（Markdown + Word），前端为 React + Electron 桌面端。技术栈参考 [ZhiBiao](https://github.com/Foulereee/ZhiBiao)。

**当前状态**：仓库只有工程脚手架（后端依赖 + 前端/Electron 骨架）。后端 `src/`、`api/`、`tests/` 尚未创建；完整功能规划、需求决策和实现顺序见 `AI编程日志-标书编写.md`，该文件是项目路线图的权威来源。

## 常用命令

### 后端（项目根目录，uv 管理，Python 3.12）

```bash
uv sync                                      # 安装/同步依赖
uv run chroma run --path ./chroma_db         # 启动 Chroma，监听 8000
uv run pytest                                # 运行全部测试（规划中）
uv run pytest tests/test_parsing.py::test_toc_parse   # 单个测试（规划中）
uv run python -m src.parsing <pdf路径>        # CLI 解析单份招标文件（规划中）
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

### 后端（依据 AI 编程日志的规划）

1. **解析**（`src.parsing`）：MinerU 云端 vlm 为主，PyMuPDF 兜底；超 200 页自动分片后合并 Markdown，并按原 TOC 对齐章节；产物落到 `upload/parsed/`。
2. **抽取**：重点抽取评标办法的评审因素/分值/评分标准、投标人须知前附表、资格审查条件、投标文件格式清单、关键时间节点。
3. **入库**：Markdown 分块后走智谱 `embedding-3` 嵌入，写入 Chroma，供后续 RAG 检索。
4. **API**：FastAPI；`POST /api/parse` 返回 202 + `job_id`，`GET /api/jobs/{id}` 轮询状态；`POST /api/bids` 直接接收 profile JSON。`TASK_BACKEND=celery` 为默认，Redis 不可用时返回 503；保留内存后端作 local 模式供测试使用。
5. **多 Agent 编写**：LangGraph 流程，输入 `bidder_profile.json`；缺失字段一律生成【待填写】占位符，**绝不编造事实**；输出 Markdown 并用 python-docx 导出 .docx。
6. **待办**：PostgreSQL 持久化公司信息、检索功能、MinIO 对象存储、Nginx 生产部署。

### 配置与数据

- `.env`（已 gitignore）由 `.env.example` 复制；关键变量：`DEEPSEEK_API_KEY`、`ZHIPU_API_KEY`、`MINERU_API_KEY`、`DATABASE_URL`、`REDIS_URL`，以及 API/Chroma/MinerU 参数。
- LLM 默认 `LLM_PROVIDER=deepseek`、`DEEPSEEK_MODEL=deepseek-flash`；嵌入模型 `EMBEDDING_MODEL=embedding-3`（智谱）。
- `pyproject.toml` 将阿里云 PyPI 镜像配置为默认 index；Python 固定 `>=3.12,<3.13`，由 `.python-version` 约束。
- 数据目录（均已 gitignore）：`chroma_db/` 向量库、`upload/` 解析与标书产物（规划 `upload/parsed/`、`upload/bids/`）；`招标文件pdf/` 是示例招标文件。

## 注意

- Windows 环境开发；长任务（解析/抽取/编写）不要阻塞 HTTP 请求，走 Celery 或轮询任务模型。
- 不要提交 `.env`、`chroma_db/`、`upload/`、`node_modules/`、`dist/`、`release/`。
