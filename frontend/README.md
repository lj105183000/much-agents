# Much Agents 前端工作台

基于 React 19、TypeScript、Vite 8、TailwindCSS 4 与 Electron 45 的本地桌面界面，连接项目根目录的 FastAPI 后端。

## 功能

- **上传解析**：拖入招标 PDF，选择解析器与抽取/入库选项，查看解析进度、统计与产物。
- **编写标书**：选择已解析文档与公司资料，先创建持久化 run 并编辑大纲（标题、目标字数、编写要点、写作重点），再生成/续跑/取消章节；完成后自动组装并显示导出前门禁，可严格导出 DOCX。
- **公司库**：完整投标人资料保存到 PostgreSQL；新建/编辑/删除/搜索公司，编写标书时可选择公司并自动载入 profile。
- **文档检索**：在已解析招标文件 chunks 中检索原文片段，展示章节/页码/相关度；语义优先，Chroma/智谱不可用时自动回退关键词。
- **文档库**：扫描后端 `upload/parsed`，查看解析产物、历史投标文件和 bid run 产物。
- **任务历史**：localStorage 记录本机提交的任务，支持筛选、继续轮询、查看详情和删除。
- **产物预览/下载**：通过后端 allowlist artifact 接口访问；`profile.json` 不会暴露。
- **深色/浅色主题**：右上角按钮切换，偏好保存在 localStorage，首屏自动恢复。

## 启动

先启动后端：

```bash
# 项目根目录
uv run alembic upgrade head
uv run uvicorn api.main:app --host 0.0.0.0 --port 8001
# 或无需 Redis/worker 的本地模式
TASK_BACKEND=local uv run uvicorn api.main:app --host 0.0.0.0 --port 8001
```

API 启动默认会执行 Alembic 迁移（`DB_AUTO_MIGRATE=false` 可关闭）。编写标书的 run、大纲和章节状态保存在 PostgreSQL；Celery worker 必须与 API 共享同一数据库与 `.env`。

公司库需要 PostgreSQL（Docker 容器 `much-agents-postgres`，端口 5433）：

```bash
docker start much-agents-postgres
```

再启动前端：

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173
npm run electron:dev   # Vite + Electron
npm run build          # tsc -b && vite build
npm run lint           # oxlint
npm run electron       # build 后打开 Electron 生产构建
npm run dist           # electron-builder 打包
```

Nginx（Docker，容器 `much-agents-nginx`）托管已构建的 `dist/` 并反代 `/api`：

```bash
docker start much-agents-nginx
# 浏览器访问 http://127.0.0.1:8080
```

修改前端后执行 `npm run build` 即可，Nginx 挂载的是 `frontend/dist`，无需重启容器。Nginx 配置在根目录 `deploy/nginx.conf`。

## API 地址

- 通过 Nginx（8080）或 Vite（5173）访问时，默认使用**同源 `/api`**；Vite 已在 `vite.config.ts` 配置 `/api` 代理。
- Electron 通过 `file://` 加载时默认使用 `http://127.0.0.1:8001`。
- 可设置绝对地址覆盖：

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8001
```

也可以在界面右上角「API 连接设置」中覆盖（保存到 localStorage）；留空恢复同源。

## 注意

- Celery 模式需要 Redis 与 `parse` 队列 worker；没有 worker 时任务会停在排队中。可以使用 `TASK_BACKEND=local` 调试。
- 生成标书是草稿：报价、证件、盖章、扫描件等仍需人工补全；缺失资料会以 `【待填写：...】` 标记。
- 创建 run 只生成大纲，不调用生成 LLM；生成时每完成一节写入 PostgreSQL，worker 中断后可在界面点击「继续生成」补跑未完成章节。
- 门禁只阻止「导出 DOCX」；`bid.md`、`bid.docx`、`report.json`、`gate.json` 等仍可在产物面板直接下载。强制导出会记录 `forced=true`。
- 任务历史仍只保存在当前浏览器/Electron profile；服务端 run 列表接口已提供，前端历史迁移留待后续。文档库来自后端磁盘扫描。
- 前端没有 preload/IPC；本地文件只通过后端 artifact 接口访问。
