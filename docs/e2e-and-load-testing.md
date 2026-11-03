# 前端 E2E 与压测

> 状态：已实现（2026-11-02）。浏览器 E2E 使用 Playwright + 隔离 PostgreSQL；压测脚本 `scripts/load_test.py` 只读，覆盖 HTTP 与 SSE。CI 的 `e2e` job 会同时运行压测并执行 p95 门禁。

## 1. 浏览器 E2E

### 1.1 架构

`scripts/run_e2e.py` 负责准备并回收整套测试栈：

```text
run_e2e.py
  ├─ 重建独立数据库 <name containing "e2e">（默认 much_agents_e2e）
  ├─ alembic upgrade head
  ├─ scripts/e2e_seed.py
  │    ├─ e2e-admin / e2e-viewer（测试专用弱口令，仅存在于 e2e 库）
  │    ├─ 加入默认项目（manager / viewer）
  │    ├─ 写入 parsed fixture e2e-doc
  │    └─ 生成 .e2e/sample.pdf
  ├─ API 127.0.0.1:8021（AUTH_ENABLED=true, TASK_BACKEND=local, STORAGE_BACKEND=local）
  ├─ Vite 127.0.0.1:5174（proxy → 8021）
  └─ Playwright（frontend/e2e）
```

- API 不连接 Redis/MinIO/Chroma，不调用 LLM；上传测试只使用 PyMuPDF 本地解析，并关闭抽取/入库。
- 数据库只在名称包含 `e2e` 时才会被 `DROP ... WITH (FORCE)`；防止误删开发库。
- 默认每次运行重建库；`--reuse-db` 仅迁移并重新 seed。
- 所有 E2E 产物在 `.e2e/`（gitignored），Playwright 报告在 `frontend/playwright-report/`。

### 1.2 覆盖范围

`frontend/e2e/smoke.spec.ts`：

1. 错误密码被拒绝，不进入工作台。
2. 管理员登录 → 选择已解析文档 → 创建大纲 → 打开「评审评论与指派」→ 发表评论 → 标记解决 → 退出登录。
3. 管理员回复评论 → 上传附件 → 删除评论 → 在「已删除」中恢复 → 退出登录。
4. 管理员打开「项目看板」→ 负责人负载与最近待处理评论可见。
5. 只读用户登录 → 「编写标书」显示只读提示，创建大纲按钮禁用。
6. 管理员打开公司「资质」弹窗，确认种子证书记录。
7. 管理员打开「运营看板」。
8. 管理员打开「评测控制台」→ 空评测集显示「暂无用例」与「运行 L1 评测」按钮。
9. 只读用户看不到「评测控制台」导航项。
10. 管理员更新通知偏好。
11. 管理员上传 PDF（PyMuPDF、关闭抽取/入库）→ 本地任务轮询 → 「解析完成」。

尚未覆盖：真实 LLM 生成、Celery/Redis 队列、MinIO、多项目切换、附件下载与截止时间交互、SSE 浏览器断线重连（API 层由单测和压测覆盖）。

### 1.3 本地运行

前置：Docker Desktop 已启动 `much-agents-postgres`（5433），Node 依赖与 Playwright 浏览器已安装。

```bash
cd frontend
npm install
npx playwright install chromium

cd ..
uv run python scripts/run_e2e.py
```

常用选项：

```bash
# 复用现有 e2e 库，只迁移+seed
uv run python scripts/run_e2e.py --reuse-db

# 只准备环境并保留 API/Vite，便于手工调试
uv run python scripts/run_e2e.py --setup-only --keep-stack

# 指定端口或数据库
uv run python scripts/run_e2e.py --api-port 8022 --web-port 5175 \
  --database-url postgresql+asyncpg://much_agents:much_agents_dev@localhost:5433/much_agents_e2e2

# 把额外参数传给 Playwright
uv run python scripts/run_e2e.py -- --reporter=line --grep "评论"
```

若只运行已经准备好的栈：

```bash
cd frontend
E2E_BASE_URL=http://127.0.0.1:5174 npm run test:e2e
```

`--setup-only --keep-stack` 会打印 API/Vite 的 PID 与日志路径；Windows 用 `taskkill /PID <pid> /T /F` 停止。

### 1.4 CI

`.github/workflows/ci.yml` 的 `e2e` job 使用 PostgreSQL 16 service，安装 Chromium 后运行 `scripts/run_e2e.py`；失败时上传 Playwright 报告、test-results 与 `.e2e/logs`。

## 2. 压测

`scripts/load_test.py` 是只读探测工具：

- HTTP：`health`、`live`、`ready`、`runs`、`comments`、只读 `search`（POST 但无副作用）。
- SSE：并发打开 `/api/bid-runs/{id}/events`，统计连接失败与 `snapshot` 帧数。
- 统计：请求数、RPS、错误率、p50/p95/p99/max、按端点拆分、SSE 连接/帧数。
- 失败判定：错误率超过 `--max-error-rate`（默认 1%）、任一 SSE 连接失败、或 p95 超过 `--max-p95-ms`（默认不启用）；失败时 stderr 会列出具体触发条件。

### 2.1 认证

token 与密码只从环境变量读取，不出现在命令行：

```bash
# 方式一：已有 Bearer token
LOAD_TEST_TOKEN=... LOAD_TEST_PROJECT_ID=<project_uuid> \
  uv run python -m scripts.load_test --base-url http://127.0.0.1:8001 \
  --endpoint health --endpoint runs

# 方式二：用账号密码登录换取 token（脚本内部调用 /api/auth/login）
LOAD_TEST_USERNAME=admin LOAD_TEST_PASSWORD='...' \
LOAD_TEST_PROJECT_ID=<project_uuid> \
  uv run python -m scripts.load_test --endpoint runs --endpoint comments \
  --run-id <run_id> --sse-connections 8 --sse-seconds 10
```

`AUTH_ENABLED=false` 的本地 API 不需要 token；业务端点仍会使用默认项目。

### 2.2 示例

```bash
LOAD_TEST_USERNAME=e2e-admin LOAD_TEST_PASSWORD=e2e-admin-password-123 \
LOAD_TEST_PROJECT_ID=00000000-0000-0000-0000-000000000001 \
LOAD_TEST_RUN_ID=<run_id> \
  uv run python -m scripts.load_test \
  --base-url http://127.0.0.1:8021 \
  --endpoint health --endpoint runs --endpoint comments \
  --duration 15 --concurrency 12 \
  --sse-connections 8 --sse-seconds 10 \
  --max-p95-ms 1000
```

在本地 E2E 栈上的冒烟结果（仅作参考）：6 并发、5 秒，health/runs/comments 共 224 请求、0 失败、44 RPS；4 条 SSE 连接全部成功，5 秒内收到 16 帧。

### 2.3 CI 门禁

`scripts/run_e2e.py --load-test` 会先通过 API 创建一个空的 bid run（用于 `comments` 与 SSE），再用 `scripts.load_test` 压测 `health/live/ready/runs/comments/search` 与 4 条 SSE 连接：

```bash
uv run python scripts/run_e2e.py \
  --load-test \
  --load-duration 10 --load-concurrency 8 \
  --load-max-p95-ms 2000 \
  --load-sse-connections 4 --load-sse-seconds 5
```

- 凭据通过子进程环境变量传入（`LOAD_TEST_USERNAME/PASSWORD/PROJECT_ID/RUN_ID/DOC_ID`），不出现在命令行。
- 任一请求错误率超过 1%、SSE 连接失败或整体 p95 超过 `--load-max-p95-ms`（默认 1500 ms；CI 使用 2000 ms）都会让 step 失败。
- `.github/workflows/ci.yml` 的 `e2e` job 已带上述参数运行；Playwright 与压测共用同一套隔离栈。

### 2.4 注意

- 生成/解析/导出会消耗 LLM 配额或 CPU，不在压测范围内；只测读路径与 SSE。
- 生产压测应通过内网入口并配合 Prometheus 指标；不要把真实 token 写进脚本、URL 或日志。
- 默认参数较保守；提高并发前先确认 API 进程数、数据库连接池与 Nginx 限流配置。
