# MinIO 对象存储

Much Agents 支持两种存储后端，由 `STORAGE_BACKEND` 选择：

| 后端 | 行为 |
|---|---|
| `local` | 与旧版本完全一致：`upload/` 下的本地文件是唯一事实来源。 |
| `minio`（默认） | `inbox/`、`parsed/`、`bids/` 对象以 MinIO 为准；本地 `upload/` 只作为任务工作缓存。 |

`minio` 模式下，API 与 Celery worker 不再要求共享同一个 `upload/` 目录：任务开始前从 MinIO 拉取需要的输入到本地缓存，任务完成后把产物同步回 MinIO。旧版本遗留的本地数据仍可读取（MinIO 缺对象时回退本地缓存），下一次写操作会自然同步。

## 1. Key 布局

所有 key 都是 POSIX 相对路径；`doc_id`/`run_id` 会做安全字符校验。

| MinIO key | 本地缓存路径（`StorageLayout`） | 说明 |
|---|---|---|
| `inbox/<job_id>/source.pdf` | `<UPLOAD_DIR>/inbox/<job_id>/source.pdf` | 上传源文件；解析成功后尽力删除 |
| `parsed/<doc_id>/...` | `<PARSED_DIR>/<doc_id>/...` | 解析、抽取、分块产物 |
| `bids/<doc_id>/<run_id>/...` | `<BIDS_DIR>/<doc_id>/<run_id>/...` | 投标产物、配图、报告 |
| `private/profiles/<doc_id>/<run_id>.json` | `<UPLOAD_DIR>/private/profiles/...`（仅本地副本） | 投标人 profile 快照，**私有对象** |

`profile.json` 永远不会出现在 `bids/` 前缀、artifact allowlist、MCP 工具输出、Redis/Celery payload 或任何下载 URL 中；它只存为上述私有 key，用于跨机器继续编辑/组装。

## 2. 配置

```dotenv
# local（与旧版本一致）或 minio（默认）
STORAGE_BACKEND=minio
MINIO_ENDPOINT=localhost:9000
MINIO_SECURE=false
MINIO_BUCKET=much-agents
# 客户端凭据；start.bat 创建本地容器时也使用这两个值作为 root 账号
MINIO_ACCESS_KEY=much_agents
MINIO_SECRET_KEY=much_agents_dev
# 未设置 ACCESS/SECRET 时客户端回退使用
MINIO_ROOT_USER=much_agents
MINIO_ROOT_PASSWORD=much_agents_dev
# start.bat 仅用于本地容器控制台映射
MINIO_CONSOLE_PORT=9001
MINIO_TIMEOUT_SECONDS=30
```

- `MINIO_ENDPOINT` 可写成 `host:port` 或 `http(s)://host:port`；`https://` 会自动启用 `secure`。API/worker 使用的端口以 endpoint 为准。
- 客户端凭据优先取 `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`，缺失时才回退 `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD`。本地容器会用最终解析出的账号作为 root。
- `MINIO_BUCKET` 必须是 3–63 位小写 DNS 名称；非法值会回退 `much-agents` 并记录 warning。
- bucket 由 API/worker 首次操作时惰性创建，启动脚本不依赖 `mc` 客户端。

## 3. 一键启动

`start.bat` 默认会确保 `much-agents-minio` 容器运行：

```text
镜像      minio/minio:latest
数据卷    much-agents-minio-data -> /data
API       <MINIO_ENDPOINT 端口>:9000
控制台    <MINIO_CONSOLE_PORT>:<MINIO_CONSOLE_PORT>
命令      server /data --console-address :<MINIO_CONSOLE_PORT>
重启策略  unless-stopped
```

启动脚本会等待 `http://<endpoint>/minio/health/live`，然后把解析后的 `STORAGE_BACKEND` 显式传给 API 与 Celery worker 子进程。

| 命令 | 任务后端 | 存储后端 | MinIO 容器 |
|---|---|---|---|
| `start.bat` | celery | minio | 自动启动/复用 |
| `start.bat local` | local | local | 不启动 |
| `start.bat --no-minio` | celery | local | 不启动 |
| `start.bat --skip-docker` | celery | minio；不可达时降级 local | 不管理 |

- `stop.bat` 只停止由本次启动拉起的 Python/Node 进程，不关闭 PostgreSQL、Redis、MinIO 等容器。
- 端口被其他程序占用时，启动脚本会给出可操作提示：修改 `.env` 的 `MINIO_ENDPOINT`（例如 `localhost:9010`）和 `MINIO_CONSOLE_PORT`（例如 `9011`）后重试。
- `--dry-run` 只打印计划，不创建容器、不写 `.env`。

## 4. 运维命令

```bash
uv run python -m src.storage check   # backend/endpoint/bucket、连通性与各前缀对象数
uv run python -m src.storage sync    # 把本地存量 upload/ 数据增量上传到 MinIO
```

- `check` 会像 API 一样先执行 `ensure_ready()`（必要时创建 bucket），再检查连通性；鉴权失败会打印原因。
- `sync` 遍历 `upload/inbox/*`、`parsed/*`、`bids/*/*`，按 `(size, mtime_ns)` 增量上传；run 的 `profile.json` 单独上传到私有 key。`local` 后端下提示无需同步。
- 启动时**不会**自动全量迁移；需要时手动执行一次 `sync`。

## 5. 运行时行为

| 流程 | 读取 | 写入 |
|---|---|---|
| 上传 `POST /api/parse` | — | 先写 `inbox/...`，再创建 job；失败时清理本地与对象 |
| 解析 worker | 从 `inbox/` 重建本地 source；解析缓存命中 MinIO | `sync_document` 整个 parsed 树；成功后尽力删除 inbox 对象 |
| 文档库 / 关键词搜索 | 直接读对象列表与 `chunks.jsonl`；语义检索仍走 Chroma | — |
| 创建 run | 拉取 parsed 缓存后规划大纲 | 上传私有 profile + `sync_run`（profile/outline） |
| 生成 / 续跑 | parsed 缓存 + 私有 profile | 章节检查点后，组装时统一同步 |
| 组装 / 合规 / 修复 | 拉取 parsed、run 与配图清单 | 同步报告、配图、`sections/`；清理过期派生对象 |
| 编辑 / 重写章节 | 拉取 run profile | 删除远程报告/门禁/合规/一致性/配图 |
| 导出 DOCX | 确保 `bid.docx` 本地存在 | 流式返回对象字节 |
| legacy `POST /api/bids` | submit 时 parsed，执行时 source+profile | 上传私有 profile + `sync_run` 全量产物 |

- **读取兜底**：`get_bytes`/`stat`/`open_stream`/`iter_lines` 在远程缺对象时回退本地缓存；`list()` 合并远程与本地 key（远程优先）。存量本地数据因此仍可列出/下载。
- **artifact 下载**：API 从对象存储流式返回字节，支持 `Range`（206/416）、`HEAD`、`ETag`、`Last-Modified` 与 RFC 5987 中文文件名；**不使用 presigned 重定向**，前端与 MCP 服务端无需改动。
- **删除范围**：`delete_missing` 只用于 `figures/`、`sections/` 等窄子树，避免与并发编辑/组装竞争。
- 异步服务中的存储调用统一通过 `asyncio.to_thread` 执行，不阻塞事件循环。

## 6. 故障与降级

| 现象 | 行为 |
|---|---|
| MinIO 不可达 | `GET /api/health` 返回 `status=degraded`、`storage_ok=false`；artifact/上传/文档库/搜索/run 接口返回 503 |
| worker 无法获取输入或同步产物 | 任务标记 failed，本地文件保留；恢复后可重试 |
| `start.bat --skip-docker` 且 MinIO 不可达 | warning 并降级 `STORAGE_BACKEND=local` |
| 凭据变更后旧容器仍用旧账号 | 客户端收到 `AccessDenied`；执行 `docker rm -f much-agents-minio` 后重新运行 `start.bat`（数据卷保留） |
| bucket 名非法 | 回退 `much-agents` 并记录 warning |
| 找不到对象 | 404（`StorageObjectNotFound`）；元数据缺失的 run 编辑/组装返回 409 |

恢复 MinIO 后无需重启 API：连接在下一次请求时重建。若需确认，运行 `uv run python -m src.storage check`。

## 7. 安全边界

- `profile.json` 只存私有 key，永不进入 artifact allowlist、MCP 输出、Redis/Celery payload 或下载 URL。
- artifact 下载始终经过 allowlist 与路径穿越校验；`profile.json` 明确 404。
- MCP 服务端是本地 API 的薄代理，不直接访问 MinIO、DB、Redis 或文件系统。
- 外部证据仍默认不可信，外部绘图仍默认关闭；对象存储不改变这些边界。

## 8. 测试

`uv run pytest` 全部使用内存 fake 存储与 fake MinIO 客户端，不需要 Docker 或真实 MinIO。启动脚本相关测试覆盖：默认 minio、`--no-minio`/`local` 强制 local、容器命令追加位置、外部 endpoint 跳过容器、端口冲突提示、`--skip-docker` 降级。
