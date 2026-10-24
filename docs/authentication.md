# 认证、会话与 RBAC

> 状态：P0-A 已实现（2026-10-24）。本切片提供本地账号、可撤销会话、角色守卫、管理员用户管理和 MCP 服务账号。P0-B 项目隔离、P0-C 审计、P0-D 生产加固与 TLS 已补齐，见 `docs/projects.md`、`docs/audit.md`、`docs/deployment.md`；剩余边界见文末。

## 1. 范围与设计

- 所有 `/api/*` 默认要求认证，公开例外只有 `GET /api/health`、`GET /api/live`、`GET /api/ready` 和 `POST /api/auth/login`。
- 使用**不透明 Bearer token**（`secrets.token_urlsafe(32)`），服务端只保存 SHA-256 哈希，不保存明文；登出、改密、重置密码、停用账号或修改角色都会撤销会话。
- 密码使用 Argon2（`argon2-cffi`）哈希；校验在线程池中执行，并受 `AUTH_HASH_CONCURRENCY` 信号量限制，避免并发登录耗尽线程池。
- 认证状态保存在 PostgreSQL 的 `users` 与 `user_sessions` 表（Alembic `0003`）。Redis 仍只保存 Celery 任务状态。
- 前端登录后把 token 保存在 `localStorage`（键 `much-agents:auth:v1`），所有 API 请求通过 `Authorization: Bearer <token>` 发送；产物图片和下载也用带认证头的 `fetch` 取 Blob，不使用 query token 或 cookie。
- `AUTH_ENABLED=false` 是显式的本地开发逃生口，不是生产模式。

## 2. 角色与权限

| 角色 | 中文 | 能做什么 |
|---|---|---|
| `admin` | 管理员 | 全部操作；用户管理；删除公司；强制导出（`force=true`） |
| `bid_manager` | 投标经理 | 上传/解析、创建/生成/编辑/组装 run、单节编辑与重写、合规修复、公司库新增/编辑、普通导出 |
| `writer` | 编写人 | 编写类操作：上传/解析、创建/生成/编辑/组装、单节编辑与重写、应用修复、发起合规检查与修复方案 |
| `reviewer` | 复核人 | 只读浏览；发起合规检查与生成修复方案；不能修改正文、生成或导出 |
| `viewer` | 只读 | 只读浏览文档、run、章节、门禁、产物等；不能上传、生成、编辑、导出或管理公司 |
| `service` | MCP 服务账号 | 通过 `MCP_API_TOKEN` 调用业务 API；可执行写入/复核/经理级操作，但**不能管理用户、不能强制导出、不能删除公司** |

权限层级：

- 所有登录角色都可以读取业务数据（run、文档、大纲、章节、门禁、合规、一致性、配图、公司摘要等）。
- `require_write`：`admin` / `bid_manager` / `writer`（`service` 可通过）。
- `require_review`：`require_write` + `reviewer`（`service` 可通过）。
- `require_manage`：`admin` / `bid_manager`（`service` 可通过），用于公司新增/编辑与普通导出。
- `require_admin`：仅交互式 `admin`，`service` 一律拒绝；用于用户管理、删除公司和强制导出。

服务端始终是权限权威；前端隐藏或禁用按钮只是减少误操作，直接调用接口仍会得到 401/403。

## 3. 首次引导管理员

前提：PostgreSQL 已启动，且迁移已应用（API 启动默认会自动执行 `alembic upgrade head`，也可以手动执行）。

```bash
uv run alembic upgrade head
uv run python -m api.users create-admin --username admin --display-name 管理员
```

命令会交互式要求输入两次密码；密码不会回显。自动化环境可以从标准输入提供密码（不会提供 `--password` 参数，避免进入 shell 历史）：

```bash
printf '%s\n' '<强密码>' | uv run python -m api.users create-admin \
  --username admin --display-name 管理员 --password-stdin
```

其他维护命令：

```bash
uv run python -m api.users list
uv run python -m api.users create-user --username zhangsan --display-name 张三 --role writer
uv run python -m api.users reset-password --username admin
```

- 用户名统一转为小写，需为 3–64 位小写字母、数字、点、下划线或连字符，且以字母或数字开头。
- 密码长度 12–128 个字符，不能等于用户名；少量常见弱口令会被拒绝。
- 密码遗失时用 `reset-password` 重置；重置会撤销该用户全部会话。
- 不能停用或降级最后一个启用中的管理员（接口返回 409）；请先创建第二个管理员。

## 4. 登录、会话与密码

```bash
curl -X POST http://127.0.0.1:8001/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<密码>"}'
```

成功返回：

```json
{
  "token": "<opaque-token>",
  "expires_at": "2026-10-24T12:00:00Z",
  "user": {"id": "...", "username": "admin", "display_name": "管理员", "role": "admin"}
}
```

后续请求：

```bash
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8001/api/auth/me
```

端点：

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/auth/login` | 公开；成功后创建会话 |
| `GET` | `/api/auth/me` | 当前用户；本地模式返回 `auth_enabled=false` |
| `POST` | `/api/auth/logout` | 撤销当前会话，返回 204 |
| `POST` | `/api/auth/password` | 修改自己的密码（`current_password` / `new_password`），撤销全部会话 |
| `GET` | `/api/users` | 管理员：用户列表 |
| `POST` | `/api/users` | 管理员：创建用户 |
| `PATCH` | `/api/users/{user_id}` | 管理员：修改显示名/角色/启用状态 |
| `POST` | `/api/users/{user_id}/password` | 管理员：重置密码并撤销该用户全部会话 |

会话规则：

- 默认有效期 12 小时（`AUTH_SESSION_TTL_HOURS`），不做滑动续期；过期后重新登录。
- 连续登录失败默认 5 次后锁定 15 分钟（`AUTH_LOGIN_MAX_FAILURES` / `AUTH_LOCKOUT_MINUTES`）。锁定期间即使密码正确也返回 429。
- 另有按 IP 的失败窗口：默认 300 秒内失败 20 次后拒绝该地址的登录（`AUTH_LOGIN_IP_*`）；生产 Nginx 还有 `limit_req`。只有来自 `TRUSTED_PROXIES` 的 `X-Forwarded-For` 才被采信。
- 未知用户名也会执行一次 dummy Argon2 校验，降低用户名枚举的时间侧信道。
- 角色修改或停用账号会立即撤销该用户全部会话。
- 认证开启但数据库不可用时，受保护接口返回 503（fail closed），不会退化为匿名访问。
- 最后一个启用管理员的停用/降级检查在单事务内使用 `pg_advisory_xact_lock`，避免并发请求同时通过检查。

## 5. 前端行为

- 应用挂载时无论有没有本地 token 都请求 `GET /api/auth/me`：认证关闭时直接进入本地模式；有 token 时验证；401 则显示登录页。
- 登录页支持切换「API 连接设置」。切换 API base 会清除已保存的会话并重新加载页面，避免把 A 后端的 token 发给 B 后端。
- 任意请求返回 401 会清除本地会话并回到登录页；登录接口本身的 401 只显示错误，不做全局登出。旧 token 的在途请求不会误踢新会话。
- 会话保存在 `localStorage` 的 `much-agents:auth:v1`，并与当前 API base、过期时间绑定；不写入 URL、不使用 cookie。
- 任务历史按用户 id 分键保存，避免共享浏览器串数据。
- 「用户管理」与「审计日志」视图只对管理员显示；角色感知按钮覆盖上传、编写、组装、编辑、重写、配图重新生成、公司管理和导出。
- Header 提供项目切换器（业务请求自动带 `X-Project-Id`）、修改密码对话框与登出；登出会清除当前用户的解析/任务历史与项目选择。
- 产物预览与下载通过带认证的 Blob 请求完成；图片使用 `AuthenticatedImage`，Markdown 内图片也会被重写为认证 Blob。没有 query token。

## 6. MCP 服务账号

有两个不同的 token：

| 变量 | 方向 | 作用 |
|---|---|---|
| `MCP_API_TOKEN` | MCP 服务端 → 业务 API | 以 `service` 身份调用 `/api/*` |
| `MCP_HTTP_TOKEN` | 外部客户端 → MCP HTTP 传输 | 保护 MCP 自身 HTTP 入口；非回环绑定必填 |

配置示例：

```bash
MCP_API_TOKEN=<随机长字符串>
```

- `api/mcp_server.py` 会在向业务 API 发送请求时自动附加 `Authorization: Bearer $MCP_API_TOKEN`。
- `AUTH_ENABLED=true` 且 `MCP_API_TOKEN` 为空时，MCP 启动日志会警告，实际工具调用会收到 401。
- `service` 不能管理用户、不能强制导出；MCP 仍是 API 薄代理，不直接访问数据库、Redis、Celery 或文件系统。
- 轮换：修改 `.env` 后同时重启 API 与 MCP 服务；旧 token 立即失效。MCP 客户端侧不需要配置这个 token，它由 MCP 服务端读取环境变量。

## 7. 本地模式（`AUTH_ENABLED=false`）

适用场景：没有 PostgreSQL、只在本机做前端/解析调试。

- 所有请求被当作本地 `admin`（id `local`，显示名「本地模式」）。
- `GET /api/auth/me` 返回 `auth_enabled=false`，前端跳过登录页。
- `POST /api/auth/login` 返回 400 `authentication is disabled`。
- 用户管理接口不可用；没有会话、登出或密码概念。
- **绝不要**在监听 `0.0.0.0` 或接入内网时使用此模式；任何能访问端口的人都是管理员。
- 默认是 `AUTH_ENABLED=true`；开启认证但未配置数据库时，受保护接口返回 503，而不是放行。

## 8. 当前边界与已知风险

P0-B/C/D 已补齐项目隔离、审计与生产部署基线，但仍存在以下边界：

1. **项目成员只能由管理员管理**：没有项目 `manager` 自助加人；公司库仍是全局共享，不做项目隔离。
2. **token 存在 localStorage**：XSS 仍可窃取会话。生产已启用 CSP、Electron 导航/权限守卫与 HTTPS，但没有安全存储或短时 token；后续可评估 Electron `safeStorage` 或会话 cookie。
3. **共享的 `MCP_API_TOKEN`**：泄露即获得 service 权限；只应在受控 MCP 进程使用，定期轮换并同时重启 API/MCP。
4. **`/docs` 的生产保护依赖配置**：`DOCS_ENABLED=false` + Nginx 404 双保险；开发环境仍公开。
5. **审计与业务写入非同一事务**：`required=True` 的操作审计失败会返回 503，但业务可能已提交；见 `docs/audit.md` 的残余窗口说明。
6. **没有 MFA/OIDC/LDAP**：仍是本地账号 + 密码；外网暴露前应增加多因素或接入企业身份源。
7. **没有 IP 白名单/下载水印**：内网部署仍需网络层访问控制；敏感资料下载可被有权限用户转存。
8. **`AUTH_ENABLED=false` 仍是本机逃生口**：生产 fail-fast 会拒绝启动，但开发误配置仍可能暴露端口；不要用 `0.0.0.0` 监听。

## 9. 配置速查

| 变量 | 默认 | 说明 |
|---|---|---|
| `AUTH_ENABLED` | `true` | 关闭后进入本地 admin 模式（仅开发） |
| `AUTH_SESSION_TTL_HOURS` | `12` | 会话有效期（小时） |
| `AUTH_LOGIN_MAX_FAILURES` | `5` | 连续失败多少次后锁定 |
| `AUTH_LOCKOUT_MINUTES` | `15` | 锁定时长（分钟） |
| `AUTH_HASH_CONCURRENCY` | `2` | Argon2 并发上限 |
| `AUTH_LOGIN_IP_MAX_FAILURES` | `20` | 单 IP 失败窗口阈值 |
| `AUTH_LOGIN_IP_WINDOW_SECONDS` | `300` | 单 IP 失败窗口长度 |
| `TRUSTED_PROXIES` | 空 | 可信代理地址，逗号分隔；`*` 表示信任直连代理 |
| `DOCS_ENABLED` | `true` | 是否暴露 `/docs`、`/redoc`、`/openapi.json` |
| `ENVIRONMENT` | `development` | `production` 时执行 fail-fast 检查 |
| `MCP_API_TOKEN` | 空 | MCP 服务端调用业务 API 的服务 token |
| `MCP_HTTP_TOKEN` | 空 | MCP HTTP 入站 token（非回环绑定必填） |

数据库迁移：

- `0001`：`companies`
- `0002`：`bid_runs` / `bid_outline_sections` / `bid_section_states`
- `0003`：`users` / `user_sessions`
- `0004`：`projects` / `project_members` / `documents` + `bid_runs.project_id`
- `0005`：`audit_events`（追加写 + 哈希链）

## 10. 排障

- **401 `authentication required`**：缺少 `Authorization: Bearer`。检查前端 API base 是否正确、会话是否过期。
- **401 `invalid or expired session`**：会话被撤销、过期或账号被停用；重新登录。
- **403 `permission denied`**：当前角色无权执行该动作，或 service token 尝试管理员操作。
- **429 账号已锁定**：等待锁定时间结束，或由管理员重置密码（会清空失败计数与锁定）。
- **503 `authentication is unavailable`**：`AUTH_ENABLED=true` 但数据库未配置/不可用；检查 `DATABASE_URL` 与 PostgreSQL。
- **400 `authentication is disabled`**：后端设置了 `AUTH_ENABLED=false`，登录接口不适用。
- **MCP 全部工具 401**：检查 API 与 MCP 是否读取了同一个 `.env` 中的 `MCP_API_TOKEN`；修改后需同时重启两者。
