# 站内通知、Webhook、企业微信与 SMTP

> 状态：已实现（2026-11-03）。站内通知始终写入 PostgreSQL；Webhook、企业微信群机器人与 SMTP 是可选出站通道，失败只记日志，不影响评论、组装或审批主流程。

## 1. 事件与收件人

| 事件 | kind | 触发点 | 收件人 |
|---|---|---|---|
| 评论 @提及 | `comment_mention` | 创建评论 | 被提及的项目成员（排除作者） |
| 评论指派 | `comment_assigned` | 创建评论或修改处理人 | 新处理人（排除作者；处理人未变化不重复通知） |
| 评论回复 | `comment_reply` | 回复他人评论 | 父评论作者（排除回复者；仍须为项目成员） |
| 评论到期 | `comment_due` | 每小时扫描 `due_at` 在未来 24 小时内或已逾期的未解决评论 | 处理人优先，其次作者；都没有（或已不是项目成员）时通知项目 manager + reviewer |
| 标书已组装 | `run_assembled` | assemble 完成且门禁非 block、未要求审批 | 项目 manager + reviewer |
| 门禁阻断 | `gate_blocked` | assemble 完成且门禁为 block | 项目 manager + reviewer |
| 待审批 | `approval_pending` | assemble 完成、`BID_REQUIRE_APPROVAL=true`、门禁非 block | 项目 manager + reviewer |
| Run 预算预警 | `run_budget_warning` | 累计成本达到 run 预算 80% | 项目 manager |
| Run 预算超限 | `run_budget_exceeded` | 累计成本达到 run 预算 100% | 项目 manager |
| 项目月度预算预警 | `project_budget_warning` | 当前自然月成本达到项目月度配额 80% | 项目 manager |
| 项目月度预算超限 | `project_budget_exceeded` | 当前自然月成本达到项目月度配额 100% | 项目 manager |
| 资质即将到期 | `qualification_expiring` | 每日扫描 `QUALIFICATION_EXPIRY_DAYS` 内到期的资质 | 使用该公司的项目的 manager |

- 只通知启用中的项目成员；admin 若非项目成员不会收到 run 级通知（仍可在 UI 查看）。
- 评论和 run 通知都有 `dedupe_key`（唯一约束 `(user_id, dedupe_key)`）。同一评论的同一提及/指派/回复、同一内容哈希的同一 run 事件不会重复创建；重复 assemble 不会刷屏。
- 评论到期提醒按 `(comment_id, due_at, user_id)` 去重：每小时扫描一次，但同一条评论在每个到期时间只提醒每个收件人一次；修改 `due_at` 后会按新时间重新提醒，解决评论后不再提醒。
- 回复只支持一层：不能回复一条回复；回复默认继承父评论的章节，显式指定其他章节返回 422。
- 预算告警按 `(scope, run/项目+月份, level, 预算值)` 去重：同一 run 的 80%/100% 各一次；项目月度配额按自然月、level、预算值各一次；调整预算后会重新触发。
- 同一用户同时被 @ 和被指派会产生两条通知（类型不同），便于区分。

## 2. 数据与 API

Alembic `0015` 新增 `notifications`；`0025` 为 `users` 增加可空的唯一邮箱（`lower(email)` 唯一索引），并为 `bid_comments` 增加 `parent_comment_id`、`due_at` 与 `due_at` 索引：

- `project_id`、`user_id`、`kind`、`title`、`body`、`resource_type/id`、`run_id`、`doc_id`、`comment_id`、`section_id`、`actor_id/name`、`dedupe_key`、`read_at`、`created_at`。
- 索引：`(user_id, read_at, created_at)`、`(project_id, created_at)`；`project_id` 级联删除。
- `user_id` 不加外键：删除账号不抹掉历史通知；收件人只来自当前项目成员。

接口（全部需认证 + `X-Project-Id`；只返回当前用户自己的通知）：

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/notifications?unread_only=&limit=&offset=` | 列表 + 未读数 |
| `GET` | `/api/notifications/unread` | 未读数 |
| `POST` | `/api/notifications/{notification_id}/read` | 标记单条已读（幂等） |
| `POST` | `/api/notifications/read-all` | 当前项目全部已读，返回更新数 |

前端 Header 的通知铃铛每 25 秒轮询一次，并在窗口重新聚焦时刷新；点击通知会标记已读，若带 `run_id` 则直接打开对应标书 run。**未实现**通知 SSE，轮询足以覆盖内网小团队。

## 3. 通知偏好

迁移 `0020` 新增 `notification_preferences`（`user_id` 主键，`comment_enabled`/`run_enabled`/`budget_enabled`，默认 true）；迁移 `0024` 增加 `qualification_enabled`：

- `GET /api/notifications/preferences`、`PATCH /api/notifications/preferences`（当前登录用户；不要求项目头）。
- 关闭某类后，该类站内通知不再写入，也不会把该用户列入 Webhook/SMTP/企业微信的收件人；评论 @/指派/回复/到期、run 完成/门禁/审批、预算告警、资质到期分别对应 comment/run/budget/qualification。
- 偏好读取失败时 fail-open（继续按默认开启处理），避免数据库抖动导致漏告警。
- 前端通知铃铛内的齿轮按钮打开「通知设置」，四个开关即时保存。

## 4. Webhook

```ini
NOTIFY_WEBHOOK_URL=https://hooks.example.internal/much-agents
NOTIFY_WEBHOOK_SECRET=change-me
NOTIFY_WEBHOOK_TIMEOUT_SECONDS=5
```

- `POST` JSON，`Content-Type: application/json`，`X-Much-Agents-Event: comment.mention | comment.assigned | comment.reply | comment.due | run.assembled | run.gate_blocked | approval.pending | budget.run_warning | budget.run_exceeded | budget.project_warning | budget.project_exceeded | qualification.expiring`。
- 设置 secret 时附带 `X-Much-Agents-Signature: sha256=<hex(HMAC-SHA256(secret, raw_body))>`；接收方必须用原始字节验签。
- 载荷只包含事件、时间、项目/run/doc/comment/section id、操作人 id/姓名、门禁状态、内容哈希、收件人 id；预算事件额外包含 `budget_scope/budget_level/budget_cents/spent_cny`，资质事件额外包含 `company_id/qualification_id/days_left`。**不包含评论正文、profile、招标文件文本、token**。
- 同步发送放在后台线程（API 请求所在 event loop 用 `asyncio.to_thread`；Celery worker 直接发送），单次超时默认 5 秒；失败只记 warning，不重试、不阻断主流程。

## 5. 企业微信群机器人

```ini
NOTIFY_WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...
NOTIFY_WECOM_TIMEOUT_SECONDS=5
```

- 以 `msgtype=markdown` 发送事件元数据：标题、事件名、项目 id、时间、run id、门禁、剩余天数、操作人；**不发送评论正文、用户邮箱、profile、文档文本、token**。
- 由于群机器人是共享通道，`dispatch_many` 会按同一逻辑事件去重，多个收件人的同一条评论/到期提醒只发一条；关闭通知类别的用户不会生成事件，因此也不会触发该通道。
- 超时默认 5 秒；失败只记 warning，不重试、不阻断主流程。

## 6. SMTP

```ini
NOTIFY_EMAIL_ENABLED=true
SMTP_HOST=smtp.example.internal
SMTP_PORT=587
SMTP_USERNAME=much-agents
SMTP_PASSWORD=change-me
SMTP_FROM=much-agents@example.internal
SMTP_STARTTLS=true
SMTP_USE_SSL=false
NOTIFY_EMAIL_TO=bid-team@example.internal,ops@example.internal
```

- 管理员可在「用户管理」中为每个用户设置邮箱（`users.email`，迁移 `0025`；可空、大小写不敏感唯一）。SMTP 优先按收件人发送到其个人邮箱；没有邮箱的用户回退到全局 `NOTIFY_EMAIL_TO`，两者都为空时跳过邮件。
- 同一事件有多个收件人时逐人拆分发送，每封邮件只有一个 `To`，避免把用户邮箱暴露给其他收件人。
- 邮件主题为 `[Much Agents] <标题>`；正文只含事件、项目/run/doc id、门禁、操作人与时间，不含评论正文或文档内容。
- 使用 `smtplib`；`SMTP_USE_SSL=true` 时走 `SMTP_SSL`，否则按 `SMTP_STARTTLS` 决定是否 `STARTTLS`。
- 未配置 `SMTP_HOST`/`SMTP_FROM` 时视为未启用；`NOTIFY_EMAIL_TO` 现在是可选回退值。

## 7. 隐私与边界

- 出站载荷与审计、日志都不得包含：评论正文（Webhook/SMTP/企业微信）、投标人资料、身份证号/银行账号/信用代码、token、密码、邮箱以外的个人信息、profile 路径。
- 站内通知正文可包含评论摘要（最多 200 字），仅当前项目用户可见。
- 用户邮箱只用于按用户发送通知邮件；`GET /api/users` 仅管理员可见，审计只记录字段名不记录邮箱值。
- 当前没有：通知重试/DLQ、Webhook 白名单域名校验、项目看板。用户级邮箱、评论回复/截止时间、企业微信通道与通知偏好已实现。
- 测试：`tests/test_notifications.py` 覆盖评论提及/指派/回复/到期、run 组装/门禁、预算告警与去重、列表/已读 API、Webhook 签名与载荷、SMTP 元数据与用户邮箱、企业微信载荷与批量去重；E2E 覆盖铃铛与“全部已读”。
