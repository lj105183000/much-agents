# 审计日志（P0-C）

> 状态：已实现（2026-10-24）。审计表只追加、不可通过 API 修改或删除，并用 SHA-256 哈希链提供篡改可见性。审计只记录元数据，不记录密码、token、`profile.json` 或正文。

## 1. 数据模型

Alembic `0005_audit_events.py` 创建 `audit_events`：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | bigint identity PK | 递增顺序 |
| `ts` | timestamptz | 服务端时间（微秒精度参与哈希） |
| `request_id` | varchar(64) | 请求关联；由 `X-Request-ID` 透传或自动生成 |
| `actor_id` / `actor_username` | varchar(36/64) | 可为空（登录失败、未认证请求） |
| `project_id` | varchar(36) | 全局事件为空 |
| `action` | varchar(64) | 见下文动作表 |
| `resource_type` / `resource_id` | varchar(32/128) | 资源标识 |
| `outcome` | varchar(16) | `success` / `failure` / `denied` |
| `ip` / `user_agent` | varchar(64/255) | 请求来源；代理场景由 Nginx 覆盖 `X-Forwarded-For` |
| `detail` | jsonb | 白名单元数据，单值最大 4000 字符 |
| `prev_hash` / `hash` | char(64) | SHA-256 哈希链 |

索引：`(project_id, ts)`、`(actor_id, ts)`、`(action, ts)`、`(request_id)`。

数据库层触发器：

- `trg_audit_events_append_only`：`UPDATE` / `DELETE` 直接抛异常。
- `trg_audit_events_no_truncate`：`TRUNCATE` 也被拒绝。
- 应用没有任何修改/删除审计的接口。删除只能由数据库超级用户临时移除触发器后执行，并应视为安全事件。

## 2. 哈希链

追加逻辑（`api/audit.py::AuditRepository.append`）：

1. 在同一事务内取 `pg_advisory_xact_lock(0x41554449)`，串行化并发追加。
2. 读取上一条记录的 `hash` 作为 `prev_hash`（首条为空字符串）。
3. 对规范化 JSON（`sort_keys=True`、无空格、`ensure_ascii=False`、时间戳固定为微秒 ISO-8601）计算：

   ```text
   hash = sha256(prev_hash + "\n" + canonical_json(event))
   ```

校验（`AuditService.verify_chain`）逐条检查 `prev_hash` 链接与重算哈希，返回 `{valid, checked, first_bad_id}`。若从中间区间开始校验，会先取区间前一条的 `hash` 作为期望值。

## 3. 请求关联

中间件 `install_request_context`：

- 读取入站 `X-Request-ID`；只接受 `[A-Za-z0-9._-]{8,64}`，否则生成新的 UUID hex。
- 写入 `request.state.request_id`，并在响应头返回 `X-Request-ID`。
- 所有 `POST/PUT/PATCH/DELETE` 如果路由没有显式写审计，会在响应后写一条 `http.request` 兜底事件（`detail={method,status}`），保证写请求至少留痕。
- 路由显式调用 `AuditService.record(...)` 后会置 `request.state.audit_recorded=True`，避免重复记录。
- 中间件不读取请求体。

Nginx 生产配置会覆盖 `X-Forwarded-For` 并传入 `X-Request-ID $request_id`；应用只在 `TRUSTED_PROXIES` 配置可信代理时读取转发头，避免伪造 IP。

## 4. 动作覆盖

| 动作 | 触发 |
|---|---|
| `auth.login` | 登录成功、失败、账号锁定、IP 限流拒绝 |
| `auth.logout` | 登出 |
| `auth.password.change` | 自助改密 |
| `user.create` / `user.update` / `user.password.reset` | 管理员用户管理 |
| `project.create` / `project.update` / `project.delete` | 项目管理 |
| `project.member.upsert` / `project.member.remove` | 项目成员变更 |
| `company.create` / `company.update` / `company.delete` | 公司库变更 |
| `document.upload` | 上传并创建解析任务 |
| `bid.legacy.create` | legacy `POST /api/bids` |
| `bid_run.create` / `bid_run.outline.patch` | run 创建、大纲修改 |
| `bid_run.generate` / `bid_run.resume` / `bid_run.cancel` | 生成生命周期 |
| `bid_run.assemble` | 组装与门禁 |
| `bid_run.section.edit` / `bid_run.section.regenerate` | 单节人工编辑/重写 |
| `bid_run.compliance.check` / `bid_run.fix_plan` / `bid_run.fix.apply` | 合规检查、修复方案、应用修复 |
| `bid_run.figures.regenerate` | 配图强制重建 |
| `bid_run.export` / `bid_run.export.force` | 普通导出、强制导出（含 reason） |
| `artifact.download` | 产物下载（GET；HEAD 不重复记录） |
| `http.request` | 未显式记录的其他写请求兜底 |

审计详情只接受 JSON 基本类型并截断；密码、token、Authorization、`profile.json` 内容和标书正文不应出现在 `detail` 中。

敏感操作（用户/角色/密码、项目与成员、公司删除、run 创建/生成/导出/强制导出、修复应用等）在路由层使用 `required=True`：数据库审计写入失败时请求返回 503，不会出现「操作成功但没有审计」的静默缺口。非敏感事件为 best-effort。

## 5. 查询与导出

仅管理员可访问；全局 `service` 账号也会被 `require_admin` 拒绝。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/audit?project_id=&actor=&action=&from=&to=&limit=&offset=` | 按时间倒序分页；`limit` 1–200，默认 100 |
| `GET` | `/api/audit/verify?from_id=&to_id=` | 校验整链或指定区间 |
| `GET` | `/api/audit/export.csv?...` | CSV 下载；最多 10000 行，`Cache-Control: no-store` |

- `actor` 同时匹配 `actor_id` 与 `actor_username`。
- `from` / `to` 为 ISO-8601 时间。
- CSV 对以 `=`、`+`、`-`、`@`、Tab、CR 开头的单元格加 `'` 前缀，防止 Excel 公式注入。
- 前端「审计日志」视图包含筛选、分页、链校验与 CSV 下载，CSV 通过带认证的 Blob 请求获取。

CLI 用于运维定期校验：

```bash
uv run python -m api.audit verify
uv run python -m api.audit verify --from-id 1000 --to-id 2000
```

退出码：0 完整，1 链损坏（打印首个异常 id），2 配置/参数错误，3 数据库不可用。

## 6. 保留与备份

- P0 不自动删除审计记录（append-only）；磁盘增长按实际事件量评估。
- 审计随 PostgreSQL 备份一起保留；`scripts/backup.sh` 的 `pg_dump -Fc` 已包含该表。
- 如需归档，必须在数据库层单独审批并记录安全事件；应用不提供删除入口。
- 恢复备份后建议立即执行一次 `verify`，确认链完整。

## 7. 已知限制

- 业务写入与审计追加不是同一数据库事务：`required=True` 的操作在审计失败时返回 503，但业务写入可能已经提交，形成「已生效、无审计」的窗口。运维应通过中间件兜底事件与日志排查。
- 哈希链只能发现篡改/删除，不能阻止拥有数据库超级用户权限的人重建整条链；更强需求需要外部 WORM/锚定。
- 所有追加共享一个 advisory lock；内网小团队足够，后续可按项目或时间分链。
- 审计查询没有全量导出以外的分析报表；长期趋势建议接入日志平台。
- 登录失败事件会记录尝试的用户名，但不会记录密码。
