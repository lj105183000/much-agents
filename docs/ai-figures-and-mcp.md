# AI 配图与 MCP

本文档说明 Much Agents 的两条扩展能力：

1. **AI 配图**：组装投标文件时，从已有招标事实、投标人资料和章节正文生成结构化示意图，自动插入 Markdown/DOCX，并带上图号、图题、来源与免责声明。
2. **MCP（Model Context Protocol）**：
   - **服务端**：把本地 FastAPI 的安全子集暴露为 MCP 工具，供 Claude Code/Desktop 等客户端调用。
   - **客户端**：一个白名单适配层，按运维配置调用外部企业资料库、检索服务或绘图工具，作为本地检索/绘图的补充。

两条能力都遵循同一安全边界：

- `profile.json` 永不进入 artifact allowlist、MCP 工具输出、Redis/Celery payload 或任何下载 URL；MinIO 模式只存私有 key `private/profiles/<doc_id>/<run_id>.json`。
- 配图与 MCP 都只使用显式传入的最小字段；外部证据默认不可信，外部绘图默认关闭。
- 配图失败、MCP 不可用或超时都只降级为警告，不阻断章节生成或组装。
- LLM 不能自由发现或调用任意 MCP 工具；工具与参数模板只能由运维在配置文件中声明。

---

## 一、AI 配图

### 1.1 产品边界

配图刻意**不使用文生图模型**。四类图都由确定性数据 + Pillow 绘制，避免生成不存在的公司、人员、证书、日期、金额或现场布置。

| 图表类型 | 触发章节 | 插入方式 |
|---|---|---|
| `org_chart` 项目组织机构图 | 七、项目管理机构表（`top-07` / `project_management`） | 章节末尾 |
| `gantt` 施工进度计划横道图 | 附件四（`att-06-04` / `attachment_schedule`） | 替换 `【待填写：施工进度网络图/横道图】` |
| `site_plan` 施工总平面布置示意图 | 附件五（`att-06-05` / `attachment_site_plan`） | 替换 `【待填写：施工总平面图】` |
| `process_flow` 主要施工工艺流程图 | 6.2 施工方案与技术措施（`narr-06-02`） | 章节末尾 |

- 只处理大纲中已选中且已生成 final 正文的章节；表单/附件使用确定性模板正文。
- `BID_FIGURES_MAX` 默认 4，按大纲顺序截断。
- 大纲新增/重排/删除目前不支持；配图规则固定，不随 LLM 输出改变。
- 图片文件名形如 `figures/fig-01-org_chart-<hash8>.png`，不包含日期、金额等易被 redaction 误伤的内容。

### 1.2 事实来源

| 图 | 数据来源 | 缺失时 |
|---|---|---|
| 组织架构图 | `profile.company.name`、`profile.project_manager`、`profile.personnel` | 无公司名则跳过；缺岗位用明确的「【待填写：岗位】」节点并记录 warning |
| 进度横道图 | `context.facts.planned_start` / `planned_end` / `duration_days` | 无日期且无工期则跳过；阶段按准备/施工/收尾示意分配，图题和注释明确标注「示意」 |
| 施工总平面图 | 章节正文中的区域关键词（办公区、生活区、材料堆放区、加工区、施工区、道路、出入口等） | 命中不足时使用标准区域示意，并标注「非现场实测，须项目技术负责人复核」 |
| 工艺流程图 | 章节正文中的有序/无序列表、短标题；不足 3 步时可选调用 DeepSeek 补足 | LLM 返回的每一步必须能在正文找到原文引用，否则丢弃；仍不足 3 步则跳过 |

可选的 LLM 只用于工艺步骤抽取，不生成新的数字/日期/人员。所有步骤会逐条校验原文。`BID_FIGURES_USE_LLM=false` 或在没有可用 key 时自动回退为纯确定性抽取。

### 1.3 渲染与中文字体

- 渲染依赖 `Pillow`；默认宽 1800px、约 200 DPI，白底工程示意配色。
- Windows 会自动探测 `msyh.ttc` / `simhei.ttf` / `simsun.ttc`，Linux 探测 Noto CJK，macOS 探测 PingFang。
- 找不到中文字体时，该图标记为失败并在 manifest / 门禁中给出警告，不阻断组装。可显式指定：

```dotenv
BID_FIGURES_FONT=C:/Windows/Fonts/msyh.ttc
```

- 单图建议控制在 8MB / 4000px 内；内置渲染不会超过该限制。

### 1.4 组装流程与产物

`BidRunService.assemble` 采用两遍组装：

1. 先做基础组装与正文 redaction，得到 `section_markdowns`（不含图）。
2. 规划并生成配图，得到 `figures/manifest.json` 与 PNG；可选调用 MCP 外部绘图工具。
3. `build_figure_blocks` 生成图片 Markdown，替换附件占位符或追加到章节末尾。
4. 再组装一次，把图片块插入正文，然后计算合规、一致性与门禁，写出 `bid.md`、`bid.docx` 和 `sections/*.md`。

插入的 Markdown 形如：

```markdown
![图 1 项目组织机构图](figures/fig-01-org_chart-1a2b3c4d.png)

*图 1 项目组织机构图*

*注：依据投标人提供的公司及人员资料绘制；标注为待填写的节点须人工补充，未提供人员不作推定。*
```

产物：

```text
upload/bids/<doc_id>/<run_id>/
  figures/
    manifest.json
    fig-01-org_chart-<hash>.png
    fig-02-gantt-<hash>.png
    ...
  bid.md
  bid.docx
  sections/*.md
  report.json   # 含 figures 统计、warnings、docx_images
  gate.json     # 含 figures warn
```

- `sections/*.md` 位于子目录，图片相对路径会写成 `../figures/...`；`bid.md` 保持 `figures/...`。
- DOCX 真实嵌入 PNG/JPEG，宽度由 `BID_FIGURES_IMAGE_WIDTH_CM` 控制（默认 15.5cm），并限制在页面可用宽度内。
- 图片缺失/格式不支持会使 DOCX 导出失败，而不是静默产出占位文字。
- 页面估算增加 `BID_FIGURES_PAGE_ESTIMATE`（默认 0.5 页/图）。

### 1.5 缓存与失效

- `manifest.json` 记录每张图的 `source_hash`（章节正文 + 图表数据/标题 + 渲染版本 + 宽度）。章节和资料未变且图片存在时直接复用。
- 以下操作会删除 `figures/` 下的 manifest 与图片：
  - 人工编辑章节正文；
  - 单节重写；
  - 大纲修改导致章节失效/重置；
  - 应用合规修复（经人工编辑语义）。
- `POST /api/bid-runs/{run_id}/figures/regenerate` 可强制重新生成配图，但不会自动重新组装；它会清空 gate 与合规/一致性报告，前端提示需要重新组装。
- run 处于 active（生成/组装中）时，重新生成配图返回 409。

### 1.6 门禁与 API

- `evaluate_gate` 新增 warn 级 `figures` 检查：有失败或跳过的图时提示「配图未全部生成，请人工补充或检查资料」。
- 配图问题不会新增 block，不改变 21 项合规检查的固定顺序和数量；只有严格 `POST /export` 会拦截 block。

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/bid-runs/{run_id}/figures` | 返回 `run_id`、`enabled`、`figures[]`、`skipped[]`、`stats`、`warnings`；无 manifest 时返回空列表 |
| `POST` | `/api/bid-runs/{run_id}/figures/regenerate` | 请求体 `{"force": true}`；同步重新生成，返回同样的 figures 响应 |

图片下载沿用 artifact allowlist：

```text
GET /api/documents/{doc_id}/bids/{run_id}/artifacts/figures/fig-01-org_chart-<hash>.png
GET /api/documents/{doc_id}/bids/{run_id}/artifacts/figures/manifest.json
```

`profile.json` 仍不会出现在 artifact 列表、下载结果或 MinIO 的 `bids/` 前缀中（只存私有 key）。

### 1.7 前端

「编写标书」页在「合规检查与一致性台账」下方显示「AI 配图」面板：

- 缩略图网格，展示图号、图题、章节、类型、来源与免责声明；点击放大。
- 统计已生成/跳过/失败/外部工具数量；可展开跳过原因与警告。
- 「重新生成配图」按钮会二次确认；成功后清空本地 gate，提示旧门禁已失效。
- run active 时按钮禁用；无图时提示「组装时自动生成」。

### 1.8 配置

```dotenv
BID_FIGURES_ENABLED=true
BID_FIGURES_MAX=4
BID_FIGURES_USE_LLM=true
BID_FIGURES_FONT=
BID_FIGURES_WIDTH_PX=1800
BID_FIGURES_IMAGE_WIDTH_CM=15.5
BID_FIGURES_PAGE_ESTIMATE=0.5
BID_FIGURES_EXTERNAL=false
```

`BID_FIGURES_EXTERNAL=false` 时不会连接外部绘图工具，即使配置了 MCP 客户端。

---

## 二、MCP 服务端

### 2.1 架构与前置条件

`api/mcp_server.py` 是本地 FastAPI 的**薄代理**。所有工具都通过 HTTP 调用现有 API，因此：

- 复用 `BidRunService` 的 revision CAS、active/lease 守卫、artifact allowlist 与 profile 排除规则；
- MCP 进程不直接访问数据库、Redis、Celery 或 run 目录；
- 使用前必须先启动 API（`start.bat` 或 `uv run uvicorn api.main:app --port 8001`）。

默认 API 地址：`http://127.0.0.1:8001`。

### 2.2 启动方式

```bash
# stdio（默认，供 Claude Code/Desktop 等本地客户端拉起）
uv run python -m api.mcp_server

# 列出已注册工具后退出（排障用）
uv run python -m api.mcp_server --check

# Streamable HTTP，仅回环
uv run python -m api.mcp_server --transport http --host 127.0.0.1 --port 8765

# 非回环绑定必须设置 token
MCP_HTTP_TOKEN=change-me uv run python -m api.mcp_server \
  --transport http --host 0.0.0.0 --port 8765
```

环境变量：

```dotenv
MCP_API_BASE_URL=http://127.0.0.1:8001
MCP_HTTP_HOST=127.0.0.1
MCP_HTTP_PORT=8765
MCP_HTTP_TOKEN=
# 业务 API 开启认证时必须设置 service token
MCP_API_TOKEN=
# 一个 MCP 进程只服务一个项目；项目类工具调用会带 X-Project-Id
MCP_DEFAULT_PROJECT_ID=
```

安全规则：

- HTTP 默认只绑定 `127.0.0.1` / `::1` / `localhost`。
- 绑定非回环地址且未配置 `MCP_HTTP_TOKEN` 时拒绝启动。
- 配置 token 后，所有 HTTP 请求必须带 `Authorization: Bearer <token>`；比较使用常量时间算法。
- 非回环绑定启用 DNS rebinding 防护；但这不等于公网安全，仍需 TLS/反代/网络访问控制。
- `AUTH_ENABLED=true` 时 MCP 通过 `MCP_API_TOKEN` 以 `service` 身份调用业务 API；未设置时工具调用返回 401。
- `MCP_DEFAULT_PROJECT_ID` 必须是合法 UUID，且 MCP 进程只能访问该项目的数据；缺失时项目类工具返回明确的 400，不会自动落到默认项目。
- API 服务不应直接暴露到公网；`profile.json` 不会出现在 MCP 工具输出中。

### 2.3 客户端配置示例

复制 `.mcp.json.example` 为 `.mcp.json`（真实文件已 gitignore）：

```json
{
  "mcpServers": {
    "much-agents": {
      "command": "uv",
      "args": ["run", "python", "-m", "api.mcp_server"],
      "env": {
        "MCP_API_BASE_URL": "http://127.0.0.1:8001"
      }
    }
  }
}
```

### 2.4 工具清单

查询类（只读）：

- `health`
- `list_documents`
- `search_tender(query, doc_id=None, top_k=5)`
- `list_companies(limit=50)`：只返回 `id/company_name/credit_code`
- `list_bid_runs(doc_id=None, status=None, limit=20)`
- `get_bid_run(run_id)`
- `get_bid_outline(run_id)`
- `get_bid_section(run_id, section_id)`
- `get_bid_gate(run_id)`
- `get_compliance(run_id)`
- `get_consistency(run_id)`
- `get_job(job_id)`
- `list_artifacts(run_id)`
- `read_artifact_text(run_id, path)`：仅文本/JSON/Markdown，强制 allowlist，`profile.json` 返回 404
- `list_bid_figures(run_id)`

写操作 / 长任务：

- `create_bid_run(doc_id, company_id=None, max_sections=None)`：不接受 inline profile；不传公司时创建空资料 run
- `patch_bid_outline(run_id, revision, sections)`
- `generate_bid_run(run_id, section_ids=None, assemble=True)`：返回 job_id，需轮询 `get_job`
- `resume_bid_run(run_id)`
- `cancel_bid_run(run_id)`
- `assemble_bid_run(run_id)`
- `edit_bid_section(run_id, section_id, markdown)`
- `regenerate_bid_section(run_id, section_id)`
- `plan_bid_fixes(run_id, check_ids=None, max_items=10)`
- `apply_bid_fix(run_id, section_id, find, replace)`
- `regenerate_bid_figures(run_id)`
- `get_bid_profile_template()`：返回空白模板，不返回任何公司实际资料

### 2.5 隐私与路径安全

- 不返回 `profile.json`、公司银行账号或 Redis/Celery payload。
- `read_artifact_text` 先校验相对路径、拒绝 `..`/绝对路径/反斜杠，再按 `BID_PATTERNS` 检查；`profile.json` 明确拒绝。
- `list_artifacts` 在 API 结果上再次过滤 `profile.json`，防止上游回归。
- 所有写操作沿用 API 的错误码与守卫，MCP 不绕过门禁或状态机。

---

## 三、MCP 客户端

### 3.1 定位

客户端是**运维配置驱动的白名单适配层**，不是通用 MCP 网关：

- 只连接 `mcp_servers.json` 中声明的服务器；
- 只调用每个服务器 `allowed_tools` 中声明的工具；
- 工具参数只能由固定占位符模板生成；
- LLM 永远不会看到或选择外部 MCP 工具；
- 默认关闭（`MCP_CLIENT_ENABLED=false`）。

三类用途：

| `purpose` | 用途 | 接入点 |
|---|---|---|
| `evidence` | 外部检索证据 | 每节 Writer 的 evidence |
| `company` | 企业资料库查询 | 每节 Writer 的 evidence |
| `figure` | 外部绘图工具 | 组装配图，仅 `site_plan` / `process_flow` |

### 3.2 配置

复制 `mcp_servers.example.json` 为 `mcp_servers.json`（真实文件已 gitignore），再设置：

```dotenv
MCP_CLIENT_ENABLED=true
MCP_CLIENT_CONFIG=./mcp_servers.json
```

示例：

```json
{
  "servers": {
    "company_kb": {
      "transport": "stdio",
      "command": "uv",
      "args": ["run", "python", "-m", "company_kb_mcp"],
      "timeout_seconds": 20,
      "allowed_tools": [
        {
          "name": "search",
          "purpose": "company",
          "arguments": {
            "query": "${query}",
            "company": "${company_name}",
            "limit": 5
          },
          "max_chars": 4000
        }
      ]
    },
    "drawing": {
      "transport": "streamable_http",
      "url": "http://127.0.0.1:9000/mcp",
      "headers": {"Authorization": "Bearer ${DRAWING_TOKEN}"},
      "timeout_seconds": 45,
      "allowed_tools": [
        {
          "name": "render_site_plan",
          "purpose": "figure",
          "arguments": {"spec": "${figure_spec}"},
          "figure_kinds": ["site_plan"]
        },
        {
          "name": "render_process_flow",
          "purpose": "figure",
          "arguments": {"spec": "${figure_spec}"},
          "figure_kinds": ["process_flow"]
        }
      ]
    }
  }
}
```

- 支持 `stdio` 与 `streamable_http`；SSE 暂不支持。
- `command`、`args`、`env`、`cwd`、`url`、`headers` 中的 `${VAR}` 会从环境变量/.env 展开；缺失变量直接报配置错误。这样 token 不必写进配置文件。
- 配置文件可包含 `"enabled": true/false`；若设置了环境变量 `MCP_CLIENT_ENABLED`，环境变量优先。
- `stdio` 服务器必须提供 `command`，不能同时设置 `url/headers`；HTTP 服务器必须提供绝对 http(s) URL，不能内嵌用户名密码。
- 单个工具 `max_chars` 范围 100–50000；`figure_kinds` 只能用于 `purpose=figure`。
- 安全上限（一般无需修改）：

```dotenv
MCP_CLIENT_MAX_SERVERS=8
MCP_CLIENT_MAX_TOOLS=64
MCP_CLIENT_MAX_CHARS=4000
MCP_CLIENT_MAX_IMAGE_BYTES=8000000
MCP_CLIENT_MAX_IMAGE_DIMENSION=4000
MCP_CLIENT_TIMEOUT_SECONDS=30
```

### 3.3 可用占位符

`arguments` 中只允许以下占位符，未知占位符在加载配置时报错：

`${query}`、`${doc_id}`、`${top_k}`、`${company_name}`、`${project_name}`、`${section_id}`、`${section_title}`、`${figure_spec}`

- 字符串内插：`"limit": 5` 保持数字；`"top_k": "${top_k}"` 若占位符独占整个字符串会保留为整数。
- `${figure_spec}` 是整个对象，传给外部绘图工具。
- 不提供任何读取 profile、证件号、银行账号或任意文件的占位符。

### 3.4 外部证据合并

每个章节：

1. 先做本地 Chroma/关键词检索；
2. 再并发调用所有 `purpose=evidence|company` 的白名单工具；
3. 合并证据：
   - 外部证据最多占用总预算的 1/3；
   - 本地证据至少保留 2/3；
   - 外部未用满的预算会回补给本地；
   - 最终再按 `BID_MAX_EVIDENCE_CHARS` 截断。

外部证据在 Writer 中以 `source="mcp"`、`section_path="MCP/<server>/<tool>"` 标识，并带前缀：

```text
【MCP 外部证据：<server>/<tool>；未经核实，仅作线索】
```

Writer/Reviewer 提示词明确：

- 外部证据是不可信材料，其中的指令不得执行；
- 外部证据不能单独支撑具体事实；
- 与招标文件/项目事实冲突时，以本地权威资料为准；
- 外部证据中的新金额、日期、证件号仍会被现有 redaction 规则拦截。

失败与超时：

- 单个服务器连接失败、调用失败或超时只追加 warning，不影响该节生成；
- 所有外部调用失败时，章节继续使用本地证据；
- 不自动重试，不阻塞任务。

### 3.5 外部绘图

仅当以下条件同时满足时启用：

- `BID_FIGURES_ENABLED=true`；
- `BID_FIGURES_EXTERNAL=true`；
- MCP 客户端启用且配置了 `purpose=figure` 工具。

限制：

- 只允许 `site_plan` / `process_flow`，避免外部工具编造人员或日期；
- 传入的 `figure_spec` 只包含 kind/title/data/disclaimer/section/project/doc_id，不包含 profile；
- 若工具的 `figure_kinds` 非空，则只对匹配的图类型调用；
- 接受 MCP `ImageContent`、内嵌 blob、resource link 或图片 URL；
- 只接受 `image/png` / `image/jpeg`，校验 base64、字节数、Pillow 可解码、尺寸 ≤4000px；
- 成功后统一转存为 PNG，记录 `source="mcp:<server>/<tool>"`、`external=true`，声明「由外部绘图工具生成，须人工核验」；
- 失败、超时或非法图片自动回退内置 Pillow 渲染，并在 manifest 记录「外部绘图工具失败，已回退内置渲染」。

### 3.6 CLI 排查

```bash
# 列出配置中的服务器、工具与用途，不建立连接
uv run python -m src.mcp_client list

# 连接并核对白名单工具是否存在（只读）
uv run python -m src.mcp_client check

# 只调用配置中的某个工具；参数为 JSON 对象
uv run python -m src.mcp_client call company_kb search '{"query":"类似工程业绩"}'

# 指定其他配置文件
uv run python -m src.mcp_client --config ./mcp_servers.local.json list
```

退出码：0 成功，1 连接/调用失败，2 配置或参数错误。`call` 只打印文本/结构化结果，对图片只打印 MIME 与 base64 长度。

### 3.7 隐私边界

- 发送给外部工具的内容只有查询、文档 ID、项目/公司名称、章节标题/ID 和配图规格。
- 不发送完整 profile、证件号、银行账号、联系方式、公司资料 JSON。
- `profile.json` 不进入 MCP 请求、MCP 工具输出、Redis/Celery payload、artifact allowlist 或任何下载 URL；MinIO 模式只存私有 key。
- 外部证据和外部图片都不能绕过本地 redaction、合规检查与门禁。

---

## 四、验证

### 自动化

```bash
uv sync
uv run pytest
cd frontend && npm run build && npm run lint
```

重点测试文件：

- `tests/test_figures_core.py`
- `tests/test_figures_assembly.py`
- `tests/test_bid_run_figures.py`
- `tests/test_mcp_server.py`
- `tests/test_mcp_client.py`

### 手工检查（Windows）

1. `start.bat local` 启动 API。
2. 创建并生成一个 run，等待自动组装。
3. 打开「编写标书」→「AI 配图」，确认四类图、图号、来源与免责声明。
4. 通过「导出 DOCX」（严格门禁）下载 `bid.docx`，确认图片真实嵌入、宽度合适、没有残留「【图片：...】」文字。
5. 编辑章节后保存，确认配图 manifest 被删除、门禁清空；重新组装后配图恢复。
6. 点击「重新生成配图」，确认提示门禁失效，再组装恢复。
7. 打开 `bid.md` 与 `sections/*.md` 预览，确认图片在浏览器和 Electron 下都能显示。

### MCP 服务端

```bash
uv run python -m api.mcp_server --check
# 启动 API 后连接客户端，依次调用：
# health → list_documents → search_tender
# create_bid_run → generate_bid_run → get_job → assemble_bid_run
# list_artifacts → list_bid_figures → read_artifact_text
```

确认：

- `read_artifact_text(run_id, "profile.json")` 返回 404；
- `list_companies` 不含银行账号；
- 非回环 HTTP 无 token 时拒绝启动。

### MCP 客户端

```bash
uv run python -m src.mcp_client list
MCP_CLIENT_ENABLED=true uv run python -m src.mcp_client check
```

生成一个章节，确认外部证据以 `MCP/...` 来源进入 Writer；停掉外部工具后确认自动回退本地检索或内置绘图；检查请求与输出中没有 profile 内容。

---

## 五、常见问题

| 现象 | 处理 |
|---|---|
| 配图全部失败且提示缺字体 | 设置 `BID_FIGURES_FONT` 指向 CJK 字体，或安装 Noto CJK |
| 某张图被跳过 | 在配图面板展开「跳过与警告」，通常是章节未生成、缺少日期/工期或步骤不足 |
| DOCX 导出提示图片缺失 | 重新组装；不要手工删除 `figures/` 下的 PNG |
| MCP 工具报 `cannot reach API` | 先启动 FastAPI，确认 `MCP_API_BASE_URL` 与端口一致 |
| 非回环 HTTP 启动失败 | 设置 `MCP_HTTP_TOKEN`，客户端加 `Authorization: Bearer` |
| MCP 客户端不影响生成 | 检查 `MCP_CLIENT_ENABLED`、配置文件路径、`allowed_tools` 的 `purpose` |
| 外部证据没有出现在正文 | 外部证据只作为线索；Writer 可能不采用。检查 `report.json` 的 warnings 与 evidence 来源 |
| 外部绘图总是回退 | 查看 manifest 警告；确认工具返回合法 PNG/JPEG、尺寸/大小未超限，且 `figure_kinds` 匹配 |

## 六、暂缓能力

- 文生图模型、自由绘图、Logo/品牌模板；
- 配图审批流与可编辑 figure spec UI；
- PDF/DOC 导出；
- MCP OAuth、远程多用户、LLM 自由调用任意 MCP 工具；
- 前端 MCP 配置 UI。
