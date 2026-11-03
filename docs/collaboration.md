# 评审评论、指派与实时进度（P2 协作）

> 状态：已实现（2026-11-03）。覆盖 run 级评论、章节定位、@项目成员、处理人指派、解决/重开、单层回复、截止时间提醒与软删除，项目看板聚合待办/逾期/负责人负载，以及基于 fetch-SSE 的生成进度实时推送。站内通知与可选 SMTP/Webhook/企业微信已实现。

## 1. 数据模型

Alembic `0014` 新增 `bid_comments`；`0025` 增加 `parent_comment_id`、`due_at` 与 `due_at` 索引；`0026` 增加软删除字段与 `(project_id, deleted_at)` 索引：

| 字段 | 说明 |
|---|---|
| `id` | UUID 字符串 |
| `project_id` / `run_id` | 所属项目与投标 run；删除项目/run 时级联删除 |
| `parent_comment_id` | 可选，父评论 id；只允许一层回复 |
| `due_at` | 可选，截止时间（带时区）；用于到期提醒 |
| `deleted_at` / `deleted_by` / `deleted_by_name` | 软删除墓碑；列表、回复引用与到期扫描均过滤已删除评论 |
| `section_id` | 可选，指向大纲章节；`NULL` 表示针对整份标书 |
| `author_id` / `author_name` | 作者快照；用户删除后历史评论仍可读 |
| `body` | 正文，1–4000 字符，去除首尾空白 |
| `mentions` | JSONB，`[{user_id, display_name}]` 快照 |
| `assignee_id` / `assignee_name` | 处理人快照，可为空 |
| `status` | `open` / `resolved` |
| `resolved_by` / `resolved_by_name` / `resolved_at` | 解决人快照与时间 |
| `created_at` / `updated_at` | 时间戳 |

作者、处理人、解决人都不加外键：`AUTH_ENABLED=false` 的本地模式使用合成用户 `local`，且删除用户不应删除评审讨论。项目与 run 仍保留外键与级联。

## 2. API

所有接口要求认证并挂项目守卫；`run_id` 必须属于当前 `X-Project-Id`，跨项目一律 404。

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| `GET` | `/api/project-members` | 项目只读 | 当前项目的成员摘要（`user_id/username/display_name/role`），用于 @ 与指派选择；不返回全局用户列表 |
| `GET` | `/api/bid-runs/{run_id}/comments` | 项目只读 | 参数 `status=open\|resolved`、`section_id`、`limit`（1–500，默认 200），按时间倒序 |
| `POST` | `/api/bid-runs/{run_id}/comments` | 全局 review + 项目 review | `{body, section_id?, parent_comment_id?, due_at?, mentions?: [user_id], assignee_id?}`；提及/处理人必须是项目成员 |
| `PATCH` | `/api/bid-runs/{run_id}/comments/{comment_id}` | 全局 review + 项目 review | `{body?, status?, assignee_id?, due_at?}`；正文仅作者或 admin 可改，指派/解决由复核角色处理 |
| `DELETE` | `/api/bid-runs/{run_id}/comments/{comment_id}` | 全局 review + 项目 review | 软删除评论及其直接回复；仅作者或 admin 可删，返回 204 |
| `GET` | `/api/project-board` | 项目只读 | 当前项目的评论/逾期/run 状态聚合看板 |
| `GET` | `/api/bid-runs/{run_id}/events` | 项目只读 | SSE 实时进度，`text/event-stream` |

示例：

```bash
curl -X POST http://127.0.0.1:8001/api/bid-runs/<run_id>/comments \
  -H "Authorization: Bearer <token>" \
  -H "X-Project-Id: <project_id>" \
  -H "Content-Type: application/json" \
  -d '{"body":"请补充进度横道图","section_id":"narr-06-04","mentions":["<user_id>"],"assignee_id":"<user_id>"}'
```

约束与语义：

- `status=resolved` 记录解决人与时间；重新打开会清空这三个字段。
- `assignee_id: null` 表示取消指派；省略该字段表示不修改。
- `due_at: null` 表示清除截止时间；省略该字段表示不修改。前端用 `datetime-local` 输入，提交前转换为 UTC ISO 字符串。
- `parent_comment_id` 只能指向同一 run 的顶层评论；回复一条回复、或显式指定与父评论不同的章节返回 422。回复未指定章节时继承父评论章节。
- 回复创建成功后会通知父评论作者（`comment_reply`）；截止时间由每小时维护任务扫描，到期前 24 小时内通知处理人/作者（`comment_due`），按评论 + 到期时间去重，解决后不再提醒。
- 删除为软删除：写 `deleted_at/deleted_by` 保留审计线索；删除父评论会级联软删除其回复。删除后不再出现在评论列表、看板计数或到期扫描中，也不能再被回复/编辑；审计记录 `comment_id` 与 `reply_count`。
- 提及去重；`mentions` 最多 20 个。成员目录不可用（如本地无 PostgreSQL）时，不带提及/指派/回复目标的评论仍可创建；带提及/指派返回 503，回复仍可创建但不发回复通知。
- 审计只记录 `comment_id/section_id/assignee_id/status/parent_comment_id/due_at/changed_fields/mention_count` 等元数据，不记录评论正文。

## 3. 项目看板

`GET /api/project-board` 按当前 `X-Project-Id` 聚合协作状态，项目只读即可访问（viewer 可看）：

- `comments`：待处理、已解决、逾期、24 小时内到期、未指派数量；
- `runs`：run 总数、进行中（queued/generating/assembling）、已组装、暂停、失败、门禁阻断数量；
- `assignees`：按处理人聚合的待处理/已解决/逾期/24 小时内到期；`user_id=null` 表示未指派；
- `sections`：按章节聚合的待处理/逾期数量，标题来自当前 run 的大纲；
- `recent_comments`：最近待处理评论摘要（正文截断 160 字），逾期优先、其次按到期时间与创建时间排序，可直接打开对应 run。

统计在 PostgreSQL 中聚合，自动排除 `deleted_at IS NOT NULL` 的评论，不读取正文以外的 profile 或文档内容。前端「项目看板」每 30 秒自动刷新，也可手动刷新；所有角色可查看，写操作仍回到对应 run 的评论面板。

## 4. SSE 实时进度

`GET /api/bid-runs/{run_id}/events` 每 1.5 秒输出一个 `snapshot` 事件，内容包括：

- run 状态、阶段、百分比、消息、`gate_status`、`content_sha256`；
- 章节完成计数与每个章节的实时状态；
- Redis/Celery 任务视图（`stage/percent/message/error`），用于比数据库行更细的进度；
- `server_time` 与 `terminal`（run 是否已离开 queued/generating/assembling）。

前端不使用 `EventSource`（它无法携带 `Authorization`），而是用 `fetch` + `ReadableStream` 解析 SSE；token 只放在请求头，**不会进入 URL**。连接中断后 3 秒重连；`useBidRun` 的 2 秒轮询仍是事实来源与兜底，SSE 负责“实时”徽章、更快进度和章节状态。服务端单连接最长 30 分钟，期间每 15 秒发送 `: keep-alive`；`X-Accel-Buffering: no` 让 Nginx 不缓冲该响应。

```text
event: snapshot
data: {"run_id":"...","status":"generating","percent":40,...,"job":{"percent":42,...}}

: keep-alive
```

## 5. 权限与前端

- 只读角色（viewer）可查看评论、项目看板与实时进度，不能发表、解决、指派、编辑或删除。
- 评论 composer 支持章节下拉、处理人下拉、@ 成员多选、可选截止时间与「回复」引用；回复以平铺列表展示父评论摘要，作者可编辑或删除自己的正文，admin 可编辑/删除任意正文。
- 所有写操作仍由后端守卫与项目隔离兜底；前端隐藏按钮不构成授权。
- 处理人/提及只能来自 `GET /api/project-members`；该接口只返回当前项目成员摘要。

## 6. 尚未实现

- 多级线程、评论附件与评论恢复；当前只支持一层回复，删除为不可逆软删除。
- 事件追加表与 `Last-Event-ID` 断点续传；当前是快照流，断线重连后由下一帧恢复完整状态。
- 编辑租约（“某人正在编辑”提示）与看板自定义时间窗口。
