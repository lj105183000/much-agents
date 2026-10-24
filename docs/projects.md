# 项目注册表与对象级 ACL（P0-B）

> 状态：已实现（2026-10-24）。项目隔离把「登录后可见全部数据」收紧为「登录角色 AND 项目成员角色」。全局角色决定能做什么动作，项目角色决定能在哪些数据上做。

## 1. 数据模型

Alembic `0004_projects_acl.py` 新增三张表，并给 `bid_runs` 增加 `project_id`：

| 表 | 关键列 | 说明 |
|---|---|---|
| `projects` | `id` UUID 字符串、`name`、`slug`（唯一）、`description`、`created_by`、`created_at`、`updated_at` | 一个招标项目 |
| `project_members` | `(project_id, user_id)` 主键、`role`、`created_at` | 用户在某项目内的角色 |
| `documents` | `(project_id, doc_id)` 主键、`filename`、`created_by`、`created_at`、`updated_at` | 已解析文档的项目注册表 |
| `bid_runs.project_id` | 非空，复合外键 `(project_id, doc_id) → documents` | run 归属项目 |

- 默认项目固定 id 为 `00000000-0000-0000-0000-000000000001`（名称「默认项目」）。
- 迁移会创建默认项目，把现有用户按全局角色加入默认项目，把 `bid_runs` 中出现的 `doc_id` 回填到 `documents`，再把 `bid_runs.project_id` 置为非空。
- 删除项目时，成员与文档注册表级联删除；仍有 `queued/generating/assembling` 的 run 时返回 409。默认项目不能删除。
- 解析产物仍按内容寻址共享：`upload/parsed/<doc_id>/`（或 MinIO `parsed/<doc_id>/`）不因项目复制。同一份 PDF 出现在两个项目时只共享缓存，读取权限仍按项目判定。

## 2. 项目角色

| 项目角色 | 读 | 编写/生成 | 合规复核 | 导出 |
|---|---|---|---|---|
| `manager` | ✅ | ✅ | ✅ | ✅ |
| `writer` | ✅ | ✅ | ✅ | ❌ |
| `reviewer` | ✅ | ❌ | ✅ | ❌ |
| `viewer` | ✅ | ❌ | ❌ | ❌ |

有效权限 = **全局角色守卫 AND 项目角色**：

- 全局 `admin`：可访问任意项目（`require_project_*` 直接放行），但请求仍必须带合法的 `X-Project-Id`。
- 全局 `service`（MCP）：不校验成员关系，但必须显式带 `X-Project-Id`，不会自动落到默认项目。
- 其他用户：必须是项目成员；非成员与不存在的项目都返回 **404**，不泄露项目是否存在。
- 全局 `viewer` 即使项目角色是 `manager`，也不能上传/生成——全局守卫先拒绝。项目角色不能提升全局权限。
- `AUTH_ENABLED=false` 的本地模式合成默认项目、跳过 ACL，所有请求按 `manager` 处理。

映射关系（创建用户或迁移时自动写入默认项目）：

| 全局角色 | 默认项目角色 |
|---|---|
| `admin` / `bid_manager` | `manager` |
| `writer` | `writer` |
| `reviewer` | `reviewer` |
| `viewer` | `viewer` |

## 3. 请求头

所有业务接口（文档、检索、任务、run、章节、产物）都需要：

```http
X-Project-Id: <project-uuid>
```

- 缺失或格式非法 → 400 `project context required` / `invalid project id`。
- 项目不存在或当前用户不是成员 → 404 `project not found`。
- 项目存在但角色不足 → 403 `permission denied`。
- `/api/projects`、`/api/users`、`/api/audit`、`/api/auth/*`、`/api/health`、`/api/live`、`/api/ready` 不需要该头。
- 前端登录后自动加载 `GET /api/projects`，选择项目后由 API 客户端统一附加；当前项目按 `用户 + API base` 保存在 localStorage，切换项目会重新挂载业务视图。

## 4. 管理接口

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `GET` | `/api/projects` | 登录 | 普通用户只看自己的项目；admin 看全部；service 返回空列表 |
| `POST` | `/api/projects` | admin | 创建项目，创建者自动成为项目 `manager` |
| `PATCH` | `/api/projects/{project_id}` | admin | 修改名称/描述；`slug` 不可改 |
| `DELETE` | `/api/projects/{project_id}` | admin | 删除项目；默认项目与活跃 run 返回 409 |
| `GET` | `/api/projects/{project_id}/members` | admin | 成员列表 |
| `PUT` | `/api/projects/{project_id}/members/{user_id}` | admin | 新增或修改成员角色 |
| `DELETE` | `/api/projects/{project_id}/members/{user_id}` | admin | 移除成员 |

项目 slug 规则：2–64 位小写字母、数字或连字符，且以字母或数字开头。

### CLI

没有管理员账号或需要批量操作时：

```bash
uv run python -m api.projects list
uv run python -m api.projects create --name "某某项目" --slug demo-2026
uv run python -m api.projects add-member --project-id <uuid> --username zhangsan --role writer
uv run python -m api.projects remove-member --project-id <uuid> --username zhangsan
uv run python -m api.projects list-documents --project-id <uuid>
uv run python -m api.projects backfill-documents --project-id <uuid>
```

**首次启用 ACL 前必须执行一次** `backfill-documents`：它扫描本地 `upload/parsed/` 与 MinIO `parsed/`，把已解析但没有 run 的文档注册到指定项目。否则这些历史文档在文档列表、检索和上传复用中不可见。已有 run 的文档已由迁移回填。

## 5. 各类入口的隔离规则

| 入口 | 规则 |
|---|---|
| 上传/解析 | 文档注册到当前项目后才入队；注册失败返回 503，不入队 |
| 文档库 | 只列出当前项目 `documents` 注册表内的文档 |
| 检索 | 只遍历当前项目允许的文档；显式传入项目外 `doc_id` 返回 404；Chroma 查询带 `doc_id` 过滤 |
| 任务 | job payload 增加 `project_id`；轮询先校验项目归属再返回状态 |
| run/大纲/章节/门禁/合规/一致性/配图 | 所有查询与修改都带 `project_id`；跨项目 ID 猜测返回 404 |
| 产物 | 文档产物与 run 产物先校验项目归属；每次下载写 `artifact.download` 审计 |
| 导出 | 普通导出要求全局 `bid_manager` + 项目 `manager`；`force=true` 仅全局 admin，且必须提供 `reason`，两次审计 |
| 公司库 | 保持全局共享，继续使用全局角色守卫（P0 不做公司项目化） |

`bid.docx` 已从 artifact allowlist 移除：DOCX 只能通过 `POST /api/bid-runs/{id}/export` 获取，从而受门禁、角色与审计约束。

## 6. 本地模式

`AUTH_ENABLED=false`（或没有数据库）时：

- `GET /api/projects` 返回一个合成的「默认项目」。
- 所有业务请求按项目 `manager` 处理，不需要 `X-Project-Id`。
- 文档注册表不可用，解析与 run 仍可正常使用。
- 该模式仅限本机开发，绝不可暴露到内网。

## 7. 测试与验收

自动化覆盖（`tests/test_projects.py`、`tests/test_project_isolation.py`）：

- 默认项目与成员角色映射；项目 CRUD 与最后管理员保护。
- 两用户两项目：列表、读取、修改、产物、任务、文档、检索均隔离；跨项目 404；缺 header 400；同名 `doc_id` 在不同项目互不覆盖。
- admin 跨项目访问；service 必须显式 header。
- `bid.docx` 不在 allowlist；普通导出需要项目 manager；force 需要 reason。

手工验收：

1. 管理员建项目 A/B，甲仅 A、乙仅 B。
2. 甲只能看到 A 的文档/run/任务/检索/产物；用 B 的 run_id/doc_id 请求得到 404。
3. 去掉 `X-Project-Id` 得到 400。
4. 甲用 `POST /api/bid-runs/{A 中 run}/export`：若甲全局是 writer，得到 403；把甲设为项目 manager 且全局 bid_manager 后可导出。

## 8. 当前边界

- 项目成员只能由管理员管理，没有项目 `manager` 自助加人。
- 公司库仍是组织级共享，不做项目隔离。
- 文档注册表不按分页返回；文档量很大时需要补分页。
- 解析缓存跨项目共享（内容相同、路径相同），授权在 API 层判定；直接访问 MinIO/文件系统不受本 ACL 保护，基础设施凭据仍需严格保管。
- 删除项目不会删除 MinIO/磁盘上的解析产物与 run 产物；如需回收，走运维清理流程。
