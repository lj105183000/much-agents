# M1 生产部署验收报告（本地 Docker Desktop 演练）

> 日期：2026-10-24
> 分支：`feat/multi-agent-bid-writing`
> 验收基线：`7722166`（本报告单独提交）
> 结论：**本地 P0 上线闭环通过**。生产 Compose 可从空卷启动，认证/RBAC/项目隔离/审计/对象存储边界、重启持久化、备份恢复均按预期工作；验收期间发现并修复了 6 个部署缺陷和 1 处验收脚本问题。这不是最终 M1 签字，原因见「残留风险」。

## 1. 范围与边界

本轮验证的是 **Windows 11 + Docker Desktop 单机生产拓扑演练**，不是最终生产环境验收。

已做：

- 生成仅本机使用的 `deploy/env.production` 与自签 `localhost` 证书（均在 gitignore 内，未提交、未打印）。
- 串行构建生产镜像，启动 `docker-compose.prod.yml`，执行 smoke、认证与角色矩阵、两项目隔离、审计、MinIO 降级、重启持久化、备份/恢复演练。
- 用合成中文 PDF 上传并解析；**未使用真实招标文件**。
- 解析使用 `parser=pymupdf&extract=true&ingest=false`，抽取走正则降级，不调用 MinerU/DeepSeek/智谱。
- 投标 run 只创建大纲，**未调用生成 LLM**，未验证组装、配图与 DOCX 正向导出；导出只验证了拒绝路径。
- 报告脱敏后提交；验收时仓库为私有且无 remote，后续已于 2026-10-24 推送 GitHub；项目采用 AGPL-3.0-or-later，公开状态以 GitHub 为准。

未做：

- 干净 Linux VM、真实内网 DNS/CA、真实 HTTPS 域名与客户端信任链。
- 远程 CI（验收时 `.github/workflows/ci.yml` 未在 GitHub 运行，仅做本地等价检查）；后续已推送，CI 结果以 GitHub Actions 为准。
- 模型网关/数据不出内网改造、P1–P4 功能、前端 E2E/压测/监控告警。
- 真实生产密钥、真实公司资料或真实投标数据。

## 2. 环境

| 项 | 值 |
|---|---|
| 主机 | Windows 11 Home China 10.0.26300 |
| 容器 | Docker Desktop 29.8.2（Linux 容器） |
| Compose | 独立 `docker-compose` v5.6.0（本机未安装 Compose 插件） |
| 资源 | 16 CPU / 8.26 GB 内存 / 约 77 GB 可用磁盘 |
| Python | 3.12.14（uv 管理） |
| 生产项目名 | `much-agents-prod`（卷/网络与开发栈隔离） |
| TLS | 自签 `CN=localhost`，SAN `DNS:localhost`，2026-10-24 至 2029-01-26 |
| 镜像策略 | `postgres`/`redis` 固定小版本，`minio`/`mc`/`chroma` 使用 `latest`（digest 见下） |

## 3. 部署结果

启动命令：

```bash
docker-compose -f docker-compose.prod.yml up -d
```

结果：

- `migrate` 与 `createbuckets` 均 `Exited (0)`；API 等待两者成功后启动。
- `postgres`、`redis`、`minio`、`chroma`、`api`、`worker-parse`、`worker-bid`、`nginx` 全部 healthy/running。
- `alembic current` = `0005 (head)`。
- 仅 `nginx` 发布主机 80/443；其余服务只暴露 Compose 内部端口。
- 5 个命名卷：`postgres-data`、`redis-data`、`minio-data`、`chroma-data`、`upload-data`。
- API/worker 容器：非 root（`user=app`）、`readonly=true`、`no-new-privileges=true`。
- `nginx -t` 通过。

### 镜像 digest

| 镜像 | digest |
|---|---|
| `postgres:16-alpine` | `sha256:f4610795587492cd4fb887c2dc3de301b813278bb6f616992061141b5f15ac36` |
| `redis:7-alpine` | `sha256:57e4255cc971b1c8d6f8a6ca283ac01bc3ecdd914273ef6bfaede87aaecd6541` |
| `minio/minio:latest` | `sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e` |
| `minio/mc:latest` | `sha256:a7fe349ef4bd8521fb8497f55c6042871b2ae640607cf99d9bede5e9bdf11727` |
| `chromadb/chroma:latest` | `sha256:1e0b73a187a28757c572acba508c46f48c9e8b0acaf5c20e6d95cdedce1acdf6` |
| `python:3.12-slim`（构建基座） | `sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704` |
| `node:22-alpine`（构建基座） | `sha256:fe4f6d74e20370acb04ad889077d14d0f64a9c8ea6a42bc60cddf1e844699914` |
| `nginx:1.27-alpine`（运行基座） | `sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10` |

本地构建镜像 ID：`api` `e0994339efa6`，worker `e1ba1857d8c0`，`migrate` `c51cb627168a`，`nginx` `5d4fadf50d58`。

## 4. Smoke 结果

| 检查 | 结果 |
|---|---|
| `http://localhost/` | 301 → `https://localhost/` |
| `/healthz`（HTTP/HTTPS） | 200 / 200 |
| `/api/live` | 200 |
| `/api/ready` | 200，`redis/database/storage` 均 true |
| `/api/health` | 200，`storage_backend=minio`，`storage_ok=true` |
| `/docs`、`/redoc`、`/openapi.json` | 404 / 404 / 404 |
| 安全头 | HSTS、CSP、`X-Content-Type-Options`、`X-Frame-Options: DENY`、`Referrer-Policy` 均存在 |
| 前端 | `/` 200，入口 JS 200，标题 `Much Agents · 标书工作台` |
| 证书 | `CN=localhost`，SAN `DNS:localhost`，自签 |

## 5. M1 功能验收

自动化脚本：`backups/acceptance/m1_acceptance.py`（本地运行，读取本地密码文件，不打印密码/token）。**63/63 通过。**

| 类别 | 代表性断言 | 结果 |
|---|---|---|
| 认证 | 未认证 `GET /api/projects` → 401 | 通过 |
| 项目头 | 缺失/非法 `X-Project-Id` → 400 / 400 | 通过 |
| 跨项目 | writer→B、manager→A、viewer→A 均 404；admin 跨项目读 200 | 通过 |
| 上传/解析 | writer 在 A、manager 在 B 上传 → 202；两个 pymupdf 解析任务 succeeded | 通过 |
| 角色组合 | 全局 writer + 项目 viewer 写 → 403；全局 viewer + 项目 manager 写 → 403；全局 bid_manager + 项目 manager 通过守卫 | 通过 |
| 用户管理 | manager/viewer 列用户 → 403 | 通过 |
| 公司脱敏 | manager 建公司 201；viewer 详情电话/银行/profile 为空；viewer 改公司 → 403 | 通过 |
| 文档/任务/产物 | 文档列表按项目隔离；跨项目 job 404；跨项目 artifact 404；本项目 meta.json 200 | 通过 |
| 检索 | 跨项目 doc_id → 404；本项关键词检索 200 | 通过 |
| Run 隔离 | writer 在 A 建 run 201；manager 在 B 建 run 201；跨项目读 run 404；本项 200 | 通过 |
| 产物 allowlist | run 产物列表无 `profile.json`/`bid.docx`；直接请求均 404 | 通过 |
| 导出守卫 | writer 导出 → 403；admin `force=true` 无理由 → 422 | 通过 |
| 服务账号 | service token 读 B 200；列用户 403；缺项目头 400 | 通过 |
| 审计 | `document.upload` 事件存在；链 valid；CSV 可导出；profile 标记未进入审计 | 通过 |
| 登录锁定 | 5 次错误 → 401×5；锁定 → 429；管理员重置后可登录 200 | 通过 |

补充检查：

- **审计追加性**：对 `audit_events` 执行 `UPDATE` 被数据库触发器拒绝（`audit_events is append-only`），随后链校验仍 valid。
- **MinIO 降级**：停止 minio 后 `/api/live` 200、`/api/ready` 503、`checks.storage=false`、`/api/health` 为 `degraded`；恢复 minio 后 ready 200。
- **重启持久化**：`down`（不带 `-v`）再 `up -d` 后，users/projects/documents/bid_runs/audit_events 计数完全一致；管理员旧会话仍 200；run/产物可读；`profile.json` 仍 404；审计链 valid。**9/9 通过。**

## 6. 备份/恢复演练

自动化脚本：`backups/acceptance/m1_drill.py`。流程：

1. 创建 baseline 项目与 MinIO 对象；
2. 停止 API/workers，取得静默快照计数；
3. 执行真实 `scripts/backup.sh`；
4. 启动 API/workers，删除 baseline 项目/对象，创建 post-backup 项目/对象；
5. 执行 `FORCE=1 scripts/restore.sh <backup>`；
6. 验证恢复结果。

结果：**27/27 通过。**

| 指标 | 值 |
|---|---|
| 备份目录 | `backups/m1-20261024T110327Z`（本地，gitignore） |
| 备份耗时 | 3.2 s（API/workers 停止，数据量小） |
| 备份体积 | 159,165 字节（33 个文件；MinIO 31 个对象） |
| 恢复耗时（RTO） | 16.6 s，至 `/api/ready` 200 |
| 演练 RPO | 14.1 s（备份完成到开始恢复的间隔；生产 RPO 取决于备份调度） |
| 恢复前计数 | users 9 / projects 10 / documents 4 / bid_runs 3 / audit_events 94 |
| 恢复后计数 | 完全一致；MinIO 对象 31 → 31 |
| 标记对象 | baseline 项目/对象恢复存在；post-backup 项目/对象已回滚 |
| 迁移版本 | `0005 (head)` |
| 审计 | CLI 与 API 校验均 valid |
| 产物边界 | run/artifact 可读，`profile.json` 仍 404 |

其他验证：

- `SHA256SUMS` 校验通过（脚本兼容 GNU 双空格与 Git Bash `*path` 格式）。
- 备份包含 31 个 MinIO 对象；空 bucket 时备份脚本默认拒绝，避免生成不可用备份。
- 恢复脚本先恢复 MinIO 再 `pg_restore --exit-on-error`，成功后重启 API/workers；核心步骤失败时保持 API/workers 停止。
- 恢复脚本的最终计数为信息性输出，计数失败不会把已完成的恢复标记为失败。

## 7. 验收中发现并修复的问题

| 问题 | 影响 | 修复 |
|---|---|---|
| `x-app-service` 未加载 `deploy/env.production` | migrate/API/worker 缺少 DATABASE_URL、MinIO、MCP 配置，生产 fail-fast 直接拒绝启动 | `eb0558b`：锚点增加 `env_file` |
| Chroma 健康检查调用 Python | 当前 `chromadb/chroma:latest` 是 Rust 镜像，无 Python/curl/wget，健康检查永远失败，API/worker 无法启动 | `9ff458b`：改用 `bash /dev/tcp` 请求 `/api/v2/heartbeat` |
| nginx 只在启动时解析 `api` | API 容器重建/恢复后 upstream IP 过期，返回 502，直到重启 nginx | `68d4bfb`：使用 Docker DNS + 变量 `proxy_pass`，10 s 重新解析 |
| 恢复脚本最终计数在宿主机展开 `POSTGRES_USER` | 恢复已成功并重启后脚本以 `unbound variable` 退出 1，容易被误判为恢复失败 | `7722166`：在容器内展开变量；计数改为非致命；ERR trap 区分已重启状态 |
| Web/nginx 构建未传镜像源参数 | legacy builder 为目标 `web` 重建 api-base 时回退 pypi.org，受限网络构建失败 | `b8d5b95`：nginx build args 增加 APT/PIP 镜像参数 |
| Git Bash Docker 绑定挂载路径 | `/c/...` 不被 Docker Desktop 接受，备份/恢复挂载失败 | `2db8d58`：`cygpath -m` 转换 |
| 验收脚本先降级 writer 再建 run | 把正确的 403 误报为失败 | 本地脚本调整顺序后重跑（不提交） |

## 8. 残留风险与上线前必做

1. **环境差异**：本轮是 Windows Docker Desktop 演练。上线前需在干净 Linux 主机重跑本报告第 3–6 节，并使用真实 DNS/企业 CA 证书。
2. **未验证 LLM 正向链路**：未配置模型密钥，未执行生成、组装、合规/一致性、配图与 DOCX 导出；只验证了解析、run 创建和导出拒绝路径。
3. **镜像未 pin**：`minio`、`mc`、`chroma` 使用 `latest`。上线前应按本报告 digest 固定版本并纳入升级流程。
4. **Chroma 是启动硬依赖**：`api`/`worker-bid` 等 Chroma healthy 才启动；运行时检索可回退关键词，但 `/api/ready` 不检查 Chroma，Chroma 运行时故障时 ready 仍可能为 200。
5. **备份无调度/加密/异地**：`scripts/backup.sh` 需由 cron/systemd timer 触发；备份含私有 `private/profiles/`，介质必须加密；默认不备份 Chroma（可从 parsed 产物重建）。
6. **恢复是破坏性操作**：脚本不自动迁移、不自动创建恢复前备份；核心步骤失败时需人工排查后重跑或换更早备份。
7. **`TRUSTED_PROXIES=*`**：本地演练保留 `*`，且不支持 CIDR；生产应限制为实际反代地址。
8. **CLI 自动迁移**：`python -m api.users` / `api.projects` 即使 `DB_AUTO_MIGRATE=false` 也会初始化并尝试迁移；生产引导应在 `migrate` 成功后执行，且避免并发运行。
9. **远程 CI 未运行（验收时）**：仓库无 remote；`.github/workflows/ci.yml` 仅做了本地等价检查（ruff、pytest 412 通过、前端 lint/test/build、Compose 校验）。后续已推送 GitHub（2026-10-24），CI 结果以仓库 Actions 页面为准。
10. **可观测性与压测**：无监控告警、Celery beat、前端 E2E/压力测试；上线前需补最小监控与一次真实数据演练。
11. **静态服务 token**：`MCP_API_TOKEN` 轮换需同时重启 API 与 MCP；会话 token 仍存 localStorage，依赖 HTTPS/CSP/Electron 守卫。
12. **验收数据**：本地生产卷保留合成测试用户/项目/文档/run 与审计事件；生产环境不要复用该卷。`deploy/env.production`、管理员密码、TLS 私钥和备份均未提交。

## 9. 复现与证据

本地复现（需先准备 `deploy/env.production` 与证书）：

```bash
docker-compose -f docker-compose.prod.yml up -d
curl -k https://localhost/api/ready
uv run python backups/acceptance/m1_acceptance.py
uv run python backups/acceptance/m1_restart.py
uv run python backups/acceptance/m1_drill.py
```

证据文件（均在本机 gitignore 的 `backups/` 下，不提交）：

- `backups/acceptance/results.json`：63 项功能验收明细。
- `backups/acceptance/restart.json`：9 项重启持久化明细。
- `backups/acceptance/drill.json`：27 项备份/恢复明细与 RPO/RTO。
- `backups/m1-20261024T110327Z/`：演练备份（`postgres.dump`、`minio/`、`SHA256SUMS`）。

生产环境复查顺序：`/api/ready` → 管理员登录 → 打开 run/下载产物 → `python -m api.audit verify` → 备份/恢复演练记录。本报告不包含任何密码、token、证书私钥或投标人资料。
