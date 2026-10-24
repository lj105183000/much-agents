# Much Agents 内网多人产品化路线图

> 状态：路线图（2026-10-24）。P0-A「认证 / RBAC」已实现；其余阶段仍为规划。功能现状以代码与 `CLAUDE.md` 为准，认证细节见 `docs/authentication.md`。

## Context

Much Agents 已经具备一条完整的标书生产链路：PDF 解析 → 结构化抽取 → Chroma 检索 → 多 Agent 编写/审查/修订 → 确定性组装 → 21 项合规 + 一致性台账 + 修复方案 → 配图 → 门禁 → DOCX 导出；存储层已有 MinIO + 私有 profile 边界，任务层已有 Celery/Redis + PostgreSQL 持久化 run。工程骨架和"不编造事实"的约束是真实优势。

它起步时仍是**单机单用户工具**：API 没有认证、CORS 为 `*`、基础设施使用默认弱口令、没有 CI/Dockerfile/Compose/备份；生成内容没有引用契约，评分要求覆盖只靠关键词子串；没有项目隔离、审批、版本与审计；DOCX 只是草稿级。P0-A 已补上认证/RBAC，但其余差距仍使直接部署到内网多人环境不可接受。

本路线图面向**内网多人系统**，覆盖用户选定的四个方向：生成质量与可信度、多用户与协作、部署与运维、交付物与复用。路线图最初只做规划；P0-A 认证/RBAC 已按独立计划先行落地，其余阶段仍待实施。

### 现状证据（关键差距）

- **认证/RBAC（P0-A 已补）**：本地账号、可撤销会话、5 角色、默认拒绝 API、管理员用户管理与 MCP 服务账号已实现（`api/auth.py`、`api/users.py`、Alembic `0003`）。但项目隔离、审计与生产加固仍未做，见 3.2/3.3/3.6。
- **开发默认仍不适合直接上内网**：`API_HOST=0.0.0.0`、`CORS_ORIGINS=*`、无 TLS、`/docs` 公开、token 存 localStorage；生产部署见 P0-D。
- **无项目实体**：数据库只有 `companies`、`bid_runs`、`bid_outline_sections`、`bid_section_states`（`migrations/versions/0001`、`0002`）；文档库仍是扫描对象存储。
- **无交付工程**：根目录没有 `Dockerfile`、`docker-compose.yml`、`.github/`、`README`，只有 `start.bat` 开发脚本；生产部署、升级、备份均无定义。
- **生成不可溯源**：`src/bidding/llm.py:95-165` 是 DeepSeek 专用客户端；`MeteredChatClient`（`:178-187`）只计调用次数、没有 token/成本；`src/bidding/prompts.py:153` 审查只看到 3 条证据；`src/bidding/checks.py:159-163` 用关键词子串判断需求覆盖。
- **无审批/版本**：`src/bidding/gate.py:214-220` 的强制导出只记录不拦截；没有审批人、审批状态、版本 diff 或恢复。
- **交付物有限**：`src/bidding/docx_export.py:286-370` 仅基础标题/段落/表格/图片；无 PDF；无模板管理。
- **输入格式单一**：`src/parsing/pipeline.py:212-213` 只接受 PDF；无 DOCX/XLSX/HTML、无本地 OCR。

## 0. 结论

1. **先做 P0 安全与部署基座**。认证/RBAC 已完成，但项目隔离、审计和备份仍未补齐；没有这些能力的系统不能上内网，这是硬门槛。
2. **质量的下一个杠杆不是更多 Agent，而是证据闭环**：每条关键事实能引用到招标文件页码，每条评分要求有登记与覆盖状态，每次生成有成本与参数溯源。
3. **协作的核心是审批与版本**，不是聊天框：审批绑定内容哈希，任何修改使审批失效；版本可 diff/恢复；导出受审批锁。
4. **交付物与复用决定长期价值**：高保真 DOCX/PDF、模板、企业资质库、跨标书复用。
5. **云依赖必须可切换**。内网客户可能禁止标书文本出境；P0 先把 LLM/Embedding 抽象为 OpenAI 兼容网关，P1/P3/P4 提供本地模型与 OCR 选项。

## 1. 目标形态与原则

**目标形态**：部署在客户内网 Linux 服务器上的多人 Web 系统（Docker Compose），支持 5–50 人、多项目并行、角色分工、审批留痕、审计可查、备份可恢复；数据默认不出内网（可配置）。Electron 保留为单机/离线入口，内网多人以 Web 为主。

**原则**

- 安全先行：认证、授权、审计、最小暴露面先于功能。
- 人在环上：AI 生成 + 确定性检查 + 人工审批；不追求无人值守投标。
- 证据优先：事实来自招标文件/企业资料，未知用 `【待填写】`，严禁编造。
- 渐进交付：每个阶段可独立上线、可回滚、有验收指标。
- 复用现有骨架：staged run、gate、compliance、consistency、fix_plan、figures、MinIO hydration 都已存在，扩展而非重写。
- 隐私边界不倒退：`profile.json` 永不进入 artifact allowlist、MCP 输出、Redis/Celery payload 或下载 URL；审计只记元数据，不记 profile 内容或正文。

**两个决策点（不阻塞路线图）**

- 部署目标：推荐 Linux VM + Docker Compose；Windows 仅开发。
- 数据边界：允许调用云 LLM / 必须全内网。路线图默认"可切换"；若必须全内网，把 P1 的本地模型/嵌入与 P3 的本地 OCR 提前到 P0/P1，形成"离线版"里程碑。

## 2. 路线图总览

| 阶段 | 主题 | 单人工作量 | 关键交付 | 出口标准 |
|---|---|---|---|---|
| P0 | 可安全上内网 | 15–20 人日 | 登录/RBAC、项目隔离、审计、Compose/CI/备份、模型网关 | 角色权限测试通过；跨项目 403；审计可查；干净主机一键部署；备份恢复演练通过 |
| P1 | 生成可信度 | 20–25 人日 | 引用契约、需求登记/覆盖矩阵、成本与溯源、评测集 | 引用覆盖率可量化；需求覆盖进入门禁；成本/参数入报告；评测回归在 CI |
| P2 | 团队协作与审批 | 20–25 人日 | 审批流、版本 diff/恢复、评论/指派、SSE | 未审批不可导出；并发编辑 409；版本可恢复；进度实时 |
| P3 | 交付物与复用 | 25–30 人日 | 高保真 DOCX/PDF、模板、资质库、内容复用、多格式导入 | DOCX 交付检查通过；模板变量校验；资质到期提醒；复用有出处 |
| P4 | 规模化运维 | 持续 | 可观测性、Celery 可靠性、性能、容灾、e2e/压测 | 监控告警覆盖核心链路；压测达标；恢复演练达标 |

> 工作量为单人全职估算，不含安全测评、采购与客户验收；两人并行可压缩约 35–40%。

### 四个方向与阶段映射

| 方向 | 主阶段 | 关键能力 |
|---|---|---|
| 生成质量与可信度 | P0（模型边界）+ P1 | 引用、需求覆盖、评测、成本、检索充分性 |
| 多用户与协作 | P0（基座）+ P2 | RBAC、项目隔离、审批、版本、评论、SSE |
| 部署与运维 | P0 + P4 | Compose/CI/TLS/备份、监控、容灾、配额 |
| 交付物与复用 | P3 | PDF/DOCX、模板、资质库、跨标书复用、多格式导入 |

## 3. P0 — 可安全上内网（15–20 人日）

### 3.1 认证、会话与 RBAC（P0-A 已实现）

> 已落地：默认拒绝 API、本地账号与 Argon2 密码、SHA-256 不透明会话、5 角色 + `service` 主体、登录锁定、最后管理员保护、管理员用户管理、MCP `MCP_API_TOKEN`、前端登录/登出/角色感知、认证 Blob 产物访问。实现与验证见 `docs/authentication.md`。
>
> 未落地：项目级 `project_members`（属于 3.2）、审计写入（3.3）、Redis/IP 登录限流、生产 TLS/安全头（3.6）。因此 P0-A 单独不能作为内网多人上线验收。

**做什么**

- 本地账号：`users` 表（Argon2 密码哈希）、`user_sessions`（不透明会话 token，服务端可撤销）。
- 角色：`admin` / `bid_manager` / `writer` / `reviewer` / `viewer`；项目内角色通过 `project_members` 绑定。
- 所有 `/api/*` 默认要求认证（白名单：`/api/health`、`/api/auth/login`）；写操作按角色与项目校验。
- 前端登录页、401 拦截、登出、角色感知（隐藏无权按钮）；Electron 同样走 Bearer token。`file://` 的 Origin 为 `null`，CORS 需显式允许（仅本机）或改用自定义协议。
- 首启管理员：`uv run python -m api.users create-admin` 或一次性 `BOOTSTRAP_ADMIN_*`，禁止默认密码。
- MCP 服务端：新增 `MCP_API_TOKEN` 服务账号，继续只做 API 薄代理；HTTP 模式保留 bearer。

**技术选择**

- `argon2-cffi` + 随机 256-bit 会话 token（DB 存 SHA-256）；避免 JWT 撤销难题。实际实现见 `api/auth.py`。
- FastAPI 依赖 `require_user` / `require_project_role(...)`；路由级默认挂载，减少漏配。
- 登录限流（Redis 计数 + 失败锁定），审计登录成功/失败。

**改动点**

- `api/app.py`（中间件/依赖）、`api/routes.py`（路由守卫）、`api/bid_runs.py`、`api/companies.py`、`api/artifacts.py`、`api/search.py`、`api/documents.py`、`api/mcp_server.py`。
- `api/config.py` 新增 `AUTH_ENABLED`、`AUTH_SESSION_TTL_HOURS`、`MCP_API_TOKEN`。
- `frontend/src/App.tsx`、`frontend/src/api/client.ts`、新增 `LoginView` / `useAuth`。

**验收**

- 未登录访问任意业务接口 → 401；viewer 调用写接口 → 403；跨项目访问 → 403/404。
- 会话过期/撤销后立即失效；登录失败 5 次锁定 15 分钟。
- pytest 覆盖每个角色 × 关键端点矩阵。

### 3.2 项目实体与数据隔离

**做什么**

- 新增 `projects`（招标项目）、`project_members`、`documents`（doc_id 注册表）、`project_documents`。
- `bid_runs.project_id` 必填；公司库默认组织级共享（编辑需 `bid_manager`/`admin`），如多事业部需要隔离再加 `company_scope`。
- 所有列表/查询/产物下载按项目成员校验；artifact 流式接口先查 ACL 再读对象。
- 现有数据迁移：创建"默认项目"，把现有 companies/bid_runs/documents 归入；管理员自动成为成员。
- 公司库敏感字段分级：viewer 只见公司名/资质概要；证件号、联系人、银行信息仅 `bid_manager`/`admin` 可见。

**改动点**

- 新迁移 `0004_projects_acl.py`（审计可另起；`0003` 已被 users/sessions 占用）。
- `api/bid_run_repo.py`、`api/bid_runs.py`、`api/companies.py`、`api/documents.py`、`api/artifacts.py`、`api/streaming.py`。
- 前端增加项目切换器、成员管理、项目维度历史。

**验收**

- 用户 A 无法列出/读取/下载项目 B 的 run、文档、产物、profile 派生信息。
- 现有数据全部归入默认项目且可正常打开。
- 文档列表不再全量扫描对象存储（走 DB 注册表 + 分页）。

### 3.3 审计日志

**做什么**

- `audit_events` 追加写表：时间、actor、project、action、object_type/id、outcome、IP、user_agent、request_id、detail JSONB（**不含 profile 内容/正文**）。
- 覆盖：登录、用户/项目/成员变更、公司库变更、上传、run 创建/生成/取消/组装/编辑/重写/应用修复、导出与强制导出、模板/资质变更、系统设置。
- 哈希链（`prev_hash`/`hash`）+ `verify` CLI，提供篡改可见性；每日摘要可选。
- 审计查询页（admin）：按项目/人/动作/时间筛选，导出 CSV。

**验收**

- 每个写操作都有审计记录；强制导出必须带 reason；审计不可通过 API 修改/删除。
- 哈希链校验通过；并发写入不破坏链。

### 3.4 模型与数据边界

**做什么**

- 把 `DeepSeekChatClient` 重构为 `OpenAICompatibleChatClient`：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_FALLBACK_MODELS`；保留 DeepSeek 默认值，现有 `.env` 不改也能跑。让现有 `LLM_PROVIDER` 真正生效。
- 新增 `EmbeddingProvider` 协议：`ZhipuEmbedder` 保留，新增 OpenAI 兼容 `/v1/embeddings` 实现；`EMBEDDING_PROVIDER`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`、`EMBEDDING_DIM`。
- 向量集合带模型/维度版本（如 `bidding_documents__<model>__<dim>`），切模型时新建集合并后台重建；提供 reindex 命令。
- 数据出境清单：`docs/data-boundary.md` 列明发送到外部的内容（招标文本、提示词、检索片段）与关闭方式；`MCP_CLIENT_ENABLED` / `BID_FIGURES_EXTERNAL` 默认关闭不变。
- 可选内网网关：LiteLLM/One-API 作为 sidecar，统一 key、限流、路由与审计；应用只连内网地址。
- MinerU 已支持 `MINERU_BASE_URL`，可在内网自建；解析失败仍回退 PyMuPDF。

**验收**

- 仅改环境变量即可把 LLM/Embedding 指向内网兼容服务，无代码改动。
- 切换 embedding 模型触发重建且旧集合不被混用；检索结果可复现。
- 断网环境（除内网网关）下解析（PyMuPDF）、生成、检索、导出可运行；云解析/云嵌入明确报可读错误。

### 3.5 部署、CI 与备份

**做什么**

- 多阶段 `Dockerfile`（后端 uv + Python 3.12-slim，非 root；前端 Node build → Nginx）。
- `docker-compose.yml`：api、worker-parse、worker-bid、postgres、redis、minio、chroma、nginx；healthcheck + `depends_on: service_healthy`；命名卷；内部网络；仅 Nginx 暴露 443。
- **拆分队列**：`parse` 与 `bid` 分开，parse worker 并发 1，bid worker 并发 2–4，避免长标书任务阻塞解析（当前两者共用 `parse` 队列且 `--pool=solo`）。
- CI：`.github/workflows/ci.yml` — 后端 pytest、前端 lint/build、docker build、`alembic upgrade head`（空库）、gitleaks/pip-audit/npm audit。
- 备份：`scripts/backup.py` 用 `pg_dump -Fc` + MinIO 镜像 + Chroma 卷快照，经 `restic` 加密到 NAS；`scripts/restore.py` + 季度演练文档。
- 升级：镜像 tag + 迁移前置备份 + 回滚步骤；`start.bat` 保留为开发入口。
- TLS：企业证书挂载；HTTP→HTTPS；安全响应头；SSE 关 buffering。

**验收**

- 干净 Linux 主机 `docker compose up -d` 后 10 分钟内健康；重启后数据在。
- CI 在 PR 上全绿；镜像可复现构建。
- 备份 → 删除测试库 → 恢复，数据与对象一致；RTO/RPO 写入运维文档。

### 3.6 安全加固

- CORS 默认改为显式来源（生产同源，可不需要 CORS）；API 默认绑定 `127.0.0.1`，由 Nginx 反代。生产环境若 `AUTH_ENABLED=false` 或仍使用示例口令，启动直接失败（不仅是 warning）。
- 生产禁用 `/docs`/`/redoc` 或仅 admin；安全头（CSP、X-Content-Type-Options、Referrer-Policy、frame-ancestors）。
- Postgres/Redis/MinIO 强密码由部署时生成；Redis 开启密码；MinIO bucket 私有；DB 最小权限账号。
- 上传：按魔数校验 PDF/ZIP；解压炸弹与页数上限；文件名净化（已有路径穿越防护）。
- 限流：Nginx `limit_req` + 应用层登录/上传/搜索限流（Redis，local 模式内存兜底）。
- 健康检查拆分：`/api/live`（进程）与 `/api/ready`（依赖，5s 缓存）；`/api/health` 保留给 UI。
- MCP：默认关闭；HTTP 非回环必须 token；stdio 仅本机；外部证据仍不可信。

**P0 关键文件**：`api/app.py`、`api/config.py`、`api/routes.py`、`api/bid_runs.py`、`api/companies.py`、`api/artifacts.py`、`api/streaming.py`、`api/bid_run_repo.py`、`api/documents.py`、`api/db.py`、`migrations/versions/`、`frontend/src/App.tsx`、`frontend/src/api/client.ts`、`deploy/nginx.conf`、`scripts/start_all.py`、`.env.example`、新增 `Dockerfile` / `docker-compose.yml` / `.github/workflows/ci.yml`。

## 4. P1 — 生成可信度（20–25 人日）

### 4.1 证据引用契约与校验（最高优先级）

**做什么**

- 检索证据编号 `E1..En`（稳定、含页码/章节/chunk id）；writer 提示词要求关键事实句尾标注 `[E3]`。
- 确定性校验器：编号存在、页码落在 chunk 范围、引用片段与证据有词面/向量重合；对金额/日期/人名/证书号等关键主张要求至少一个引用。
- 可选 LLM 判官抽样验证"主张是否被引用片段支持"；失败降级为确定性结果。
- 产物 `citations.json`：每节引用列表、未引用关键主张、覆盖率；进入 `report.json` 与门禁（先 warn，稳定后 block）。
- 前端"来源"面板：逐条展示引用 → 点击跳到招标文件页码/片段。

**改动点**：`src/bidding/retrieval.py`（证据 id）、`prompts.py`、`checks.py`、`staged.py`、`assemble.py`、`gate.py`、`staged_output.py`、`frontend/src/components/BidSectionModal.tsx` / `BidRunWorkspace.tsx`。

**验收**：固定样例的引用覆盖率可计算；伪造编号/越界页码被拦截；门禁输出引用指标；前端可定位原文。

### 4.2 需求登记簿与覆盖矩阵

**做什么**

- 从 `EvaluationFactor` + `QualificationRequirement` + 正则兜底生成 `requirements.json`：`REQ-EVAL-*` / `REQ-QUAL-*`，含原文、来源页、分值/权重、强制/评分属性。
- 需求 → 章节映射：复用 `src/bidding/outline.py:118-180` 的关键词映射，允许人工调整；映射进入大纲编辑。
- 覆盖状态：`covered/partial/missing/not_applicable`，由 reviewer 按需求清单输出 + 确定性兜底（关键词/相似度）；人工可覆盖并写理由。
- 门禁消费：强制需求 missing → block；评分需求加权覆盖率低 → warn；`report.json` 输出覆盖率与缺口清单。
- 合规 21 项中的"评分覆盖"改用登记簿，不再只查关键词子串（`src/bidding/checks.py:159-163`）。

**验收**：样例招标需求抽取召回率基线；每个评分因素可追溯到章节与证据；缺项在 UI 与门禁可见。

### 4.3 成本、用量与溯源

**做什么**

- 升级 `MeteredChatClient`：从 OpenAI 响应读取 `usage`，按配置单价计算成本，按节/run 聚合。
- `report.json` 与 DB 记录 prompt/completion tokens、成本、模型、耗时、重试次数。
- 运行预算：`BID_RUN_BUDGET_CENTS` + `BID_BUDGET_ENFORCEMENT=warn|stop`；超预算暂停并提示。
- 溯源：prompt 版本/哈希、模型/provider/base_url host（不含 key）、temperature、top_k、evidence cap、应用版本/git sha、时间戳。
- 前端运行详情展示成本与参数；项目维度月度用量（P2 配额基础）。

**验收**：每次 LLM 调用都有 usage；预算超限按配置 warn/stop；报告可复现"用什么模型/提示词/参数生成"。

### 4.4 审查与检索质量

- reviewer 看到与 writer 相同的完整证据（移除 `prompts.py:153` 的 `[:3]`），并输出需求级覆盖与问题清单。
- 修订次数可配置 >1；只有问题数下降或给出接受理由才接受修订。
- 检索：相似度阈值、按 chunk 去重、MMR 多样性、按需求拆查询；证据不足时在节状态与门禁 warning，禁止静默降级。
- 记录 `retrieval_mode` / `fallback_reason`，前端可见。

### 4.5 PII 与提示注入加固

- 扩展 redaction：手机号、邮箱、身份证、银行卡、地址、密钥/token；覆盖 narrative 与表单内容。
- 招标文本按"数据非指令"处理：证据分隔、指令样模式中和、系统提示明确禁止执行文档内指令；补充对抗测试。
- MCP 外部证据维持不可信与 1/3 预算上限。

### 4.6 黄金评测集与 CI 回归

- `tests/eval/`：5–10 份脱敏招标样例 + 期望需求/关键事实/引用；录制 LLM 响应做确定性 CI，夜间跑真实模型。
- 指标：需求召回、引用有效率、无支撑事实率、审查问题数、单标书成本、耗时。
- prompt 变更必须附评测对比；阈值回归阻断合并。
- 提示词外置为版本化文件/常量，`PROMPT_VERSION` 写入报告。

**P1 关键文件**：`src/bidding/{prompts,checks,retrieval,staged,assemble,gate,compliance,staged_output}.py`、`src/bidding/llm.py`、`src/parsing/extraction/schemas.py`、`api/bid_runs.py`、`frontend/src/components/{BidSectionModal,BidRunWorkspace,BidGatePanel}.tsx`、新增 `citations.json` / `requirements.json` 产物。

## 5. P2 — 团队协作与审批（20–25 人日）

### 5.1 审批流与导出锁

- 状态机：`draft → in_review → approved | rejected`；任何编辑/重写/应用修复使审批失效（内容哈希变化）。
- run 级审批绑定 `bid.md` + 所有 selected 章节的内容哈希 + `profile_sha256`；章节可单独审批。
- 导出：未审批 → 409；`force` 仅 admin + reason + 审计；审批人不能是最后编辑者（可配置职责分离）。
- 前端审批面板：请求审查、通过/驳回、意见、状态徽章、导出按钮禁用原因。

### 5.2 版本历史、diff 与恢复

- `bid_section_versions`：每次编辑/重写/修复保存旧版本（内容、来源、作者、哈希、时间、审查摘要）。
- `GET .../sections/{id}/versions`、`GET .../diff?from=&to=`（统一 diff）、`POST .../restore`（走人工编辑语义，清 gate，留审计）。
- 前端并排 diff / 渲染 diff；大纲 revision 历史可选。

### 5.3 评论、指派与通知

- `section_comments`（作者、正文、resolved、@提及）；`section_assignments`（负责人、截止时间）。
- 通知：站内 + SMTP/Webhook（run 完成、门禁阻断、待审批、@提及、资质到期）。
- 项目看板：节状态、负责人、截止时间、阻塞原因。

### 5.4 并发保护与实时进度

- 章节/大纲 PATCH 增加 `expected_revision`；冲突返回 409 + 最新版本，前端提示 diff/重载。
- 可选 Redis 编辑租约（120s 心跳）显示"某人正在编辑"；不强行锁死。
- SSE：`run_events` 追加表 + `GET /api/bid-runs/{id}/events?after=`；worker 写事件，API 流式推送；前端用 fetch-SSE（带 Authorization），断线用 Last-Event-ID 续传；local 模式回退轮询。
- Nginx 关闭 SSE buffering，读超时覆盖长任务。

**验收**：未审批导出被拒；两人同时编辑同节，后提交者 409；版本可恢复且审计完整；SSE 在 2s 内反映状态变化。

## 6. P3 — 交付物与复用（25–30 人日）

### 6.1 高保真 DOCX/PDF

- DOCX：页眉页脚、页码域、目录域、分节、样式、图表题注与交叉引用、编号；`python-docx` 扩展 + `docxtpl` 模板注入。
- PDF：LibreOffice headless（独立容器/队列）转换，内置 Noto CJK 字体；失败回退并提示。
- 交付检查：目录可更新、页码存在、图片嵌入且 DPI 达标、字体不缺失、图表编号连续、页数上限；进入 gate（先 warn 后 block）。
- TOC 域需在 Word/LibreOffice 更新；导出时尝试自动更新，失败则明确提示。

### 6.2 模板管理

- `templates` 表 + MinIO（项目级/组织级）；DOCX 封面/页眉页脚/样式模板，变量 JSON Schema 校验；未知/缺失变量阻断导出。
- 模板版本化、预览、启用/停用；后续再支持整本文档模板。

### 6.3 企业资质与资料库

- `company_assets`：资质证书、人员证书、业绩、财务；字段含证书号、有效期、扫描件私有对象、核验状态。
- 到期提醒（P2 通知 + beat 任务）；投标时从资产库生成 profile 片段，保留出处。
- 敏感字段继续按角色掩码；资产文件不进公开 artifact allowlist。

### 6.4 跨标书内容复用

- `approved_content`：经审批的段落/章节，带 embedding、标签、来源 run/section、有效期。
- 检索作为额外证据源（`source=library`），只能复用已审批内容，并标注出处；插入前做事实冲突检查（公司/项目/日期/金额）。
- 禁止自动复用未审批 AI 文本。

### 6.5 多格式导入与 OCR

- DOCX/XLSX/HTML/ZIP 适配器 → 统一 `content_list`；无页码格式用 `locator`（段落/工作表/单元格）。
- 扫描 PDF：PyMuPDF 渲染 + PaddleOCR（中文），记录置信度，低置信度进入人工复核队列；MinerU 仍可作云端/自建选项。
- ZIP 招标包：清单、去重、逐文件解析、项目级归档。
- 若全内网要求，OCR/自建 MinerU 与 P1 本地模型一起作为"离线版"里程碑。

### 6.6 大纲增删改（可选）

- 目前固定 11 节 + 表单。真实项目若需要增删/重排，再开放白名单操作，并同步合规、评分映射、配图规则与模板；默认不做自由编辑。

**验收**：DOCX 交付检查清单全过；模板变量缺失被拦截；资质到期可见；复用内容有出处且事实一致；DOCX/XLSX/扫描件可解析并标注置信度。

## 7. P4 — 规模化运维（持续）

- **可观测性**：请求 ID 贯穿 API→Celery；JSON 日志（user/project/run/job）；Prometheus 指标（延迟、5xx、队列深度、run 失败率、LLM 错误/成本、存储错误）；Grafana 面板与告警；可选 OpenTelemetry + Tempo。
- **Celery 可靠性**：`acks_late=True` + `task_reject_on_worker_lost=True`（先完成幂等验证）、瞬时错误重试退避、任务时间上限、`prefetch_multiplier=1`；parse/bid 独立队列与 worker；beat 负责清理、到期提醒、备份触发（或主机 cron）。
- **性能**：文档注册表分页、搜索分页、关键索引（`bid_runs(project_id, created_at)`、`audit_events(project_id, ts)`、`section_versions(section_id, version)`）；Chroma 元数据过滤与重建策略；健康检查缓存。
- **容灾与配额**：Postgres PITR/每日备份、MinIO 版本化 + 异地镜像、Chroma 可重建；RPO/RTO 目标与季度演练；项目级存储/运行/Token 配额。
- **测试**：Playwright e2e（登录→上传→run→审批→导出）、Vitest 前端单测、k6/Locust 压测（10/50 并发）、契约测试；上线前安全测评。
- **离线版（如需要）**：vLLM/SGLang + bge-m3 + PaddleOCR + 自建 MinerU，全部走内网网关；作为独立交付变体。

## 8. 技术选型清单

| 能力 | 推荐 | 说明 / 替代 |
|---|---|---|
| 密码与会话 | `argon2-cffi` + DB 不透明 token | 已实现；简单、可撤销；后续可加 OIDC/LDAP 适配层 |
| RBAC | FastAPI 依赖 + `project_members` | 5 角色；项目级授权；不引入 Casbin，保持简单 |
| 审计 | PostgreSQL 追加表 + 哈希链 | 可查询、可校验；更强需求可加 MinIO WORM 锚定 |
| 部署 | Docker Compose + Nginx | 内网单机/小集群足够；后续再上 K8s |
| CI | GitHub Actions（或 GitLab CI） | pytest / lint / build / docker / 安全扫描 |
| 备份 | `pg_dump` + `restic` + MinIO 镜像 | 加密、可离线；季度恢复演练 |
| 可观测性 | Prometheus + Grafana + JSON 日志 | 可选 Loki/Tempo；先覆盖核心指标 |
| 实时进度 | SSE（fetch 流） | 比 WebSocket 简单；断线续传；Nginx 关 buffering |
| LLM 网关 | 应用内 OpenAI 兼容客户端 + LiteLLM/One-API sidecar | 统一 key/路由/限流；应用保持薄 |
| Embedding | OpenAI 兼容接口；智谱适配器保留 | 内网可用 bge-m3（Xinference/vLLM/Ollama） |
| 文档转换 | python-docx + docxtpl + LibreOffice headless | PDF 稳定、可容器化；保留 Word 人工终检 |
| OCR | PaddleOCR（中文） | 本地 CPU 可跑；低置信度人工复核 |
| 评测 | pytest 评测集 + 录制回放 | 可进 CI；夜间真实模型回归 |
| 前端测试 | Vitest + Playwright | 目前前端零测试，先覆盖登录/审批/导出主链路 |

## 9. 数据模型与迁移

新增表（按阶段）：

- **P0**：`users`、`user_sessions`、`projects`、`project_members`、`documents`、`project_documents`、`audit_events`。
- **P1**：`document_requirements`（或先 JSON 产物）、`run_usage`（成本/用量）。
- **P2**：`bid_section_versions`、`section_comments`、`section_assignments`、`run_events`、`notifications`、审批字段/表。
- **P3**：`templates`、`company_assets`、`approved_content`。

迁移策略：

- `0003` 已用于 `users`/`user_sessions`；后续从 `0004` 起递增。先加 nullable 列/表，回填"默认项目"，再加非空约束。
- 现有 `upload/parsed|bids` 文件按 doc_id/run_id 建立注册表记录；MinIO key 不变，ACL 在 API 层。
- 切换 embedding 模型时新建集合 + 后台重建；旧集合保留只读一个版本周期。
- 所有迁移在 CI 空库 + 生产副本演练；迁移前自动备份。

## 10. 速赢清单（可与 P0 并行，1–2 周）

1. 引用标记 + 确定性校验（4.1 的最小版）。
2. reviewer 去掉 3 条证据上限。
3. `MeteredChatClient` 接入 token 统计。
4. `report.json` 写入模型/参数/prompt 哈希/版本。
5. 检索阈值与低证据 warning。
6. PII 正则扩展 + 表单也 redaction。
7. 需求覆盖矩阵最小版（JSON + 报告）。
8. SSE 进度（先只推送 run 级状态）。

## 11. 明确不做（本阶段非目标）

- 公有云多租户 SaaS、在线计费/订阅。
- 移动端 App、微信小程序。
- 无人值守自动投标、自动签章、自动报价决策。
- 文生图/自由绘图/自动排版创意。
- 开放式 Agent 聊天替代固定投标流程。
- OA/CRM/合同管理全栈。
- 初期不做 MCP OAuth、插件市场、跨组织联邦。
- 不做 DOC（97-2003）导出，PDF/DOCX 优先。

## 12. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 标书/企业资料出境合规 | 无法交付 | P0 模型网关可切换；离线版选项；数据边界文档；默认关闭外部 MCP/绘图 |
| 认证/权限漏配 | 数据泄露 | 路由默认鉴权、角色矩阵测试、跨项目测试、代码评审清单 |
| 生成质量不稳定/模型升级回归 | 用户不信任 | 评测集 + 引用覆盖 + 审批；固定模型版本；报告溯源 |
| 成本失控 | 预算超支 | 用量核算、预算上限、并发限制、缓存 |
| 迁移损坏现有数据 | 无法回滚 | 迁移前备份、默认项目回填、演练、灰度 |
| LibreOffice 排版差异 | 交付不合格 | 交付检查清单、模板锁定、人工抽检、必要时 Windows Word 转换 |
| 范围蔓延 | 延期 | 阶段出口标准；非目标清单；每阶段独立上线 |
| 单人维护瓶颈 | 运维风险 | CI/CD、文档、备份演练、至少两人掌握部署 |

## 13. 里程碑与验证

| 里程碑 | 内容 | 验证方式 |
|---|---|---|
| M1 内网可上线 | P0 | 干净 VM 部署；角色矩阵测试；跨项目隔离测试；审计校验；备份恢复演练；CI 全绿 |
| M2 可信生成 v1 | P1 | 3 份真实标书跑通；引用覆盖率/需求覆盖率/成本报告；评测集回归；人工抽查 |
| M3 团队协作 v1 | P2 | 3–5 人试点：分工、评论、审批、导出锁、并发冲突、SSE |
| M4 交付复用 v1 | P3 | 交付检查清单；模板/资质库/复用试点；DOCX/XLSX/扫描件导入 |
| M5 规模化 | P4 | 压测 50 并发；监控告警演练；容灾演练；安全测评 |

**试点指标（建议基线，按实际校准）**

- 单份标书编写工时下降 ≥40%。
- 关键事实引用覆盖率 ≥80%；强制需求覆盖率 100%。
- 门禁一次通过率 ≥60%（不含强制导出）。
- 运行失败率 <5%；P95 API 延迟 <500ms（非生成类）。
- 单标书 LLM 成本可见且不超过预算。

**如何验证路线图本身**

- 选 2–3 个真实历史标书做回放：记录现状工时、门禁问题数、人工修改量、成本；P1 后同口径对比。
- 每个阶段结束做用户验收（bid manager + writer + reviewer 各至少 1 人），不通过不进入下一阶段。

## 14. 落地方式与下一步

P0-A（认证/RBAC）已落地，其余阶段仍按独立实施计划推进。建议顺序：

1. 把 P0 剩余部分拆成 3 个独立实施计划：项目隔离、审计、部署/CI/备份；每个计划单独评审与验收。
2. 在 P0 开发同时，用现有系统跑 2–3 份真实标书建立质量/成本基线，作为 P1 评测集的种子。
3. P0 上线前完成一次安全自查（认证绕过、越权、上传、CORS、默认口令、日志泄露）。
4. 每个阶段结束按第 13 节做用户验收，不通过不进入下一阶段。
