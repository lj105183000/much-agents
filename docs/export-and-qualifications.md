# 交付物、DOCX 模板与企业资质库

> 状态：已实现（2026-11-02，2026-11-03 增加模板变量与封面占位）。DOC 导出复用 Headless LibreOffice；DOCX 支持全局默认模板、项目级模板库、模板变量/封面占位与分页预览；资质库提供公司级证书 CRUD 与到期状态展示。

## 1. Word 97-2003（.doc）导出

- 接口：`POST /api/bid-runs/{run_id}/export/doc?force=false&reason=...`
- 与 DOCX/PDF 相同的严格门禁与权限：普通导出需要全局 `bid_manager`/`admin` 且项目 `manager`；`force=true` 仅交互式 admin，且必须提供 reason；审计事件 `bid_run.export_doc`、`bid_run.export_doc.force`。
- 实现：`src/bidding/doc_export.py` 调用 `soffice --convert-to "doc:MS Word 97"`，校验 OLE Compound File magic `D0 CF 11 E0 A1 B1 1A E1` 后原子替换；产物 `bid.doc` 与 `bid.pdf` 一样不在 artifact allowlist，只能走严格导出。
- 缓存：`bid.doc` mtime 晚于 `bid.docx` 且 magic 正确时直接复用；否则重新转换。人工编辑、章节重写、大纲失效、应用修复、重新配图会删除本地与远程 `bid.doc`（`_clear_reports` + `DERIVED_REPORT_FILES`）。
- 可用性：LibreOffice 与 PDF 导出共用；`GET /api/health` 返回 `doc_export_available`。未安装时接口 503，前端按钮禁用并提示。
- 前端：门禁面板新增「导出 DOC」；门禁 block 时 admin 可「强制导出 DOC」（需 reason 确认）。
- 指标：`much_agents_doc_exports_total{result}`、`much_agents_doc_export_duration_seconds{result}`。

## 2. DOCX 样式模板

### 2.1 全局默认模板

```ini
BID_DOCX_TEMPLATE=/opt/much-agents/templates/corporate-bid.docx
```

- 可选、全局、仅支持 `.docx`；留空使用内置版式。
- 行为：以模板为基底，删除模板正文示例内容（保留 `sectPr`），保留页边距、页眉页脚、Normal/Heading 等样式；随后写入标题、TOC、正文、表格、图片与页码字段。
- 不覆盖模板样式：使用模板时不再强制宋体/黑体、页边距与内置页脚，避免破坏企业模板。
- 模板缺失或后缀错误会让组装/导出明确失败（`BidExportError`），不会静默回退；生产更换模板前应先用样本 run 验证。
- 项目没有启用模板库条目时，持久化 bid run 使用该全局模板；CLI 与 legacy `POST /api/bids` 始终只使用该全局模板。

### 2.2 项目模板库（Alembic `0021`）

项目可以维护多个 DOCX 模板并选择其中一个启用。模板文件存对象存储私有前缀，元数据存 PostgreSQL：

| 字段 | 说明 |
|---|---|
| `id` / `project_id` | 模板主键；项目外键级联删除 |
| `name` / `description` | 展示名称与说明 |
| `filename` / `object_key` / `sha256` / `size_bytes` | 原文件名、存储 key、内容哈希与大小 |
| `is_active` | 项目内唯一启用标记；由部分唯一索引 `uq_bid_templates_active_project` 保证 |
| `version` | 预留版本号（当前恒为 1，重新上传会创建新模板） |
| `created_by` / `created_by_name` / `created_at` / `updated_at` | 创建人与时间 |

| 方法 | 路径 | 权限 |
|---|---|---|
| `GET` | `/api/templates` | 项目只读 |
| `POST` | `/api/templates` | 项目 `manager`（multipart：`file`、`name`、`description`） |
| `PATCH` | `/api/templates/{template_id}` | 项目 `manager`（`name`、`description`、`is_active`） |
| `DELETE` | `/api/templates/{template_id}` | 项目 `manager` |
| `GET` | `/api/templates/{template_id}/download` | 项目只读 |
| `GET` | `/api/templates/{template_id}/preview?page=N` | 项目只读 |

模板接口均需 `X-Project-Id`，只能访问当前项目内的模板。

- 对象 key 为 `private/templates/<project_id>/<template_id>.docx`，不在 artifact allowlist，也不进入 MCP 工具输出或 run 产物目录。
- 上传校验（`src/bidding/docx_template.py`）：仅 `.docx`、≤10 MB；必须是 zip，包含 `[Content_Types].xml` 与 `word/document.xml`；拒绝含 `word/vbaProject*` 的宏模板；条目 ≤2000、解压总量 ≤100 MB；必须能被 python-docx 打开。校验通过后计算 SHA-256。
- 启用模板优先于 `BID_DOCX_TEMPLATE`；停用或删除后回退全局模板/内置版式。
- 组装时把启用模板下载到临时目录，`markdown_to_docx` 使用后立即清理；启用模板文件丢失返回 409，不静默回退。
- 模板切换只影响**下一次组装**；已组装 run 的 `bid.docx` 不会自动重建。人工编辑、章节重写、大纲失效等仍按原规则清空派生产物。
- 模板预览：`GET /api/templates/{template_id}/preview?page=N`。首次请求下载 DOCX，经 Headless LibreOffice 转 PDF（与 PDF 导出共用进程内信号量），PDF 缓存在 `private/template-previews/<project_id>/<template_id>/<sha256前16位>.pdf`；再用 PyMuPDF 渲染指定页为 PNG。响应头 `X-Template-Preview-Page` / `X-Template-Preview-Pages` 给出页码/总页数（CORS 已暴露），页越界返回 400，LibreOffice 不可用返回 503，模板文件丢失返回 409。删除模板会清理预览缓存。
- 审计：`template.create`（名称/SHA-256/大小）、`template.update`（变更字段）、`template.delete`。
- 前端「编写标书」顶部的「DOCX 模板」面板支持查看、预览（分页 PNG）、下载、上传、启用/停用与删除；只有项目管理员看到写操作，只读角色可查看、预览与下载。LibreOffice 不可用时隐藏预览按钮并提示。

### 2.3 模板变量与封面占位

模板正文、表格、页眉和页脚支持 `{{变量}}`。允许的变量由 `src/bidding/docx_export.py` 的 `TEMPLATE_VARIABLES` 固定，包括：`project_name`、`project_code`、`bid_section`、`tenderer`、`agency`、`location`、`scope`、`duration_days`、`quality_requirement`、`max_price_text`、`bid_deadline`、`bid_open_time`、`planned_start`、`planned_end`、`bid_validity_days`、`company_name`、`legal_representative`、`authorized_agent`、`project_manager`、`date`。

- `{{content}}` 是正文插入点：模板中该段落之前的内容作为封面/固定页保留；该段落及其后的模板示例内容会被删除，生成正文追加在封面之后。
- 没有 `{{content}}` 时保持原行为：清空模板正文，仅保留样式、页边距、页眉页脚与 `sectPr`。
- 变量值来自招标文件事实与投标人 profile；缺失值渲染为 `【待填写：标签】`，不会编造。
- 未知变量（`content` 除外）会让组装/导出明确失败（`BidExportError`），不会静默替换为空。
- 变量替换优先按 run 保留格式；跨 run 拆分的占位符会退化为单 run 文本。
- 该功能对项目模板库、`BID_DOCX_TEMPLATE`、CLI 与 legacy `POST /api/bids` 一致生效。
- 当前边界：没有模板变量/封面占位符、未知变量阻断、公司级/组织级模板、模板版本历史或整本文档模板；legacy `POST /api/bids` 与 CLI 不读取项目模板库。

## 3. 企业资质库

### 3.1 数据

Alembic `0016` 新增 `company_qualifications`，`0023` 增加扫描件/OCR 字段：

| 字段 | 说明 |
|---|---|
| `id` / `company_id` | 主键，公司外键级联删除 |
| `name` | 资质/证书名称，必填 |
| `qualification_type` | 施工资质/安许/ISO/人员证书等 |
| `certificate_no` / `issuer` | 证书编号、发证机关 |
| `issued_at` / `expires_at` | 日期；到期日可空 |
| `notes` | 备注 |
| `file_key` / `file_name` / `file_sha256` / `file_size` | 私有扫描件对象元数据 |
| `ocr_status` / `ocr_text` / `ocr_updated_at` | `pending/text/ocr/unavailable/failed` 与提取文本 |
| `created_by` / `created_at` / `updated_at` | 审计快照 |

索引 `(company_id, expires_at)`，列表按到期日升序（未填到期日排最后）。

### 3.2 API

公司库是全局共享资源，不要求 `X-Project-Id`：

| 方法 | 路径 | 权限 |
|---|---|---|
| `GET` | `/api/companies/{company_id}/qualifications` | 登录用户 |
| `POST` | `/api/companies/{company_id}/qualifications` | `require_manage`（admin/bid_manager） |
| `PATCH` | `/api/companies/{company_id}/qualifications/{qualification_id}` | `require_manage` |
| `DELETE` | `/api/companies/{company_id}/qualifications/{qualification_id}` | `require_manage` |
| `POST` | `.../qualifications/{qualification_id}/file` | `require_manage`（multipart `file`，PDF ≤10MB） |
| `GET` | `.../qualifications/{qualification_id}/file` | 登录用户 |

- 名称必填、去首尾空白；到期日不得早于发证日（422）。
- 扫描件仅接受 PDF（1–200 页、无加密），保存到私有对象 `private/qualifications/<company_id>/<qualification_id>.pdf`；删除资质时同步删除对象。
- 上传后异步 OCR：优先 PyMuPDF 文本层；无文本且配置 `MINERU_API_KEY` 时走 MinerU OCR；否则 `ocr_status=unavailable`。OCR 失败只更新状态，不删除扫描件。
- 审计：`company.qualification.create/update/delete/file`，只记录 id、类型、到期日、文件名/哈希/大小与变更字段。
- 公司不存在返回 404；证书不属于该公司返回 404。
- 到期提醒：Celery 每日扫描 `QUALIFICATION_EXPIRY_DAYS`（默认 30）内到期的资质，通知使用该公司的项目的 `manager`；同一证书/到期日/剩余天数去重，受用户「资质到期通知」偏好控制。

### 3.3 前端

「公司库」每张公司卡片新增「资质」按钮，打开弹窗：

- 列表展示名称、类型、证书号、发证机关、发证/到期日、备注与状态徽章。
- 状态：已过期（红）、30 天内到期（黄）、有效（绿）、未填到期日（灰）。
- 有管理权限时可新增/编辑/删除，并上传/替换扫描件；只读角色可查看与下载扫描件。
- 展示 OCR 状态（已提取/处理中/失败/未配置）与扫描件文件名。
- 到期提醒走站内通知与可选 Webhook/SMTP；当前没有资质与 profile 字段自动合并。

## 4. 测试

- `tests/test_doc_export.py`：LibreOffice 缺失、DOCX 缺失、magic 校验、原子写入。
- `tests/test_bid_run_api.py`：DOC 导出成功/失败、文件失效、权限与审计路径（复用 gate 测试）。
- `tests/test_docx_template.py`：模板页边距/页脚保留、示例正文清除、缺失模板报错。
- `tests/test_bid_template_api.py`：上传校验、CRUD/启停/下载、项目隔离、启用模板进入组装、模板对象丢失阻断组装，以及预览渲染/缓存/分页越界/LibreOffice 不可用。
- `tests/test_company_api.py`：资质 CRUD、日期校验、未知公司与未知证书。
- `tests/test_qualification_scan_api.py`：扫描件上传/下载、非 PDF 拒绝、OCR 状态。
- `tests/test_qualification_expiry.py`：到期通知、去重与偏好关闭。
- Playwright：公司库资质弹窗展示种子证书。

## 5. 尚未实现

- 模板变量/封面占位符、未知变量阻断、公司级/组织级模板、模板版本历史与整本文档模板。
- legacy `POST /api/bids` 与 CLI 不支持项目模板库（仍只用全局模板）。
- 资质与 profile 字段自动合并、扫描件与投标文件自动引用、证书扫描件图片格式（当前仅 PDF）。
- DOC 复杂排版与内置模板的像素级一致性仍需人工抽查。
