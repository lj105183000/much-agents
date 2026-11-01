# Much Agents 线上生产就绪与评测路线图

> 状态：草案（2026-11-01）
> 适用形态：单机 / 私有化 / 单租户，Docker Compose 部署，小团队内网使用
> 关联文档：[产品化路线图](productization-roadmap.md)、[生产部署与运维](deployment.md)、[M1 生产验收报告](m1-acceptance-report.md)
> 本文只做诊断与分阶段规划，不代表已实施；功能现状以代码与 `CLAUDE.md` 为准。

## 实施进度（2026-11-01）

已落地：

- L0 指标模块与离线基线脚本：`src/bidding/metrics.py`、`scripts/baseline_metrics.py`；报告新增 `coverage.weighted`、`coverage.reviewer_gap`。
- 私有评测集构建/校验：`src/evaluation/dataset.py`、`scripts/eval_dataset.py`；真实标书写入 gitignored 的 `eval-data/`。
- LLM 调用账本与成本计量：`src/bidding/llm_usage.py`、迁移 `0006`、`llm_call_log`/`run_usage`；记录实际模型、token、延迟、重试、fallback、finish_reason。
- 单节重试上限与 regenerate 绕过修复：`BID_SECTION_MAX_ATTEMPTS`。
- Chroma readiness、JSON 日志、request_id 贯穿 API→Celery、`/api/metrics` 与核心指标：`src/observability/`、`api/metrics.py`。
- 可靠性：独立心跳、generation_token 条件写、Celery beat stale reaper、取消终态修复、Redis DLQ 与 `scripts/dlq.py`；迁移 `0007`。
- 备份：`age`/`gpg` 加密归档、rclone/rsync 异地、最后成功指标、systemd timer。
- 交付完整性：assembled run 写入 `content_sha256`，导出前校验；迁移 `0008`。
- 审批 API：`POST .../approve|reject`、`GET .../approval`，审批绑定内容哈希；`BID_REQUIRE_APPROVAL` 控制导出；迁移 `0009`。
- DOCX 质量：导出补 TOC 域、页脚 PAGE 域、表格重复表头；门禁消费 TOC/页码/字体/表格/DPI 检查。
- 供应链：MinIO/Chroma/Postgres/Redis 使用 M1 验收报告记录的 digest；新增 `scripts/check_image_pins.py` 与 CI Trivy/Syft SBOM。
- L2 评测：`scripts/eval_judge.py` 盲评五维评分；`scripts/eval_calibrate.py` 计算一致率与 Cohen's kappa；`.github/workflows/eval-nightly.yml` 在自托管 runner 上按开关运行生成 + 回归。
- 人工复核入口：`scripts/eval_review.py` 导出 `eval-data/reviews/<case>.md` 清单，供专家确认评分因素、候选点和关键事实。
- 监控：`monitoring` Compose profile 提供固定 digest 的 Prometheus + Alertmanager + Grafana、5 条告警规则、Grafana 数据源与 `Much Agents Overview` 看板；Grafana 仅绑定 `127.0.0.1:3000`，看板已实测加载（HTTP 200）。Prometheus 已识别 `alertmanager:9093`；Alertmanager 当前使用占位 webhook，需替换为实际通知渠道。
- 检索快照：每个章节的 query、mode、top_k、命中 chunk/score/页码/文本哈希写入 `retrieval_snapshots`（迁移 `0010`），不保存证据原文。
- Prompt registry：`prompt_registry`（迁移 `0011`）记录 prompt 文件哈希、默认模型/温度与 git sha，`scripts/seed_prompts.py` 可重复 upsert。

**真实 staged E2E 验收（2026-11-01）**：使用默认项目与 `6055594244948` 跑 1 节真实生成（`deepseek-flash`），run `22e47cd4c8864580832bb789b8557c03`。结果：3 次 LLM 调用、1 条 `run_usage`、1 条 `retrieval_snapshot`、1 条 `bid_approval`；`content_sha256` 已写入；导出 DOCX 171KB；DOCX 质量检查 TOC/页码/字体/表头/DPI 全部 pass；`/api/metrics` 含 LLM/run/queue 指标。gate 为 block，原因是示例 `bidder_profile.example.json` 产生 273 个占位符、14 个缺失资料、1 个 reviewer 高严重度覆盖缺口（非编造/脱敏）与 1 个合规阻断项；使用 admin override 审批后导出成功。该结果验证了“生成→组装→审批→导出→账本/快照/指标”的完整链路；正式资料下的门禁通过率已用真实公司 profile 复测（见下）。

**真实公司 profile 复测（2026-11-01）**：将 `eval-data/bid_docs` 中的中标/投标公司写入公司库后，用「北京华昊水利水电工程有限责任公司」（`company_id=a9a4193b346e49668568835581d90408`）对 `6055594244948` 重跑 1 节真实生成（run `d5b77b6770524e6b9c8478f8b58b9926`，`deepseek-flash`，company_id 载入 profile）。首次结果：3 次 LLM 调用、1 条 `run_usage`、1 条 `retrieval_snapshot`、1 条 `bid_approval`；占位符 248（示例 profile 273）、缺失资料 10（示例 14）、合规 10 pass / 9 warn / 2 fail / 1 blocked。gate 仍为 block，但定位为误报：`idx-00`「评标要素索引表」中的评审标准「符合性」被一致性台账当成投标人名称/质量要求的取值，产生 1 个 critical conflict 与 `company_identity` 阻断项。已修复为一致性台账跳过 `kind=index` 章节，并新增回归测试。修复后同一 run 重新 assemble：gate 为 `warn`（无 block），合规 12 pass / 9 warn / 0 fail / 0 blocked，一致性 12 consistent / 0 conflict；剩余 9 项均为真实缺失资料（电话、统一社会信用代码、法人/代理人、人员、财务、保证金、资质、248 个占位符）；审批后导出 DOCX 233,595 字节，DOCX 质量检查全 pass。注意重新 assemble 会重写 `figures/manifest.json` 的 `generated_at`，`content_sha256` 随之变化，需重新审批（fail closed）。

**完整 11 节 + Chroma E2E（2026-11-01）**：用同一真实公司 profile 对 `6055594244948` 跑完整 11 个叙事章节（run `47d44fe4c2c1491cae374abb8fbd4054`，`BID_USE_CHROMA=true`，`deepseek-flash`，并发 2）。首次执行在 33%（第 5/6 节）悬挂约 56 分钟后由脚本超时终止；改为 resume 后 11/11 节 final、0 failed。结果：加权覆盖率 **0.6387**（1 节 run 0.5714，L0 基线 0.5398），叙事覆盖率 **1.0**（13/13 因素 covered）；占位符 543、缺失资料 10；合规 10 pass / 10 warn / 1 fail / 1 blocked，一致性 10 consistent / 0 conflict / 7 missing / 4 not_found；7 条检索快照全部为 `semantic`（Chroma + 智谱 embedding），无关键词回退。gate 仍为 block：4 个章节存在真实的高危覆盖/编造问题（`narr-06-01/02/03` 为工程概况、重难点、进度与季节性施工覆盖不足；`narr-06-05` 引用了证据中不存在的通用合同条款 4.1.6 与京建发 2021 404 号），不是 `idx-00` 误报；审批 override 后导出 DOCX 337,433 字节，DOCX 质量检查全 pass。LLM 账本：report 记录 29 次调用，但 `llm_call_log`/`run_usage` 仅 17 次、80,784 tokens——usage 在全部章节完成后才批量落库，首次悬挂导致 `narr-06-01..04` 的 12 次调用丢失；`cost_cny=0` 是因为 `LLM_PRICE_TABLE` 未配置。后续需：按章节增量落 LLM usage（crash-safe）、配置价格表、补全公司资料并复核 4 个高危章节。

**合成数据完整 E2E（2026-11-01）**：创建公司库合成测试公司「北京华昊水利水电工程有限责任公司（合成测试）」（`company_id=35cf63a061664398a59699c3a7f620fe`；除公司名称/地址/项目经理/类似业绩来自公开中标公示外，其余字段均标注“（合成）”），对 `6055594244948` 跑完整 11 节 + Chroma（run `7da64a5ecf8b4afb8091f0dfdba135f5`）。结果：11/11 final；32 次 LLM 调用、185,443 tokens、0.303677 CNY（占位价格表）；11/11 检索快照为 semantic；缺失资料 0；占位符 544→485（安全替换 4 处 + 补全第 6.6 节后）；合规 20 pass / 1 warn / 0 fail / 0 blocked；一致性 16 consistent / 0 conflict；gate 为 `warn`（仅剩占位符警告），审批后导出 DOCX 343,278 字节，DOCX 质量检查全 pass。过程中修复：① 项目经理表格中的职称单元格被误当姓名（增加“工程师/建造师/经理/职称/职务/岗位”等非姓名 token 过滤）；② 第 6.6 节 writer 输出被 `max_tokens` 截断（2867 字，表格中途结束），手工替换为完整的相对日历天进度计划；③ 绝对阶段日期会被确定性检查判为编造，改用相对日历天。截断处理已改为 fail-closed：`MeteredChatClient` 记录 `truncated` 后抛出 `BidLLMTruncatedError`，writer 章节标记 failed，不再把截断文本当作 final；reviewer 截断按告警降级，reviser 截断保留原稿。该结果验证了合成资料下的完整生成→组装→门禁→审批→导出链路；真实投标仍需补全公司资料并处理 485 处占位符。

**截断修复后的 v2 复测（2026-11-01）**：用同一合成公司重跑完整 11 节 + Chroma（run `0bfce4bf5dce45ac98532ac7a53a75a9`）。结果：11/11 final、0 failed；5 次 reviser 截断全部被 `BidLLMTruncatedError` 识别并丢弃，保留 writer 原稿；32 次 LLM 调用、189,099 tokens、0.306861 CNY；11/11 检索快照为 semantic。初次 gate block（3 节高危），经 2 处安全替换 + 补全 6.6 进度计划与 6.7 资源配置后：gate `warn`（仅占位符），合规 20 pass / 1 warn / 0 fail / 0 blocked，一致性 17 consistent / 0 conflict，占位符 335→247，导出 DOCX 342,291 字节，DOCX 质量检查全 pass。说明：6.1 的合成证书编号会被确定性检查判为未授权，最终改为证书名称并引用资格审查资料。该轮验证了截断 fail-closed 与合成资料下的完整生成→组装→审批→导出链路。

**投标人资料摘要与 v3 复测（2026-11-01）**：新增 `BidderProfile.brief()`，向 writer/reviewer/reviser 注入非敏感摘要（公司名称/地址/电话、人员姓名/岗位/证书编号、业绩、财务、投标信息；排除身份证号、银行账号、统一社会信用代码与 `other`），并在提示词中约束“类似业绩不得作为本工程范围或设计依据”；隐私回归测试覆盖敏感字段排除。v3 完整 11 节 + Chroma（run `6f8d954a7d34432eb5248645fca569eb`）：11/11 final，31 次 LLM 调用、205,887 tokens、0.320227 CNY、11/11 检索快照 semantic。初次 gate block（3 节高危 + `计划开工日期 | 计划完成日期` 表头被误当日期值）；修复表头误报后 consistency 为 17 consistent / 0 conflict，剩余 block 为 narr-06-03/07/11 的内容缺口。占位符 385（v2 初始 335、v2 修复后 247）——资料摘要未降低总数，writer 生成了更多招标文件侧资源/进度表占位；下一步应把图纸、工程量清单等表格数据接入检索/证据链，而不是继续扩大资料摘要。另修复 revision 后陈旧 reviewer issue 仍参与门禁的问题：带引用的 issue 若在最终正文中不存在则丢弃（加回归测试）。v3 经手工修复 6.3 证据 4 引用、6.7 资源配置并刷新 6.11 陈旧 issue 后，gate 为 `warn`（仅占位符），合规 20 pass / 1 warn / 0 fail / 0 blocked，一致性 17 consistent / 0 conflict，导出 DOCX 340,239 字节，DOCX 质量检查全 pass。

**拟投入策略与 v4 复测（2026-11-01）**：调整写作策略，允许明确标注“拟投入/拟安排”的设备、劳动力、周转材料及相对阶段安排；公司、人员、证书、业绩、报价和绝对日期仍严格来源。v4 完整 11 节 + Chroma（run `f025c28d358748c3806cea00c0a98878`）：11/11 final，31 次 LLM 调用、202,073 tokens、0.313456 CNY、11/11 检索快照 semantic；占位符 385→250，DOCX 267,053 字节。初次 gate block 2 节（6.1 工程概况覆盖、6.3 管理体系证书日期/编号被误判），修复 `BidderProfile.facts()` 列表遍历（列表内证书日期/编号纳入白名单）并补全 6.1 框架性描述后：gate `warn`（仅占位符 + 1 张配图跳过），合规 20 pass / 1 warn / 0 fail / 0 blocked，一致性 17 consistent / 0 conflict，占位符 248，导出 DOCX 267,094 字节，DOCX 质量检查全 pass。该轮把“可拟投入”和“必须有来源”分开，显著减少设备/劳动力/进度类占位符；剩余 248 处主要是图纸、工程量清单等招标文件侧具体数据。经检查 `full.md`/`chunks.jsonl`/`content_list.json`：项目编码、管径、水压试验、回填压实、沟槽支护等关键词均为 0 命中，说明这些数据未进入解析文本（图纸/清单可能为图片或未随招标文件提供）；继续调大检索 top_k/证据预算不会补齐，需增加 OCR/附件解析或人工补充。

**附件证据链（2026-11-01）**：新增 `src/parsing/attachments.py` 与 `scripts/attach_evidence.py`，支持把工程量清单/补充资料以 PDF/CSV/TXT/MD 追加到已解析文档：chunk id `<doc_id>:att-<sha12>:<ordinal>`，写入 `chunks.jsonl`/`attachments.json`，以 `prune=False` upsert Chroma，minio 模式同步 `parsed/<doc_id>/` 对象；无文本层的 PDF 页面记录“需 OCR”警告，XLSX 未支持。用明确标注“合成测试附件”的 BOQ CSV 附加到 `6055594244948`：1 个 chunk 入库；查询“项目编码/管径/水压试验/回填压实度/沟槽支护”时附件为第 1 命中；检索时对含附件文档额外执行 BOQ 专项 query 并把附件命中前置合并。单节 `narr-06-02` 生成占位符 17→0，实际使用 DN200、0.8MPa、0.95、钢板桩等附件数据；该次 reviewer 返回非法 JSON（仅 warning），issue 为空。完整 11 节 + 附件的 v5（run `44220df27ec0465ca6a795a8a0371127`）：gate **warn（无 block）**、11/11 final、占位符 215（v4 250）、合规 19 pass / 2 warn / 0 fail / 0 blocked、一致性 15 consistent / 0 conflict、DOCX 333,365 字节、29 次 LLM 调用 / 215,210 tokens / 0.338338 CNY、11/11 检索快照 semantic；2 次 reviewer 返回非法 JSON 已降级为 warning。限制：扫描件 OCR 未自动执行，附件需人工确认来源，XLSX 需先导出 CSV/PDF。

**迁移与恢复演练（2026-11-01）**：本地 Docker Desktop 上将开发库 `0005 → 0009` 升级成功，用户/审计数据保持不变；临时库完成 `0009 → 0001 → 0009` 往返。使用生产 Compose 对 32 个 MinIO 对象和 95 条审计事件执行备份 → 删除测试数据 → `restore.sh` 恢复，恢复后 `Backup Drill Co` 与 `drill/restore-check.txt` 均存在，`/api/ready` 返回 200，审计链 95 条校验通过。演练后已删除测试数据并停止生产栈，命名卷与备份保留；仍需在干净 Linux 主机按真实数据量复测 RPO/RTO。

**基线结果（2026-11-01，方向性基线）**：3/4 份解析文档完整；3 份真实标书各跑 1 节（`--max-sections 1 --no-chroma`，纯关键词检索），加 1 个历史 2 节 run，共 4 个 run。平均加权覆盖率 0.5398；55 因素标书的叙事覆盖率约 0.76–0.80（13 个非模板因素），32 因素标书仅 1 个非模板因素（叙事覆盖率 1.0 但样本过窄），1 因素标书没有非模板因素；占位符密度约 20/千字，加权审查问题约 1.2/节。该基线可作趋势对照，但不代表完整 11 节 + Chroma 语义检索的生产质量。原始报告在 gitignored 的 `upload/baseline/latest.md`。回归比较已跑：`ok=True`，3/3 个案例有 run，加权覆盖率 0.525 vs 基线 0.5398（下降 0.0148，未超过 0.02 阈值）。

仍待实施（按优先级）：

- force override 的角色细化（force 导出仍仅限 admin；审批 override 当前允许评审角色）。
- 启用 Cosign 签名（CI 已提供可选步骤，需配置 `CI_SIGN_IMAGES`、`COSIGN_PRIVATE_KEY`/`COSIGN_PASSWORD`）。
- 将 Alertmanager 的占位 webhook 替换为实际 webhook/SMTP 配置。
- L1 人工复核与 L2 judge 的人工标注校准（judge、校准工具、复核清单与夜间 workflow 骨架已落地）。
- MinerU/Embedding 配额、PITR。

## 0. 摘要

### 0.1 三句话结论

1. 系统已经具备“能跑完一单”的工程骨架，但还不具备“敢对外承诺质量与 SLA”的生产能力。最大风险不是功能少，而是**质量不可测、成本不可见、故障不可见、失败不可自动恢复**。
2. **现在不建重型数据中台，建轻量数据底座**：PostgreSQL 明细表 + MinIO 对象快照 + Prometheus 指标 + SQL 视图，覆盖成本、质量、反馈、审批与检索溯源；等数据量、计费或团队规模出现明确信号再升级。
3. 未来 3 个月优先做“止血 + 可见性 + 可恢复 + 质量基线”，6 个月做“评测闭环 + 交付质量”，12 个月才讨论规模化与可选数据中台。单机形态下不追求 HA，不承诺 99.9%。

### 0.2 现在要不要数据中台

**结论：不建重型数据中台，建轻量数据底座。** 不建议现在引入 ClickHouse、Kafka、dbt、Airflow、特征平台、独立数仓或 BI 平台。

| 判断维度 | 现状 | 结论 |
|---|---|---|
| 数据量 | 本机仅 3 份真实标书，55/32/1 个评标因素 | PostgreSQL 单表完全足够 |
| 客户形态 | 单机、私有化、单租户，每个客户独立部署 | 不需要多租户分析模型 |
| 计费需求 | 当前无按 token/项目计费、无客户账单 | 不需要财务级明细架构 |
| 团队规模 | 无专职数据/分析工程师 | 重型平台运维成本大于收益 |
| 当前瓶颈 | 质量、成本、可靠性、可观测性 | 不是“缺数仓”，而是“缺埋点与评测” |
| 数据敏感性 | 标书正文、投标人资料、私有 profile | 集中式数据平台反而扩大泄露面 |

**升级触发条件（满足任意 2 项再评估）**：

| 触发维度 | 建议阈值 |
|---|---|
| 明细数据量 | `llm_call_log` 超过 2,000 万行或 50 GB；`run_usage` 超过 100 万 run |
| 调用频率 | 每月超过 50 万次 LLM 调用，或每天超过 200 次评测运行 |
| 部署数量 | 超过 3 个生产部署需要统一横向对比/合并报表 |
| 计费需求 | 需要按客户/项目出具账单、分摊成本、做 chargeback |
| 分析需求 | 需要跨 12 个月以上自由 SQL、秒级交互分析，或实时（<1 分钟）跨 run 监控 |
| 团队规模 | 出现 2 名以上专职数据/分析工程师，或 5 名以上固定报表消费者 |
| 合规要求 | 需要 WORM 归档、长期审计、监管报表或外部 BI 接入 |

达到以上条件前，升级到数据中台属于过度设计。建议在 6 个月和 12 个月各做一次“数据平台决策记录”，明确继续不建或启动的条件。

### 0.3 如果只做 5 件事

| 序号 | 事项 | 为什么是第一位 | 3 个月验收 |
|---|---|---|---|
| 1 | LLM 调用明细 + 成本/预算 + 修复 regenerate 绕过 | 当前不知道花了多少钱，且单节重写可绕过 attempt 上限反复烧钱 | 100% LLM 调用有 model/token/成本；预算到 100% 阻断；重写不能重置 run attempt |
| 2 | stale reaper + generation_token 条件写 + 定期心跳 + 取消语义 | worker 崩溃会留下僵尸 run，旧 worker 可能覆盖新结果 | 崩溃后 2×stale 内自动 paused；旧 token 写入失败；取消后 job 不得显示 succeeded |
| 3 | `/metrics` + 结构化日志 + request_id 贯穿 API→Celery + Chroma readiness | 现在故障只能靠猜，无法定位一次 run 的完整链路 | 10 个核心指标可抓取；一条 run 的 API/worker 日志可用 request_id 串起；Chroma 故障可见 |
| 4 | L0 确定性评测 + 真实加权覆盖率 + report 溯源字段 | “覆盖率”目前只判断章节是否写完，不是质量指标 | 加权覆盖率、reviewer 覆盖点、model/prompt/git sha 进入 report 和 CI |
| 5 | 备份自动化/加密/异地 + 门禁强制导出留痕 | 现在门禁可被 force 绕过，备份无调度无加密 | 每日加密备份 + 异地副本 + 恢复演练；force 有理由/审计/失效机制 |

> 这 5 件事不做，后面所有质量优化都无法证明有效，也无法安全对外承诺。

## 1. 范围与判断基线

### 1.1 目标形态

- **单机 / 私有化 / 单租户**：一个客户一套 Compose 栈，不做 SaaS 多租户改造。
- **异步为主**：标书生成是长任务，SLA 应定义“受理时延 + 完成时延 + 失败率”，不定义单次同步请求的严格时延。
- **人在环上**：AI 生成 + 确定性检查 + 人工审批；不承诺模型输出可直接投标。
- **数据边界不倒退**：`profile.json` 永不进入 artifact allowlist、日志、指标标签、审计 detail、Redis/Celery payload 或下载 URL；新方案同样遵守。
- **AGPL-3.0 合规**：新增监控、评测、运维脚本均属于源码；不引入与 AGPL 不兼容的依赖；新增依赖更新 `THIRD_PARTY_NOTICES.md`，继续保留源码入口。

### 1.2 成熟度判断

| 维度 | 现状 | 判断 | 主要缺口 |
|---|---|---|---|
| 安全与部署 | 非 root、只读根文件系统、TLS、CSP、限流、生产 fail-fast、RBAC/ACL/审计 | 强 | 镜像未 pin、无 SBOM/签名、恢复未在真实 Linux 演练 |
| 确定性质量链 | 21 项合规、一致性台账、门禁、修复方案、产物落盘 | 强 | 覆盖率是“写完判断”，未消费 reviewer 覆盖点，未按分值加权 |
| 质量测量 | 无金标准、无评测集、测试全 mock | 弱 | 无法判断 prompt/model 变更是否变好 |
| 成本治理 | 无 token/成本明细、无预算、无配额 | 很弱 | 只能事后猜，无法阻止超支 |
| 可观测性 | 无指标、无结构化日志、无 request_id 透传 | 很弱 | 故障不可见、不可定位 |
| 可靠性 | 失败章节隔离、resume、acks_late、generation_token 部分实现 | 中 | 无 beat/reaper、心跳稀疏、旧 worker 可覆盖、取消语义错误 |
| 交付质量 | 门禁 + DOCX 导出 | 中 | DOCX 无 TOC 域/页码/DPI/表格跨页检查，审批与版本缺失 |
| 数据底座 | 原始表 + 每 run 落盘 report | 弱 | 无跨 run 聚合、无 LLM 明细、无版本历史、无反馈 |

### 1.3 SLA 边界

**可承诺**：

- API 可用性：月度 99.5%，不含计划维护窗口。
- 异步任务受理：API 接受请求并返回 `job_id` 的成功率 ≥ 99%。
- 失败可恢复：worker 崩溃后 run 可自动/半自动恢复，不丢已完成章节。
- 数据恢复：RPO/RTO 按备份方案明确承诺。

**不可承诺**：

- 单次 LLM 生成时延的硬上限。
- 生成内容的绝对正确性、法律合规性、中标率。
- 单机形态下的 99.9% 可用性或零停机升级。
- Chroma/云 LLM/MinerU 外部依赖故障时的同等质量输出。

## 2. 现状诊断

### 2.1 已具备的强项

- 单机生产拓扑：`docker-compose.prod.yml`（Nginx TLS、Postgres、Redis、MinIO、Chroma、API、parse/bid worker），非 root、只读根文件系统、健康检查、日志轮转。
- 认证/RBAC/项目 ACL/append-only 审计哈希链/备份恢复脚本均已实现。
- 确定性质量链：21 项合规（`src/bidding/compliance.py`）、跨章节一致性台账（`src/bidding/consistency.py`）、门禁（`src/bidding/gate.py`）、修复方案（`src/bidding/fix_plan.py`）。
- 每节草稿、审查、修订、终稿持久化在 `bid_section_states`；run/大纲/节状态在 PostgreSQL；产物在 MinIO。
- 46 个测试文件约 400 个测试，全部 mock 网络；CI 覆盖 ruff/pytest/迁移/前端/Compose/安全扫描。
- 失败章节隔离、章节级检查点、resume、generation_token + heartbeat、Celery `acks_late`。

### 2.2 关键缺口（按风险排序）

| 维度 | 现状与证据 | 风险 |
|---|---|---|
| 评测 | 无评测集/金标准；`tests/` 全 mock；无 `tests/eval`；CI 不跑真实模型 | prompt/model 变更无法量化，模型升级只能上线后暴露 |
| 覆盖率语义 | `src/bidding/assemble.py:218-280` 只判断映射章节是否写完；`src/bidding/staged_output.py:69-81` 重建 review 时丢失 `missing_points/covered_points`；`compliance.py:456-482` 不按分值加权 | “覆盖”不等于“响应评分点”，高价值评分点可能假覆盖 |
| 成本 | `src/bidding/llm.py:95-165` 不读 `response.usage`；`MeteredChatClient`（`:178-187`）未接线；`src/bidding/staged.py:342` 的 `llm_calls` 是估算值 | 不知花多少钱，无法控预算 |
| 绕过次数上限 | `api/bid_runs.py:1286-1297` 的 regenerate 把 `attempt` 重置为 0 | 可反复单节重写，成本失控 |
| 可观测性 | 无 Prometheus/OTel/Sentry；日志纯文本；request_id 不进 Celery；`api/routes.py:361-405` 的 `/api/ready` 不查 Chroma | 故障不可见、不可定位；Chroma 故障时 ready 仍可能 200 |
| 僵尸 run | 无 Celery beat/stale reaper；`api/bid_run_repo.py:579-611` 只在人工 resume 时恢复；心跳只在每节完成时更新 | worker 崩溃后 run 长期停在 generating |
| 旧 worker 覆盖 | `api/bid_run_repo.py:513-553` 的 `update_section` 无 generation_token 条件 | stale 恢复后旧 worker 仍可能写入/覆盖 |
| 取消语义 | `api/staged_bids.py:343-420` 取消后仍可能 `mark_succeeded`；进行中的 LLM 调用不会中止 | 任务状态误导，无法判断是否真正停止 |
| 备份 | 备份/恢复脚本存在，但无自动调度、无加密、无异地、无 PITR；只在 Windows 合成数据演练过 | 无法承诺 RPO/RTO，主机故障可能丢数据 |
| 交付锁 | 门禁可被 `force` 绕过；无审批/内容哈希；`bid.md`/`sections/*.md` 可直接下载 | 低质量标书可直接交付 |
| 版本/反馈 | 人工编辑覆盖 `final_markdown`；无 `bid_section_versions`；无反馈/采纳入口；编辑/重写会删除 gate/compliance/consistency/report | 无法构造“修改前→修改后”样本，无法衡量真实采纳 |
| 模型调用 | 两个重复的 DeepSeek 客户端；`llm.py:127-129` 静默 fallback 到 `deepseek-chat`；不检查 `finish_reason` | 实际模型不可知；截断内容静默返回 |
| 检索 | Chroma 单集合；embedding 模型/维度不入集合版本；reviewer 只看前 3 条证据（`src/bidding/prompts.py:153`） | 检索不可复现；审查与完整证据不一致 |
| 供应链 | MinIO/Chroma/Postgres/Redis 已固定 digest；CI 有 Trivy/Syft，Cosign 签名待接入 | 供应链安全仍需发布流程签名 |

### 2.3 单机 Demo 可接受、生产不可接受的边界

| 单机 Demo 可接受 | 生产不可接受（或必须配套补偿） |
|---|---|
| 单机单副本、无资源限制 | 无监控告警、无集中日志、无业务指标 |
| 手动备份、无加密/异地 | 无自动备份调度、无 PITR、无恢复演练 |
| 只有 `/api/health` 与 `docker compose logs` | 故障只能等用户报障 |
| 无 token 成本统计、管理员人工控制使用 | 无预算/配额，单个项目可烧穿成本 |
| Chroma 单实例、单集合 | Chroma 是启动硬依赖但不在 readiness；运行故障不可见 |
| 会话 token 存 localStorage，靠 HTTPS/CSP 缓解 | 无 MFA/OIDC、密钥只在 env 文件 |
| Electron `file://` + 本机 API | 正式桌面分发需要签名/自动更新/错误上报 |
| 公司库全局共享、解析缓存跨项目共享 | 若对外多租户，需要存储层隔离 |

## 3. 轻量数据底座（替代数据中台）

### 3.1 数据流

```text
API/Worker ──> PostgreSQL（明细：llm_call_log / run_usage / versions / feedback / approvals）
     │              └── SQL 视图/物化视图 ──> Grafana / 管理页
     ├──> MinIO（大对象：完整 report、检索 trace、评测报告、DOCX 检查结果）
     └──> Prometheus（时序：请求、队列、失败、延迟、成本、备份年龄）
```

### 3.2 建议新增表（全部 additive）

| 表 | 目的 | 关键字段 | 阶段 |
|---|---|---|---|
| `prompt_registry` | prompt 版本/哈希 | `id/kind/version/template_sha256/template_path/model_default/temperature_default/git_sha/deprecated_at` | P0 |
| `llm_call_log` | 每次调用明细 | `request_id/project_id/run_id/section_id/job_id/provider/model_requested/model_actual/operation/prompt_sha256/call_fingerprint/temperature/max_tokens/json_mode/prompt_tokens/completion_tokens/cost_cny/latency_ms/retry_count/fallback/finish_reason/status/error_type/ts` | P0 |
| `run_usage` | 每 run 聚合 | `run_id(PK)/project_id/llm_calls/tokens/cost_cny/retries/fallback_calls/duration_ms/updated_at` | P0 |
| `bid_section_versions` | 章节历史 | `run_id/section_id/version/content_sha256/content_markdown/source(generated/revision/manual/fix/rewrite/restore)/editor_id/edit_reason/issues/warnings/created_at` | P0 |
| `bid_quality_snapshots` | 历史质量信号 | `run_id/revision/content_sha256/gate_status/compliance_status/consistency_status/report_summary JSONB/artifact_key/prompt_registry_id/model/created_at` | P0 |
| `feedback_events` | 反馈/采纳/编辑原因 | `run_id/section_id/user_id/event_type(rating/adopt/edit/rewrite/reject/comment)/score/reason/content_sha256/metadata/created_at` | P1 |
| `bid_approvals` | 审批与内容绑定 | `run_id/status/content_sha256/gate_status/approved_by/approved_at/reason/override/expires_at` | P1 |
| `retrieval_snapshots` | 检索可追溯 | `run_id/section_id/query_text/query_sha256/mode/top_k/hits JSONB/evidence_chars/trace_key/created_at` | P1 |
| `external_usage` | MinerU/Embedding 用量 | `provider/operation/project_id/doc_id/job_id/units/unit_type/cost_cny/status/latency_ms/created_at` | P1 |

### 3.3 迁移建议

现有迁移到 `0005`。建议新增：

| 迁移 | 内容 | 阶段 |
|---|---|---|
| `0006_prompt_usage` | `prompt_registry`、`llm_call_log`、`run_usage` | P0 |
| `0007_section_versions_quality` | `bid_section_versions`、`bid_quality_snapshots`；`bid_runs.content_sha256` | P0 |
| `0008_approvals_feedback` | `bid_approvals`、`feedback_events`；`bid_runs.approval_status/approved_sha256/approved_at` | P1 |
| `0009_retrieval_external_usage` | `retrieval_snapshots`、`external_usage` | P1 |
| `0010_reliability_lease` | `bid_section_states.generation_token`、`last_heartbeat_at` | P0（可提前合并到 0006） |

迁移验收：`alembic upgrade head → downgrade 0005 → upgrade head` 均通过，CI 覆盖；新表不影响现有读写；业务数据回填用独立、可重跑、可审计脚本，不写在迁移里。

### 3.4 复用现有能力与暂不做

**先复用**：

- `bid_runs.settings` JSONB 先扩展 `temperature/max_tokens/prompt_registry_id/git_sha`，不必新表。
- `bid_section_states` 继续存当前正文；`bid_section_versions` 只做历史。
- 完整 `report.json`、`compliance.json`、`consistency.json`、`gate.json` 继续落盘/MinIO；DB 只存摘要和 key。
- 完整检索证据 trace 放 MinIO 私有对象；DB 只存索引。
- 审批/反馈先做最小表，不引入工作流引擎。

**暂不做**：

- 不建独立指标库、宽表集市、特征平台。
- 不做跨客户数据合并；多个部署各自独立。
- 不做实时流式分析；夜间批处理足够。
- 不做完整实验管理平台；评测报告 + git sha + prompt hash 足够。

### 3.5 隐私与 AGPL 约束

- `profile.json` 永不进入新表、日志、指标标签、trace、评测报告或审批 reason。
- `llm_call_log` 不记录 prompt/response 正文，只记录哈希和 token；`bid_section_versions.content_markdown` 是敏感正文，只允许 DB 访问，不进入 artifact allowlist/日志/指标。
- 结构化日志只记录 `request_id/job_id/run_id/project_id/section_id` 等元数据，不记录正文、Authorization、Cookie、profile 字段。
- 评测数据中的真实标书受版权/隐私约束，放私有目录或独立私有仓库；公开仓库只放合成样例和 schema。
- 新增依赖（如 `prometheus-client`、JSON 日志库）需兼容 AGPL，并更新 `THIRD_PARTY_NOTICES.md`；网络服务继续提供对应源码入口。
- 新管理/审批接口必须走现有 RBAC/项目 ACL；不得新增匿名访问。

## 4. 分层评测体系

### 4.1 总体结构

| 层级 | 目的 | 数据 | 运行位置 | 是否阻断合并 |
|---|---|---|---|---|
| L0 确定性指标 | 无模型也能量化质量与回归 | 合成 fixture + 现有 report/compliance/consistency/gate | CI 每次提交 | 是 |
| L1 离线评测集 | 用真实历史标书做小样本回归 | 5–10 份脱敏标书 + 标注 | 夜间/发布前/私有环境 | 发布阻断，PR 可先告警 |
| L2 LLM-as-judge | 批量判断内容质量 | L1 的章节 + judge rubric | 夜间/变更评估 | 校准后作为发布阻断 |
| L3 人工抽检 | 无人工评分基础下的冷启动与校准 | 分层抽样章节 + 双人复核 | 每周/每迭代 | 校准和发布签字 |

原则：**L0 先上，L1 再补，L2 校准后使用，L3 持续校准。** 不要一开始就追求全自动评分。

### 4.2 评测集设计

**规模：5–10 份脱敏历史标书。** 现有 3 份（55/32/1 个评标因素）先纳入，优先 55 和 32 的两份；1 个因素的那份保留为“稀疏抽取”回归样本。后续从真实历史标书补 2–7 份，覆盖不同行业、评分结构、页数。

**每份样本至少包含**：

- 脱敏后的 `extraction.json`、`toc.json`、`chunks.jsonl`、`full.md`。
- 评标因素表：`category`、`factor`、`max_score`、`scoring_criteria`。
- 关键事实表：项目名称、编号、标段、工期、投标有效期、保证金、项目经理、证书、财务、业绩等。
- 必须覆盖点：按评分因素拆成 3–8 条可判断的要点，而不是完整范文。
- 参考大纲：章节标题、评分因素映射。
- 可选人工样本：20–30 节，标注 1–5 分与错误类型。

**冷启动标注策略（无人工评分基础）**：

- 不让业务专家打 100 分；先标“必须覆盖点是否出现”“事实是否正确/有无编造”“是否前后一致”。
- 用错误类型而不是总分：`fabrication`、`missing_point`、`inconsistency`、`format`、`unsupported_claim`。
- 每份样本标注 2 人交叉复核 20%；记录分歧，不追求完全一致。
- 评测数据放私有目录或独立私有仓库；仓库只保留 schema、脚本和合成样例。

### 4.3 指标定义

| 指标 | 定义/公式 | 数据来源 | 用途 |
|---|---|---|---|
| 评分点加权覆盖率 | `Σ(max_score_i × status_weight_i) / Σ(max_score_i)`；covered=1.0，partial=0.5，template=0.5，missing=0；`max_score` 为空时用 1.0 或同类别中位数 | `report.coverage`、outline 映射、reviewer `missing_points/covered_points` | L0 核心质量指标 |
| 评分点数量覆盖率 | `covered_count / total_factors` | 同上 | 辅助观察，不能替代加权 |
| 事实一致率 | `pass / (pass + conflict + missing + unverified)` | consistency ledger | 事实一致性 |
| 事实可核验率 | `pass / total_facts` | consistency ledger | 观察“未提及”而非“冲突” |
| 占位符密度 | `placeholder_count / max(1, chars/1000)` | report.placeholders | 未完成度 |
| 审查问题密度 | `Σ severity_weight(issue) / sections`；high=3，medium=2，low=1 | report.issues | 审查负担 |
| 门禁通过率 | `gate.status == pass` 的 assembled run / assembled run | gate.json / bid_runs | 交付阻塞率 |
| 人工编辑率 | 有 manual 版本事件的章节 / final 章节 | `bid_section_versions` | 生成可用性 |
| 重写率 | regenerate 事件数 / final 章节数 | feedback/events + versions | 返工成本 |
| 成本/千字 | `cost_cny / max(1, output_chars/1000)` | `run_usage` / `llm_call_log` | 成本效率 |
| 时延 P50/P95 | run 从 queued 到 assembled；section 从 writing 到 final | `run_usage` / section states | 交付时延 |
| 失败率 | failed run / started run；failed section / selected section | bid_runs / section states | 稳定性 |
| 模型 fallback 率 | `fallback=true` 调用 / 总调用 | `llm_call_log` | 静默降级告警 |

> `status_weight` 是产品口径，必须在第一次评测基线评审时冻结，后续变更需版本化。

### 4.4 L2：LLM-as-judge

- **先校准，再使用。** 用 L3 人工样本校准 judge；judge 与生成模型尽量不同；同模型时需明确相关性偏差。
- Rubric：相关性、证据支撑、覆盖完整性、结构/格式、事实编造惩罚；每项 1–5 分，最后给错误类型。
- 盲评：隐藏模型名、prompt 版本、生成时间；随机顺序；temperature=0；多次采样取多数。
- 校准指标：与人工的一致率、Cohen's kappa、Krippendorff's alpha；低于阈值不得作为发布门禁，只能作为观察信号。
- judge prompt 必须进 prompt registry，记录 model、prompt hash、rubric version。

### 4.5 L3：人工抽检

- 每周 20–30 节，按章节类型、评分因素、模型版本分层抽样。
- 每节 10–15 分钟；双人复核 20%；记录耗时，用于估算人工成本。
- 输出：错误类型分布、典型失败案例、judge 校准样本、下一轮 prompt 改进建议。
- 在无人工评分基础的情况下，这是唯一可信的校准来源；不要用 L2 替代 L3。

### 4.6 回归机制

**三层回归**：

1. **PR 回归（每次提交）**：L0 + 合成 fixture + 脱敏 replay cassette；不调用真实模型；必须通过。
2. **夜间回归（每天）**：L1 小样本 × 2–3 个章节 × 真实模型；成本上限固定；生成评测报告到 MinIO，失败发告警。
3. **发布回归（每次发布）**：L1 全量 + L2 judge + L3 抽检签字；对比基线，超过阈值阻断发布。

**阈值建议**：

- 第一次基线只记录，不阻断。
- 第二次起启用相对阈值：加权覆盖率下降 >2pp、事实一致率下降 >1pp、占位符密度上升 >20%、成本/千字上升 >20%、P95 时延上升 >30%、失败率上升 >1pp，任一触发告警/阻断。
- 同时设置绝对下限：加权覆盖率 ≥0.80、事实一致率 ≥0.95、失败率 ≤2%、高 severity 问题密度 ≤0.2/节。绝对下限在积累 10 次以上基线后调整。
- 小样本波动大，夜间回归连续 2 次异常才阻断发布；单次异常先告警。

**prompt/model 变更流程**：

- 修改 `src/bidding/prompts.py`、`src/bidding/llm.py`、`src/bidding/agents.py`、模型/温度配置时，PR 必须附 `eval_report.json`。
- CI 检测变更路径，缺少评测报告则失败。
- 报告至少包含：变更说明、baseline vs candidate、L1 指标、成本变化、失败案例、是否建议合并。

## 5. 可观测性与成本治理

### 5.1 最小可用方案

推荐组合：**Prometheus + Alertmanager + 结构化 JSON 日志 + 简单看板**。暂不上 OpenTelemetry Collector、Jaeger/Tempo、Sentry 全套 APM。

- API 暴露 `/metrics`，只在内网/受控路径可访问，不经公网 Nginx 暴露。
- Prometheus + Grafana 以 `monitoring` Compose profile 运行；Grafana 仅绑定 `127.0.0.1:3000`。告警规则由 Prometheus 评估，Alertmanager/邮件/企业微信/钉钉通知渠道仍需按环境接入。
- 日志统一 JSON 到 stdout，由 Docker `json-file` 收集；单机不强制上 Loki/ELK，必要时再加。
- 关键业务指标同时写 Prometheus 和 PostgreSQL：Prometheus 用于告警，PostgreSQL 用于跨 run 分析。

### 5.2 必须覆盖的指标

| 指标 | 类型 | 标签 | 用途 |
|---|---|---|---|
| `much_agents_http_requests_total` | counter | method/route/status | 请求量与错误率 |
| `much_agents_http_request_duration_seconds` | histogram | method/route | API P50/P95 |
| `much_agents_runs_total` | counter | status | run 成功/失败/取消 |
| `much_agents_run_duration_seconds` | histogram | status | 单标书完成时延 |
| `much_agents_section_failures_total` | counter | stage | 章节失败定位 |
| `much_agents_gate_status_total` | counter | status/check_id | 门禁 block 率 |
| `much_agents_llm_calls_total` | counter | model/operation/status/fallback | 模型调用与降级 |
| `much_agents_llm_tokens_total` | counter | model/kind | token 用量 |
| `much_agents_llm_cost_cny_total` | counter | model/project | 成本 |
| `much_agents_queue_depth` | gauge | queue | 队列积压 |
| `much_agents_worker_heartbeat_age_seconds` | gauge | queue/worker | worker 卡死 |
| `much_agents_ready_checks` | gauge | component | DB/Redis/MinIO/Chroma 健康 |
| `much_agents_backup_last_success_timestamp_seconds` | gauge | target | 备份年龄 |

> 至少保留请求、run、章节、门禁、LLM 调用、token、成本、队列、心跳、readiness、备份年龄。

### 5.3 告警建议

| 告警 | 条件 | 级别 | 动作 |
|---|---|---|---|
| API 5xx 比例高 | 5 分钟 >5% 或 10 分钟 >2% | P1/P2 | 查日志/依赖 |
| API P95 延迟高 | 10 分钟 >2s（非生成接口） | P2 | 查 DB/Redis/存储 |
| bid 队列积压 | `queue_depth > 5` 持续 15 分钟 | P2 | 扩容/查 worker |
| worker 无心跳 | 心跳年龄 >120 秒 | P1 | 重启 worker/查 broker |
| run 卡在 generating | 超过 `2×BID_RUN_STALE_SECONDS` 的数量 >0 | P1 | reaper/人工 resume |
| LLM 错误率高 | 10 分钟 >10% | P1 | 查 provider/限流/预算 |
| 模型 fallback 率高 | 1 小时 >5% | P2 | 查模型名/配置 |
| 日成本超预算 | 80% 预警，100% 严重 | P1/P0 | 暂停非关键任务 |
| 门禁 block 率异常 | 24 小时 >40% 或比基线上升 20pp | P2 | 查质量回归 |
| readiness 失败 | DB/Redis/MinIO/Chroma 任一 false >2 分钟 | P1 | 查依赖 |
| 备份过期 | 最后成功 >26 小时 | P1 | 查 timer/存储 |
| 磁盘使用率高 | >80% 预警，>90% 严重 | P1 | 清理/扩容 |
| 评测回归失败 | 夜间指标低于阈值 | P2 | 查变更/回滚 |

### 5.4 结构化日志与 request_id

- 新增 `src/observability/logging.py`：JSON formatter，字段包括 `ts/level/logger/event/request_id/job_id/run_id/project_id/section_id/user_id/duration_ms/status/error_type`。
- API 启动与 Celery 启动都配置该 formatter；保留 `LOG_LEVEL` 环境变量。
- 现有 `api/audit.py:618-636` 已生成 `request.state.request_id`；扩展为 `contextvars.ContextVar`，供任意 logger 读取。
- `CeleryDispatcher.dispatch` 在 `send_task` 时加 `headers={"request_id": ...}`；`api/tasks.py` 任务绑定 headers，设置 contextvar；`api/staged_bids.py` 的日志与 `llm_call_log` 使用同一 request_id。
- Job payload 增加 `request_id`、`actor_id`（非敏感），不增加 profile/正文。
- 禁止日志记录：Authorization、Cookie、请求体、profile、完整 prompt、完整投标正文、证据原文。

### 5.5 Chroma readiness 与 worker 健康检查

- `api/routes.py:361-405` 的 `/api/ready` 增加 `chroma` 检查：调用 Chroma `/api/v2/heartbeat`，超时 2 秒，缓存 5 秒。
- 增加 `READINESS_STRICT`：默认生产 true，Chroma false 时整体 503；如果业务接受关键词回退，可设 false，此时 checks 仍暴露 `chroma=false` 并标 degraded。
- `scripts/worker_healthcheck.py` 除 `active_queues` 外，增加 `inspect().ping()`/`stats()` 检查 worker 是否响应；队列积压不作为 Docker healthcheck 失败条件，改为 Prometheus 告警，避免忙时被误杀。
- 验收：停止 Chroma 后 `/api/ready` 在 5 秒内 503 且 `checks.chroma=false`；`/api/live` 仍 200；恢复后自动 200；worker 进程卡死时 healthcheck 失败并重启。

### 5.6 成本与配额治理

- 读取 `response.usage`、`response.model`、`finish_reason`；无 usage 时 token/cost 记 null 并告警，不估算。
- 新增 `src/bidding/pricing.py` + `LLM_PRICE_TABLE`（含生效日期）；未知模型成本 null + 告警。
- 预算：`BUDGET_MONTHLY_CNY`、`PROJECT_MONTHLY_BUDGET_CNY`、`BID_RUN_MAX_LLM_CALLS`、`BID_RUN_MAX_COST_CNY`、`BID_SECTION_MAX_ATTEMPTS`；入队前 + 每次调用前检查；超限暂停并保留已完成章节。
- 修复 regenerate 绕过：删除 `api/bid_runs.py:1286-1297` 的 `attempt=0`；run attempt 只增不减；单节重写受 `BID_SECTION_MAX_ATTEMPTS` 限制；前端达上限禁用并提示。
- 生产默认 `DEEPSEEK_ALLOW_FALLBACK=false`；若开启，记录 `model_requested/model_actual/fallback=true` 并在 report/warnings 提示；fallback 率 >5% 告警。
- MinerU/Embedding 配额：`MINERU_DAILY_PAGE_BUDGET`、`EMBEDDING_DAILY_CHAR_BUDGET`，超限 429，记录 `external_usage`。
- 价格对账：`run_usage` 与 `llm_call_log` 每日对账，差异 <0.5%；未知模型或缺失 usage 进入告警。

## 6. 可靠性与数据安全

### 6.1 stale reaper（Celery beat）

- `api/celery_app.py` 增加 `beat_schedule`：每 60 秒执行 `reap-stale-bid-runs`。
- 新建 `api/maintenance.py` 或 `api/tasks.py` 任务；调用新增 `BidRunRepository.reap_stale(limit=100, stale_seconds=...)`，批量选择 `status in ('generating','assembling') and heartbeat_at < cutoff`。
- 将 run 置为 `paused`，把 writing/reviewing/revising 的 section 置为 `pending`，写 `error/message`，记录审计事件；对应 job 标记 failed（exit_code 124，stale）。
- `docker-compose.prod.yml` 增加 `beat` 服务和 `worker-maintenance` 服务；maintenance 消费 `maintenance` 队列，不阻塞 bid 队列。
- 保留 API `resume` 作为人工兜底。
- 验收：kill -9 worker 后，2×stale 内 run 自动 paused、不再显示 generating；日志/指标有 `stale_reaped_total`；不影响正常长任务。

### 6.2 心跳频率与长章节误判

- 在 `src/bidding/staged.py` 的 `_run_section` 或 `generate_sections` 启动独立心跳协程，每 `BID_HEARTBEAT_SECONDS=60` 更新一次；即使 LLM 调用在 `asyncio.to_thread` 中执行，事件循环仍可发心跳。
- `_RunHooks.heartbeat` 继续更新 `bid_runs.heartbeat_at` 和 Redis job progress。
- 增加 section 级 `last_heartbeat_at`（迁移 0010），用于区分“整体 run 活着但某节卡住”。
- stale 阈值建议 `BID_RUN_STALE_SECONDS=300`（配合 60 秒心跳），或保留 900 但要求心跳 ≤180 秒。
- 验收：模拟单节 1200 秒，run 不被误判 stale；worker 真正死亡后 2×心跳间隔内被 reaper 发现。

### 6.3 generation_token 条件写

- `bid_section_states` 增加 `generation_token`（迁移 0010）；`claim_worker` 设置该 token。
- `_RunHooks.update_section`（`api/staged_bids.py:58-65`）传入 token；repository 更新时增加 `generation_token == token`。
- worker 对 `bid_runs` 的状态更新（`api/staged_bids.py:279/349/384/396/427`）改用 `update_run_owned(run_id, token, ...)`，条件包含 token 和 `status='generating'`。
- API 用户操作（编辑、审批、导出）仍走非 active 状态检查，不需要 token。
- 验收：启动 run A，取消后用 token B 重启；模拟旧 worker A 写 section/run，全部失败；最终数据属于 B。

### 6.4 取消语义与 DLQ

- cancel 时设置 `cancel_requested`，并记录 `cancel_requested_at`；worker 在每节开始、writer/reviewer/reviser 返回后、assembly 前检查。
- 取消后 run 置 `paused` 或新增 `cancelled` 状态；job 标记 `failed/cancelled`，exit_code 130，不得 `mark_succeeded`。
- 在 `api/jobs.py`/`api/redis_jobs.py` 增加 `cancelled` 终态，前端识别。
- 进行中的同步 LLM 调用无法安全强杀：设置单次调用 timeout，取消后最多等待当前调用返回；不再发起后续调用。验收时明确“至多 in-flight 调用继续”的残余风险。
- DLQ：Celery `task_failure`/`task_revoked` 信号把 `job_id/task_name/error_type/ts` 写入 Redis list `much_agents:dlq` 或 Postgres `job_failures` 表；提供 CLI 查看/重放；Prometheus 计数 + 告警。
- 验收：取消后 job 终态不是 succeeded；DLQ 有失败记录；重放幂等。

### 6.5 备份自动化、加密、异地、PITR

**最低要求（P0）**：

- 每日 02:00 自动执行 `scripts/backup.sh`；使用 systemd timer 或 cron，`Persistent=true`。
- 备份内容：PostgreSQL dump、MinIO 对象（含 `private/profiles`）、可选 Chroma。
- 加密：使用 `age` 或 `gpg` 对备份归档加密；密钥与备份分开保存；备份目录 `umask 077`。
- 异地：通过 `rclone`/`rsync` 同步到 NAS 或对象存储；至少一份不在同一主机/机柜。
- 保留：14–30 天；定期清理。
- 告警：最后成功备份 >26 小时触发告警；备份脚本写 Prometheus textfile 指标。
- 演练：每季度在干净 Linux 主机恢复并记录 RPO/RTO；已在本地 Docker Desktop + 生产 Compose 完成一次真实数据演练，仍需在干净 Linux 主机按真实数据量复测。

**PITR（P1，条件触发）**：

- 如果业务要求 RPO <24 小时，启用 PostgreSQL WAL 归档 + 每周 base backup（pgBackRest 或 WAL-G），归档到异地对象存储。
- 最低目标：RPO ≤15 分钟，RTO ≤4 小时；每季度做一次 PITR 演练。
- 如果只接受 RPO 24 小时，则明确写入 SLA，不引入 PITR 复杂度。

### 6.6 镜像与供应链

- `docker-compose.prod.yml` 中 MinIO/Chroma 已固定 digest（沿用 M1 验收报告记录）；Postgres/Redis 仍需固定 digest 或小版本 tag。
- CI 增加 Trivy 镜像扫描、Syft SBOM；Cosign 签名在具备密钥/无钥匙 OIDC 的发布流水线中接入。HIGH/CRITICAL 漏洞阻断或有明确 allowlist。
- 保留 LICENSE/NOTICE 在镜像内；AGPL 源码入口不变。
- 验收：`docker compose config` 不含 `:latest`；CI 生成 SBOM；扫描结果可查。

## 7. 模型质量与交付质量

### 7.1 模型/提示词溯源

- 新建 `prompt_registry`，`src/bidding/prompts.py`、`src/parsing/extraction/prompts.py`、`fix_plan.py`、`figures/planner.py` 的 prompt 外置/版本化；记录 `prompt_sha256`。
- 记录 `model_actual`、`system_fingerprint`（若有）、`temperature`、`max_tokens`、`json_mode`、`git_sha`、`app_version`、每阶段耗时、实际调用数。
- **处理截断**：`finish_reason == "length"` 时不得静默返回；按配置重试/续写/失败，并写 `status=truncated` 告警。
- **审查证据**：移除 `src/bidding/prompts.py:153` 的 `evidence[:3]`，reviewer 看完整证据；输出 `missing_points/covered_points` 并进入 coverage。
- **加权覆盖率**：`src/bidding/assemble.py:218-280` 增加 `coverage.weighted`、`coverage.reviewer_gap`；`src/bidding/compliance.py:456-482` 的 `scoring_coverage` 改为按加权阈值；`src/bidding/staged_output.py:69-81` 保留 reviewer 覆盖点。
- **检索快照**：记录 query、mode、top_k、命中 chunk/score/page、fallback_reason；embedding 模型/维度写入集合版本（如 `bidding_documents__<model>__<dim>`），切模型新建集合并后台重建。
- **统一 LLM 客户端**：把 `src/bidding/llm.py` 与 `src/parsing/extraction/llm.py` 收敛为 OpenAI 兼容客户端 + 计量包装；避免两套重试/fallback 逻辑漂移。

### 7.2 门禁硬化与审批

**保留 force，但把它变成“有理由、有期限、有留痕的例外”。**

- 明确“交付物 = `bid.docx`”；`bid.md`/分节 Markdown 标记为草稿；artifact 接口请求 `bid.docx` 返回 404。
- 门禁硬化：未审批/内容哈希不匹配不可导出；`force` 仅 admin + reason + 24 小时有效 + 审计；任何编辑/重写/重新组装/配图重生成撤销审批。
- 内容哈希：章节版本哈希 + outline revision + figure manifest hash + gate/compliance/consistency 摘要规范化哈希；DOCX 另存 `docx_sha256`。
- 审批最小模型：`bid_runs.approval_status`（not_required/pending/approved/rejected/revoked）+ `bid_approvals` 历史；`BID_REQUIRE_APPROVAL=true` 生产默认；reviewer 审批、bid_manager 导出、admin force。
- 角色与审计：service token 不可 force；审批/驳回/撤销/override 全部写审计；内容变更使旧审批/override 自动失效。

### 7.3 DOCX 自动检查

新增 `src/bidding/docx_quality.py`：

| 检查 | 规则 | 失败级别 |
|---|---|---|
| 目录域 | XML 存在 `TOC \o` 域，`w:updateFields=true` | block |
| 页码域 | footer 存在 `PAGE` 域 | block |
| 字体 | 正文/标题 run 的 eastAsia 字体非空；字号符合模板 | warn |
| 图片 DPI | 像素宽度 / 显示英寸 ≥150；<96 block，96–150 warn | warn/block |
| 表格表头 | 有表头的表格设置 `tblHeader`，跨页重复 | warn |
| 表格行拆分 | 关键行设置 `cantSplit`（可选） | warn |
| 图片文件 | 存在、可解码、路径安全 | block |
| 页数 | 有 LibreOffice 时转 PDF 取实际页数；无则标记估算 | warn |
| 空章节/占位符 | 复用 report.placeholders | warn |

落地：`src/bidding/docx_export.py` 增加 TOC 域、footer PAGE 域、表格 `tblHeader`、图片尺寸/DPI 元数据；`DocxResult` 增加 `page_count`/`quality`；`staged_output.py:write_staged_outputs` 调用 `check_docx`，写 `docx_quality.json` 并进入 `report["docx_quality"]`；`gate.py` 增加 `docx_quality` 检查；CI 安装 LibreOffice Writer + CJK 字体，生成 fixture DOCX 测试。

## 8. 分阶段路线图

> 工作量为单人全职估算；项目目前基本单人维护，**完整 P0 约 95 人日（最小包 67 + 追加 25–30），单人 4–5 个月**。建议分“最小上线包”和“完整 P0”两档；若只有 3 个月，按下方裁剪版执行。

### 8.1 P0（0–3 个月，上线阻断项）

**最小上线包（约 67 人日，单人 13–15 周；严格 3 个月见下方裁剪版）**：

| 工作项 | 人日 | 验收 |
|---|---:|---|
| 基线测量：3 份真实标书跑当前系统，记录 L0 指标/成本/耗时/失败 | 4 | 基线报告归档；不阻断 |
| L0 加权覆盖率 + report 溯源字段 | 7 | report 含 weighted/reviewer_gap/model/prompt/git sha/实际调用数 |
| `tests/eval` 骨架 + 3 份标书脱敏标注 | 6 | CI L0 <60 秒；真实数据在私有目录 |
| LLM 明细 + 成本 + 价格表 + 预算 | 10 | 100% 调用有 model/token/成本；预算阻断 |
| 修复 regenerate 绕过 + 单节上限 | 4 | 第三次重写 409；API 无法重置计数 |
| `/metrics` + JSON 日志 + request_id + Chroma readiness | 10 | 10+ 指标；一条 run 日志可关联；Chroma 停 ready 503 |
| stale reaper + 心跳 + token 条件写 + 取消语义 + DLQ | 12 | 崩溃自动 paused；旧 token 写入失败；取消不 succeeded |
| 备份自动化/加密/异地 + 恢复演练 | 5 | 每日备份；异地；RPO/RTO 记录 |
| 门禁 force 策略 + 内容哈希 + 交付物边界 + 审批 API（无 UI） | 6 | 未审批不可导出；force 留痕/24h 失效；审批 API 可用 |
| 镜像 pin + 漏洞扫描 | 3 | 无 latest；扫描通过 |
| **合计** | **约 67 人日** | 单人需 13–15 周；建议砍掉非必须项或分两批 |

> 若严格 3 个月单人，建议只做：基线+L0（11）、LLM 明细+预算+regenerate（14）、指标/日志/readiness（10）、reaper/心跳/token/取消（12）、备份（5），约 52 人日；门禁/镜像/标注可延到第 4 个月。

**完整 P0 追加（约 25–30 人日，建议 2 人）**：L1 3 份标注完善、审批最小 UI、DOCX 质量检查、PITR 评估、真实 Linux 部署/恢复演练、前端错误上报。

**P0 出口标准（必须全部满足）**：

- 一次真实小样本 run 的每个 LLM 调用都有 `model_actual/tokens/cost`；不存在未计价调用；预算 100% 阻断。
- `BID_RUN_MAX_ATTEMPTS=2` 时单节重写第三次 409；无 API 可重置。
- kill worker 后 2×stale 内 run 自动 paused；旧 generation_token 写入失败。
- 取消中的 run 最终不是 succeeded；DLQ 可查；告警可送达。
- `/metrics` 可抓取；一条 run 的 API/worker 日志可用 request_id 关联；Chroma 停止时 `/api/ready` 503。
- `report.json` 含加权覆盖率、reviewer 覆盖点、model/prompt/git sha、实际调用数与耗时；CI L0 通过。
- 3 份现有标书完成脱敏标注；L0 baseline 报告归档。
- 未审批导出被拒；admin force 有 reason/审计/24h 失效。
- 每日加密备份 + 异地副本；恢复演练记录 RPO/RTO；镜像无 `latest`。

### 8.2 P1（3–6 个月，质量闭环，约 60–75 人日）

- L1 评测集扩到 5–10 份；L2 judge 校准（一致率 ≥80%/kappa ≥0.6）；L3 每周抽检。
- record/replay + 夜间真实模型回归 + 阈值阻断；prompt/model 变更强制评测报告。
- 反馈/采纳入口；`bid_section_versions` + 版本列表/恢复；编辑率/重写率/采纳率看板。
- 审批流 UI + 内容哈希绑定；DOCX 质量检查进 gate；交付物边界固化。
- 检索快照 + reviewer 完整证据 + embedding 集合版本；统一 LLM 客户端。
- 外部用量配额；PITR（若 RPO <24h）；Grafana 看板。

**P1 出口**：5–10 份评测集；prompt/model 变更附报告；夜间回归连续 3 次绿色才发布；DOCX fixture 通过 TOC/页码/DPI/表头检查；审批后编辑自动失效；版本可恢复；干净 Linux 恢复演练通过。

### 8.3 P2（6–12 个月，规模化与可选数据中台，约 40–60 人日）

- 跨 run 聚合视图/看板（项目/模型/prompt/成本/覆盖率/门禁/失败率）。
- 明细保留/分区/归档（90 天热 + 24 个月聚合）；数据量触发时做数据平台决策记录。
- 10–20 并发 run 压测；DB 连接池/队列背压评估；单机灾备 runbook。
- 可选：本地模型网关（数据不出内网）、SSO/MFA、前端 E2E。
- **只有满足第 0.2 节触发条件才建重型数据中台**；否则明确继续轻量底座。

## 9. Non-goals（现在不做）

| 不做 | 原因 | 何时再考虑 |
|---|---|---|
| 重型数据中台（ClickHouse/Kafka/dbt/Airflow/特征平台） | 数据量小、无计费、无专职数据团队；运维成本大于收益 | 满足 0.2 的触发条件 |
| 独立数仓分层/宽表集市 | 单机 PostgreSQL 足够；过早分层会拖慢迭代 | 跨 12 个月分析或 >3 部署合并 |
| Kubernetes/HA/多区域 | 目标单机私有化；HA 需要架构改造和成本 | 客户明确要求 99.9% SLA |
| 多租户 SaaS 改造 | 目标单租户；每客户独立部署更简单、更安全 | 真要转 SaaS 时重新设计 |
| 完整 MLOps/实验平台/模型注册中心 | prompt registry + 评测报告 + git sha 足够 | 多模型、多团队、频繁训练 |
| 微调/训练自有模型 | 当前瓶颈是数据与评测，不是模型容量 | 有足够标注数据和稳定评测后 |
| 文生图/通用图像生成 | 现有配图是确定性/证据驱动；文生图增加编造风险 | 不计划 |
| 完整 APM（Jaeger/Tempo/OTel Collector 全链路） | Prometheus + 结构化日志 + request_id 足够 | 跨多服务、复杂调用链 |
| 实时协同编辑/BPMN 审批引擎 | 小团队异步审批足够 | 多人同时编辑/复杂审批 |
| 替换 Chroma/重做向量库 | 当前不是瓶颈；先 pin 版本、备份、评测 | 检索质量成为主要瓶颈 |
| 前端重写/微服务拆分 | 无收益，增加风险 | 团队/流量显著增长 |

## 10. 风险与取舍

| 风险 | 影响 | 补偿控制 | 残余风险 |
|---|---|---|---|
| 主机/磁盘故障 | 全站不可用 | 每日加密备份 + 异地 + 快速重建 runbook | RTO 取决于备机/恢复速度 |
| 单机资源竞争 | 生成/解析互相拖慢 | 队列隔离、并发限制、资源监控、错峰 | 高峰期时延上升 |
| Redis 丢失 | 任务状态丢失 | AOF、run 状态在 Postgres、可 resume | 进行中任务需重跑 |
| Chroma 丢失 | 检索不可用 | 可从 parsed/chunks 重建；关键词回退 | 重建期间质量下降 |
| MinIO 丢失 | 产物/profile 丢失 | 备份/异地；profile 私有加密 | 备份窗口内数据丢失 |
| 云 LLM 故障/涨价 | 生成失败/成本上升 | fallback、预算、队列暂停、模型网关 | 无法完全消除 |
| 单机无 HA | 升级/重启停机 | 维护窗口、滚动重启、健康检查 | 不能承诺 99.9% |
| 小样本评测波动 | 误判质量升降 | L1+L2+L3 三角验证；连续 2 次异常才阻断 | 统计不确定性 |

**必须先解决才能对外承诺 SLA**：

1. 自动 stale reaper + 心跳 + token 条件写。
2. 成本明细 + 预算。
3. 指标/告警/日志 request_id。
4. 备份自动化 + 加密 + 异地 + 恢复演练。
5. 审批 + 内容哈希 + 门禁硬化。
6. L0/L1 评测基线。
7. 镜像 pin + 漏洞扫描。
8. 真实 Linux 主机验收（当前只在 Windows Docker Desktop 演练）。

## 11. 需要确认的决策点

| 决策 | 建议 | 确认方 |
|---|---|---|
| 是否现在建数据中台 | 不建；建轻量底座 | 产品/技术负责人 |
| 云 LLM 是否允许出网 | 默认允许；客户禁止时走本地网关 | 客户/安全 |
| 评测数据归属 | 私有目录/私有仓库；公开仓库只放合成样例 | 法务/安全 |
| 审批角色 | `reviewer` 审批，`bid_manager` 导出，`admin` force | 产品 |
| force 导出策略 | admin + reason + 24h + 审计 | 产品/安全 |
| SLA | API 99.5%，异步完成时延按基线，RPO/RTO 按备份方案 | 业务/客户 |
| 3 个月人力 | 建议 2 名工程师；单人需砍 scope 或延到 5 个月 | 管理层 |
| PITR | 仅当 RPO <24h 时做 | 业务/运维 |

## 附录 A：评测数据目录建议（私有）

```text
eval-data/
  manifest.sha256
  tenders/<case_id>/
    extraction.json
    toc.json
    chunks.jsonl
    full.md
    sanitization_report.json
  labels/<case_id>/
    evaluation_factors.json
    key_facts.json
    must_cover_points.json
    reference_outline.json
  human/<case_id>/
    sections/<section_id>.json
  judge/
    rubric.md
    calibration.json
```

- `EVAL_DATA_DIR` 指向仓库外目录；CI 只在私有环境挂载。
- 公开仓库放 `tests/eval/schema/` 和合成样例。
- 每份数据有脱敏报告和 SHA256；禁止提交真实公司名、电话、证书编号、地址、金额。

## 附录 B：新增环境变量建议

| 变量 | 默认 | 说明 |
|---|---|---|
| `LLM_PRICE_TABLE` | 空 | JSON/文件路径；模型输入/输出单价 |
| `BUDGET_MONTHLY_CNY` | 空 | 全局月预算；空表示不限制 |
| `PROJECT_MONTHLY_BUDGET_CNY` | 空 | 项目月预算 |
| `BID_RUN_MAX_LLM_CALLS` | 空 | 单 run 调用上限 |
| `BID_SECTION_MAX_ATTEMPTS` | 2 | 单节重写上限 |
| `BID_HEARTBEAT_SECONDS` | 60 | 心跳间隔 |
| `BID_RUN_STALE_SECONDS` | 300 | stale 阈值 |
| `DEEPSEEK_ALLOW_FALLBACK` | `false`（生产） | 是否允许静默 fallback |
| `METRICS_ENABLED` | `true` | 是否暴露指标 |
| `METRICS_TOKEN` | 空 | 可选；保护 `/metrics` |
| `LOG_FORMAT` | `json`（生产） | `json`/`text` |
| `READINESS_STRICT` | `true`（生产） | Chroma 失败是否整体 503 |
| `WORKER_MAX_BACKLOG` | 50 | worker 告警阈值 |
| `BID_REQUIRE_APPROVAL` | `true`（生产） | 导出是否要求审批 |
| `BACKUP_ENCRYPT` | `age`/`gpg`/空 | 备份加密方式 |
| `BACKUP_OFFSITE_TARGET` | 空 | rclone/rsync 目标 |
| `MINERU_DAILY_PAGE_BUDGET` | 空 | MinerU 日页数上限 |
| `EMBEDDING_DAILY_CHAR_BUDGET` | 空 | Embedding 日字符上限 |
| `EVAL_DATA_DIR` | 空 | 私有评测数据目录 |
| `EVAL_MODE` | `replay` | `record/replay/live` |
| `GIT_SHA` | 构建注入 | report/provenance |
| `APP_VERSION` | 0.1.0 | report/provenance |

## 附录 C：验收清单

- [ ] 3 个月 P0 工作项全部完成，或有明确的风险接受记录。
- [ ] LLM 调用 100% 有明细；预算 100% 阻断；无未计价调用。
- [ ] 单节重写不能绕过 attempt 上限。
- [ ] worker 崩溃自动 paused；旧 token 写入失败；取消不显示 succeeded。
- [ ] `/metrics`、JSON 日志、request_id、Chroma readiness 全部可用。
- [ ] report 含加权覆盖率、reviewer 覆盖点、model/prompt/git sha、实际调用数和耗时。
- [ ] 3 份标书标注完成；L0 CI 通过；L1 基线报告归档。
- [ ] 未审批不可导出；force 有 reason/审批/审计/24h 失效。
- [ ] 每日加密备份 + 异地副本 + 季度恢复演练。
- [ ] 镜像无 `latest`；SBOM/扫描通过。
- [ ] 6 个月 L1 5–10 份、L2 校准、夜间回归、DOCX 检查、审批 UI 完成。
- [ ] 12 个月数据平台决策记录完成；未满足触发条件则不建重型中台。
