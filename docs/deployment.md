# 生产部署与运维（P0-D）

> 状态：已实现（2026-10-24）。目标形态是**单台 Linux 主机 + Docker Compose + Nginx TLS** 的内网小团队部署。Windows `start.bat` 仍用于本机开发，不是生产方案。

## 1. 拓扑

```text
浏览器 / Electron
        │  HTTPS 443
   ┌────▼────┐
   │  nginx  │  唯一发布端口的容器
   └────┬────┘
        │ http://api:8001（Compose 内部网络）
   ┌────▼────┐    ┌──────────────┐
   │   api   │───▶│   postgres   │
   └────┬────┘    └──────────────┘
        │         ┌──────────────┐
        ├────────▶│    redis     │◀──── worker-parse / worker-bid
        │         └──────────────┘
        │         ┌──────────────┐
        ├────────▶│    minio     │
        │         └──────────────┘
        │         ┌──────────────┐
        └────────▶│    chroma    │
                  └──────────────┘
```

| 服务 | 作用 | 端口 |
|---|---|---|
| `nginx` | TLS 终止、静态 SPA、`/api/` 反代、登录限流 | 主机 80/443 |
| `api` | FastAPI，非 root、只读根文件系统 | 仅内部 8001 |
| `migrate` | 一次性 `alembic upgrade head`，成功后才启动 API/worker | 无 |
| `worker-parse` | Celery `parse` 队列，并发 1 | 无 |
| `worker-bid` | Celery `bid` 队列，并发 1 | 无 |
| `postgres` | PostgreSQL 16 | 仅内部 5432 |
| `redis` | Redis 7，密码 + AOF | 仅内部 6379 |
| `minio` | 对象存储，bucket 由 `createbuckets` 创建 | 仅内部 9000 |
| `chroma` | 向量库 | 仅内部 8000 |

所有业务容器使用 `restart: unless-stopped`、`no-new-privileges`、日志轮转；API/worker 只读根文件系统，`/tmp` 为 tmpfs，`/app/upload` 为共享命名卷。

## 2. 前置条件

- Linux 主机，Docker Engine 24+，`docker compose` 插件（独立 `docker-compose` 也可，脚本会自动识别）。
- 主机 80/443 可用；内网 DNS 或 hosts 指向部署机。
- `openssl`（生成自签证书）与 `bash`。
- 至少 4 GB 内存、20 GB 可用磁盘（含镜像、Postgres、MinIO、Chroma）。
- 部署机可访问模型 API（DeepSeek/智谱/MinerU）；完全离线时这些功能会降级，见第 11 节。

## 3. 首次部署

### 3.1 准备配置

```bash
git clone <repo> much-agents && cd much-agents
cp deploy/env.production.example deploy/env.production
chmod 600 deploy/env.production
```

编辑 `deploy/env.production`，至少替换所有 `CHANGE_ME`：

| 变量 | 要求 |
|---|---|
| `POSTGRES_PASSWORD` / `DATABASE_URL` | 强密码，两处一致；密码只用字母数字，避免 URL 转义 |
| `REDIS_PASSWORD` / `REDIS_URL` / `CELERY_BROKER_URL` | 强密码，三处一致 |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` / `MINIO_ROOT_*` | 强随机值，两两一致 |
| `MCP_API_TOKEN` | `openssl rand -hex 32`，至少 16 字符 |
| `CORS_ORIGINS` | 实际访问地址，如 `https://much-agents.internal`；不能为 `*` |
| `DEEPSEEK_API_KEY` / `ZHIPU_API_KEY` / `MINERU_API_KEY` | 按需填写；缺失只影响对应能力 |

生产启动会执行 fail-fast，以下任一条件都会导致 API 拒绝启动：

- `AUTH_ENABLED=false`、`CORS_ORIGINS=*`、`DOCS_ENABLED=true`、`DB_AUTO_MIGRATE=true`。
- `TASK_BACKEND` 不是 `celery` 或 `STORAGE_BACKEND` 不是 `minio`。
- 数据库/Redis/MinIO 仍使用开发默认凭据或 `CHANGE_ME` 占位符，或 `MCP_API_TOKEN` 太短、仍是占位符。
- `MAX_PDF_PAGES > 200`，或认证/Celery 时间参数超出合理范围。

`deploy/env.production` 已被 gitignore，**不要提交**。请另存一份到加密的密钥管理系统；备份脚本不会复制它。

### 3.2 TLS 证书

自签（内网测试）：

```bash
./deploy/nginx/generate-self-signed.sh much-agents.internal
```

脚本生成 `deploy/nginx/certs/fullchain.pem` 与 `privkey.pem`，拒绝覆盖已有文件。自签证书需要导入客户端信任库；正式环境建议使用企业内部 CA 或公网证书，同样命名为这两个文件。

证书与私钥已被 gitignore；`scripts/backup.sh` 不包含它们，需要单独备份。

### 3.3 启动

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

启动顺序由 Compose 控制：Postgres/Redis/MinIO/Chroma 健康 → `migrate` 与 `createbuckets` 完成 → API/worker 启动 → Nginx 等待 API 健康。`createbuckets` 失败时 API/worker 不会启动，避免首次上传遇到 bucket 不存在。

镜像内默认使用 `deb.debian.org`、`pypi.org` 与 `registry.npmjs.org`。受限网络（如国内内网）可在构建时改用镜像源；这些构建参数不会进入运行时配置：

```bash
APT_MIRROR=mirrors.aliyun.com \
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
NPM_REGISTRY=https://registry.npmmirror.com \
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

验证：

```bash
curl -k https://127.0.0.1/healthz             # ok
curl -k https://127.0.0.1/api/live            # 200 {"status":"ok"}
curl -k https://127.0.0.1/api/ready           # 200，依赖全部可用
curl -kI https://127.0.0.1/docs               # 404
curl -I http://127.0.0.1/                     # 301 到 HTTPS
```

`/api/ready` 只返回组件布尔值，失败时 503；结果缓存 5 秒（`READINESS_CACHE_SECONDS`）。`/api/health` 仍保留为前端使用的详细信息接口。

### 3.4 创建管理员与项目

```bash
docker compose -f docker-compose.prod.yml exec api \
  python -m api.users create-admin --username admin --display-name 管理员
```

密码交互输入。已有数据库升级时不要重复创建。

创建项目并分配成员（也可在管理员前端完成）：

```bash
docker compose -f docker-compose.prod.yml exec api \
  python -m api.projects create --name "某某项目" --slug demo-2026
docker compose -f docker-compose.prod.yml exec api \
  python -m api.projects add-member --project-id <uuid> --username zhangsan --role writer
```

**首次启用项目 ACL 前**，把已有解析文档注册到默认项目：

```bash
docker compose -f docker-compose.prod.yml exec api \
  python -m api.projects backfill-documents
```

从旧版本升级时，迁移 `0004` 已回填有 run 的文档；`backfill-documents` 补齐只有解析产物、没有 run 的文档。

## 4. 升级

```bash
# 1. 备份（见第 6 节）
scripts/backup.sh

# 2. 获取新代码 / 新镜像
git pull

# 3. 先重建并执行迁移，再滚动重启
#    `run` 会复用本地已有的 migrate 镜像，不随代码自动更新。
docker compose -f docker-compose.prod.yml build migrate
docker compose -f docker-compose.prod.yml run --rm migrate
docker compose -f docker-compose.prod.yml up -d --build api worker-parse worker-bid nginx

# 4. 验证
curl -k https://127.0.0.1/api/ready
docker compose -f docker-compose.prod.yml ps
```

`migrate` 服务是幂等的；API 启动时不再自动迁移（`DB_AUTO_MIGRATE=false`）。迁移失败时不要启动新版 API。

回滚：

1. 优先回退到上一版代码/镜像并重启；Alembic 迁移通常向后兼容。
2. 如必须降级数据库，评估 `alembic downgrade -1` 的破坏性（`0004` 在多项目出现同名 `doc_id` 时会拒绝降级）。
3. 最稳妥的是用升级前备份恢复（第 7 节），并重新执行 `/api/ready` 与登录验证。

## 5. 健康检查与故障转移

| 检查 | 用途 |
|---|---|
| `GET /healthz`（Nginx） | 容器存活 |
| `GET /api/live` | API 进程存活，始终 200 |
| `GET /api/ready` | Redis/DB/MinIO 依赖检查，失败 503 |
| `worker-parse` / `worker-bid` healthcheck | `scripts/worker_healthcheck.py` 通过 Celery inspect 确认队列有消费者 |

Celery 配置为 `task_acks_late=True`、`task_reject_on_worker_lost=True`、`prefetch_multiplier=1`，并带可配置的 soft/hard 时间上限（默认 3300/3600 秒）。解析任务使用原子 claim，投标 run 使用数据库 lease 与章节检查点，因此 worker 崩溃后任务可以安全重投或 `resume`。

优雅停机：API `stop_grace_period` 30 秒，worker 120 秒。`docker compose stop` 后 Redis 中未完成任务保留；重启后 worker 继续消费。

## 6. 备份

```bash
scripts/backup.sh                              # 默认 backups/<UTC 时间戳>/
scripts/backup.sh /mnt/nas/much-agents/20261024
BACKUP_CHROMA=1 scripts/backup.sh              # 额外备份 Chroma 卷
BACKUP_RETENTION_DAYS=30 scripts/backup.sh     # 修改保留期（默认 14 天）
```

内容：

- `postgres.dump`：`pg_dump -Fc`，包含用户、项目、run、审计等全部数据。
- `minio/`：`mc mirror` 的全量对象（parsed、bids、私有 profile）。其中 `private/profiles/` 含投标人资料快照，备份介质需按敏感数据保护。
- `chroma/chroma.tgz`（可选）：Chroma 卷；可从 parsed 产物重建，默认跳过。
- `SHA256SUMS`：所有文件的校验和。

注意事项：

- 备份期间建议暂停写入（至少暂停上传与生成），避免 Postgres 与 MinIO 跨系统不一致。
- 如果 MinIO 备份为空，脚本默认失败，避免生成一个恢复时会清空 bucket 的不可用备份。确实使用空 bucket 时才设置 `ALLOW_EMPTY_MINIO=1`。
- `deploy/env.production` 与 TLS 私钥**不在备份内**，单独加密保存。
- 保留策略只删除 `backups/` 下的旧目录；自定义目标目录需自行清理。

## 7. 恢复

```bash
scripts/restore.sh backups/20261024T120000Z
FORCE=1 scripts/restore.sh <dir>               # 自动化，无交互确认
RESTORE_CHROMA=1 scripts/restore.sh <dir>      # 同时恢复 Chroma
ALLOW_MISSING_CHECKSUMS=1 scripts/restore.sh <dir>  # 无 SHA256SUMS 时才使用
ALLOW_EMPTY_MINIO=1 scripts/restore.sh <dir>        # 仅限确实为空的 bucket
```

脚本会：校验 SHA256 → 停止 API/worker → 用 `mc mirror --remove` 恢复 MinIO → `pg_restore --clean --if-exists --exit-on-error` → 可选恢复 Chroma → 重启 API/worker → 打印表行数与对象数。

安全前置条件：

- 默认要求 `SHA256SUMS` 且校验通过；缺失时拒绝恢复（`ALLOW_MISSING_CHECKSUMS=1` 仅用于可信备份）。
- 默认拒绝缺失或为空的 `minio/` 备份，避免 `mc mirror --remove` 清空 bucket；确有空 bucket 时才用 `ALLOW_EMPTY_MINIO=1`。
- 恢复顺序是先 MinIO、后 PostgreSQL。任一步失败时脚本退出，**API/worker 保持停止**，不会把半恢复的数据库暴露给用户；排查后重跑同一备份，或改用更早的备份。脚本不自动执行迁移。

恢复后必须验证：登录、打开一个 run、下载产物、`GET /api/ready`、`python -m api.audit verify`。建议每季度在测试栈演练一次，并记录实际 RPO/RTO。

## 8. 日志与排障

```bash
docker compose -f docker-compose.prod.yml logs -f api
docker compose -f docker-compose.prod.yml logs -f worker-bid
docker compose -f docker-compose.prod.yml ps
```

日志为 json-file，单文件 20 MB、保留 5 个。应用错误只记录异常类名与 `request_id`，不记录 SQL 参数、密码或 token；用 `X-Request-ID` 关联一次请求在 Nginx/API/审计中的记录。

| 现象 | 排查 |
|---|---|
| API 启动即退出 | 查看日志中的 `unsafe production configuration`；按提示修正 `deploy/env.production` |
| `/api/ready` 503 | 看返回的 `checks`：`redis` / `database` / `storage` 哪项为 false；检查对应容器健康 |
| 登录 429 | 账号锁定或 IP 限流；等待窗口结束，或管理员重置密码 |
| 上传 503 | MinIO 不可达或 bucket 未创建；检查 `createbuckets` 日志与 MinIO 健康 |
| worker healthcheck 失败 | 确认 broker 密码一致、队列名正确、worker 日志无崩溃 |
| `/docs` 404 | 生产预期行为；需要接口文档时临时在开发环境启动 |
| 浏览器证书错误 | 自签证书未导入信任库，或证书 SAN 与访问地址不一致 |

## 9. 安全基线

- 仅 Nginx 发布 80/443；Postgres/Redis/MinIO/Chroma 不映射到主机。
- TLS 1.2/1.3、HSTS、CSP（内联主题脚本按 SHA-256 固定）、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、`server_tokens off`。
- `/docs`、`/redoc`、`/openapi.json` 在生产返回 404；`DOCS_ENABLED=false` 双保险。
- 登录：Nginx `limit_req` + 应用内按 IP 失败窗口 + 账号锁定；Argon2 并发受限。
- 生产 fail-fast 拒绝弱配置；`AUTH_ENABLED=false` 不能在生产启动。
- 项目 ACL、角色守卫、审计哈希链、产物私有边界（`profile.json` 永不可下载）默认生效。
- 容器非 root、只读根文件系统、`no-new-privileges`；密钥只通过 `env_file` 注入。

如修改 `frontend/index.html` 的内联主题脚本，必须重新计算 CSP hash：

```bash
python - <<'PY'
import base64, hashlib, re
html = open('frontend/index.html', encoding='utf-8').read()
body = re.search(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S).group(1)
print('sha256-' + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode())
PY
```

然后更新 `deploy/nginx/conf.d/default.conf` 中的 `script-src`。

## 10. CI

`.github/workflows/ci.yml` 在 push 到 `main`/`feat/**` 与 PR 时运行：

- **backend**：`uv sync --frozen`、Ruff、pytest。
- **migrations**：PostgreSQL 16 service，`upgrade head → downgrade -1 → upgrade head`。
- **frontend**：`npm ci`、lint、test、build、`npm audit --omit=dev --audit-level=high`。
- **docker**：校验 Compose、构建 API/Web 镜像、`nginx -t`。
- **security**：gitleaks、`pip-audit`。

本地等价检查：

```bash
uv run ruff check .
uv run pytest
cd frontend && npm run lint && npm run test && npm run build
docker compose -f docker-compose.prod.yml config
```

## 11. 数据边界与降级

- 解析默认走 MinerU 云端；无 `MINERU_API_KEY` 或网络不通时回退 PyMuPDF（能力下降）。
- 抽取/写作默认调用 DeepSeek；嵌入调用智谱。密钥缺失时抽取降级为正则、语义检索回退关键词；写作需要 DeepSeek。
- 外部 MCP 证据与外部绘图默认关闭（`MCP_CLIENT_ENABLED=false`、`BID_FIGURES_EXTERNAL=false`）。
- 如合规要求数据不出内网，需要单独建设 OpenAI 兼容网关/本地模型；本阶段未包含模型边界重构。
- Chroma 不可用不影响上传解析，检索自动回退关键词并返回 `warnings`。

## 12. 已知限制

- 单机 Compose，无 HA/多副本；Postgres/Redis/MinIO 故障会导致服务不可用。
- 没有内置监控/告警、Celery beat、自动备份调度；备份需由主机 cron/systemd timer 触发。
- 没有正式的前端 E2E 与压力测试；上线前建议按验收清单手工验证。
- `MCP_API_TOKEN` 是静态服务 token，轮换需同时重启 API 与 MCP。
- 会话 token 仍存 localStorage；依赖 CSP、Electron 守卫与 HTTPS 缓解 XSS 风险。
- Electron 生产建议直接加载 HTTPS 页面；`file://` 仅作离线开发模式。
