# 反馈闭环、质量快照与运营看板（轻量数据底座）

> 状态：已实现（2026-11-02）。新增 `feedback_events`、`bid_quality_snapshots`（迁移 `0017`）与管理员「运营看板」。仍不引入 ClickHouse/Kafka/dbt/Airflow/独立数仓。

## 1. 为什么不做数据中台

当前是单机、私有化、单租户、小数据量，没有计费与专职数据团队。重型中台的运维与泄露面大于收益。继续使用：

```text
PostgreSQL 明细表 → SQL 聚合 → 管理页
MinIO（大对象） + Prometheus（时序）
```

升级触发条件（满足任意 2 项再评估）：`llm_call_log` > 2000 万行/50GB、`run_usage` > 100 万 run、每月 > 50 万 LLM 调用、> 3 个部署需合并报表、按客户计费、12 个月以上自由分析、2 名以上数据工程师、WORM/监管报表。

## 2. 反馈事件

### 2.1 数据

`feedback_events`：

| 字段 | 说明 |
|---|---|
| `project_id` / `run_id` | 级联删除 |
| `section_id` | 可为空（run 级反馈） |
| `user_id` / `user_name` | 作者快照 |
| `event_type` | `rating` / `adopt` / `reject` / `edit` / `rewrite` / `restore` |
| `score` | 1–5，仅评分 |
| `reason` | 可选原因，最多 1000 字 |
| `content_sha256` | 反馈针对的章节内容哈希 |
| `event_metadata` | JSONB（来源、上一状态等） |
| `created_at` | 时间 |

索引：`(run_id, section_id, created_at)`、`(project_id, created_at)`、`(event_type, created_at)`、`(user_id, created_at)`。

### 2.2 自动事件

- 人工编辑 → `edit`（正文哈希为新内容）
- 恢复版本 → `restore`
- 单节重写 → `rewrite`（正文哈希为重写前内容）

自动事件仅记录元数据，不影响主流程；写入失败只 warning。

### 2.3 API 与前端

| 方法 | 路径 | 权限 |
|---|---|---|
| `GET` | `/api/bid-runs/{run_id}/feedback?section_id=&limit=` | 项目只读 |
| `POST` | `/api/bid-runs/{run_id}/feedback` | 项目 review |

创建请求：`{section_id?, event_type: rating|adopt|reject, score?: 1-5, reason?: str, content_sha256?}`；`rating` 必须带 score；未提供哈希时用当前 final 正文计算。审计只记录 id/类型/章节/分数，不记录 reason。

前端章节弹窗新增「本节质量反馈」：1–5 评分 + 原因、采纳、需修改。

## 3. 质量快照

`bid_quality_snapshots` 在每次组装后写入，唯一约束 `(run_id, content_sha256)`，重复组装同一内容不重复记录：

- `gate_status` / `compliance_status` / `consistency_status`
- `requirements_coverage`（加权覆盖率）/ `citation_coverage`
- `model`、`revision`
- `summary` JSONB：门禁检查计数、合规/一致性/需求/引用摘要、警告数、页数上限

不记录正文、profile、证据原文；完整报告仍在 run 目录/MinIO。

## 4. 外部用量（MinerU / Embedding）

迁移 `0018` 新增 `external_usage`：

| 字段 | 说明 |
|---|---|
| `project_id` / `doc_id` / `job_id` | 来源关联；项目删除时置空 |
| `provider` / `operation` | `mineru/parse`、`zhipu/embedding` |
| `units` / `unit_type` | 页数或 token/text 数 |
| `cost_cny` | 可选；价格未配置时为 null |
| `status` / `latency_ms` / `usage_metadata` | 状态、耗时与模型/批次等元数据 |

- 解析任务与附件任务结束后，`execute_parse_job`/`execute_attach_job` 通过 `api/external_usage.py` 写入账本；Celery 与本地 runner 都覆盖。
- MinerU：仅当实际使用 `mineru` 解析/OCR 时记录页数；PyMuPDF 回退不计外部用量。
- Embedding：`ZhipuEmbedder` 从响应 `usage` 累计调用数、文本数、prompt/total tokens，写入 `ingest.json` 摘要并进入账本；缓存命中不重复计入。附件 PDF OCR 与附件 embedding 也计入。
- 计价：`MINERU_PRICE_PER_PAGE_CNY`、`EMBEDDING_PRICE_PER_1K_TOKENS_CNY`，0 表示只记录用量不算成本。
- 运营看板新增外部记录数、单位数、成本与按 provider 拆分。

## 5. Run 预算

```ini
BID_RUN_BUDGET_CENTS=500          # 500 分 = ¥5.00；0 关闭
BID_BUDGET_ENFORCEMENT=warn       # warn | stop
```

- 预算基于 `run_usage.cost_cny` 累计；价格未配置时 LLM 成本为 0，无法触发预算。
- `warn`：达到预算后继续生成，在 run warnings 中追加提示。
- `stop`：达到预算后停止继续调度新章节（已并发中的章节可完成），run 变为 `paused`，message/warnings 记录预算超限；`generate/resume/regenerate` 在预算仍超限时返回 409，需管理员调整预算或新建 run。
- 单节重写/续跑同样受预算约束；预算检查为 best-effort，账本写入失败只 warning。
- **预算告警**：run 与项目月度预算达到 80% 发 warning、100% 发 exceeded；收件人为项目 manager，走站内通知与可选 Webhook/SMTP。按 `(scope, run/项目+月份, level, budget)` 去重，调整预算后会重新触发；告警失败不影响生成。

**项目月度配额**：`projects.monthly_budget_cents`（迁移 `0019`，`NULL`/0 不限制），在「项目管理 → 编辑」中按元设置。`generate/resume/regenerate` 会累计当前自然月 `run_usage` + `external_usage` 成本，达到配额后拒绝新的生成/续跑（409）；已运行任务不受影响。运营看板项目表显示「本月成本 / 预算」。

## 6. 运营看板

`GET /api/analytics/overview?days=7|30|90`（仅 admin）：

- 运行：总数、状态/门禁分布、失败数、平均耗时
- 用量：LLM 调用、prompt/completion/total tokens、成本、重试、回退（来自 `run_usage`）
- 外部用量：MinerU/Embedding 记录数、页/token 单位、成本与 provider 拆分（来自 `external_usage`）
- 反馈：总数、类型分布、平均评分、Top 原因
- 质量：快照数、门禁分布、平均需求/引用覆盖率
- 项目 Top 10：runs、调用、门禁阻断、成本
- 最近 14 天：runs、门禁阻断、成本

前端左侧「运营看板」仅管理员可见，支持 7/30/90 天切换与刷新。查询全部走 PostgreSQL 聚合，不新增外部服务。

`GET /api/analytics/export.csv?days=7|30|90`（仅 admin）导出 UTF-8 BOM CSV，包含 Summary、Daily、Projects（含本月成本/预算）、Feedback、External、BudgetAlerts 分节；所有单元格做 CSV 公式注入前缀转义。前端「导出 CSV」按钮下载。运营看板同时展示最近预算告警历史（kind/title/body/时间/已读）。

`GET /api/bid-runs/{run_id}/usage`（项目只读）返回该 run 的 LLM 调用/token/成本/重试/耗时、run 预算与策略、项目本月成本/月度配额，以及该文档的 MinerU/Embedding 外部用量；前端「成本与预算」面板显示进度条与外部用量明细。

## 7. 隐私与边界

- 反馈 reason 可能含业务信息：仅管理员看板聚合展示 Top 原因；不进入审计 detail、日志或 Webhook。
- 快照只存摘要和哈希，不存正文/profile。
- 当前仍未实现：物化视图/预聚合、SQL 自由查询、按客户计费、配额历史与预算趋势图。
- 数据量增长后先加索引/物化视图/定时聚合；达到 0.2 节触发条件再评估重型数据平台。
