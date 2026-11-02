# 交付物、DOCX 模板与企业资质库

> 状态：已实现（2026-11-02）。DOC 导出复用 Headless LibreOffice；DOCX 支持全局默认模板与项目级模板库；资质库提供公司级证书 CRUD 与到期状态展示。

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

模板接口均需 `X-Project-Id`，只能访问当前项目内的模板。

- 对象 key 为 `private/templates/<project_id>/<template_id>.docx`，不在 artifact allowlist，也不进入 MCP 工具输出或 run 产物目录。
- 上传校验（`src/bidding/docx_template.py`）：仅 `.docx`、≤10 MB；必须是 zip，包含 `[Content_Types].xml` 与 `word/document.xml`；拒绝含 `word/vbaProject*` 的宏模板；条目 ≤2000、解压总量 ≤100 MB；必须能被 python-docx 打开。校验通过后计算 SHA-256。
- 启用模板优先于 `BID_DOCX_TEMPLATE`；停用或删除后回退全局模板/内置版式。
- 组装时把启用模板下载到临时目录，`markdown_to_docx` 使用后立即清理；启用模板文件丢失返回 409，不静默回退。
- 模板切换只影响**下一次组装**；已组装 run 的 `bid.docx` 不会自动重建。人工编辑、章节重写、大纲失效等仍按原规则清空派生产物。
- 审计：`template.create`（名称/SHA-256/大小）、`template.update`（变更字段）、`template.delete`。
- 前端「编写标书」顶部的「DOCX 模板」面板支持查看、下载、上传、启用/停用与删除；只有项目管理员看到写操作，只读角色可查看与下载。
- 当前边界：没有模板变量/封面占位符、未知变量阻断、模板预览、公司级/组织级模板、模板版本历史或整本文档模板；legacy `POST /api/bids` 与 CLI 不读取项目模板库。

## 3. 企业资质库

### 3.1 数据

Alembic `0016` 新增 `company_qualifications`：

| 字段 | 说明 |
|---|---|
| `id` / `company_id` | 主键，公司外键级联删除 |
| `name` | 资质/证书名称，必填 |
| `qualification_type` | 施工资质/安许/ISO/人员证书等 |
| `certificate_no` / `issuer` | 证书编号、发证机关 |
| `issued_at` / `expires_at` | 日期；到期日可空 |
| `notes` | 备注 |
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

- 名称必填、去首尾空白；到期日不得早于发证日（422）。
- 审计：`company.qualification.create/update/delete`，只记录 id、类型、到期日与变更字段。
- 公司不存在返回 404；证书不属于该公司返回 404。

### 3.3 前端

「公司库」每张公司卡片新增「资质」按钮，打开弹窗：

- 列表展示名称、类型、证书号、发证机关、发证/到期日、备注与状态徽章。
- 状态：已过期（红）、30 天内到期（黄）、有效（绿）、未填到期日（灰）。
- 有管理权限时可新增/编辑/删除；只读角色只能查看。
- 当前没有证书扫描件上传、到期站内通知/邮件提醒、资质与 profile 字段自动合并。

## 4. 测试

- `tests/test_doc_export.py`：LibreOffice 缺失、DOCX 缺失、magic 校验、原子写入。
- `tests/test_bid_run_api.py`：DOC 导出成功/失败、文件失效、权限与审计路径（复用 gate 测试）。
- `tests/test_docx_template.py`：模板页边距/页脚保留、示例正文清除、缺失模板报错。
- `tests/test_bid_template_api.py`：上传校验、CRUD/启停/下载、项目隔离、启用模板进入组装、模板对象丢失阻断组装。
- `tests/test_company_api.py`：资质 CRUD、日期校验、未知公司与未知证书。
- Playwright：公司库资质弹窗展示种子证书。

## 5. 尚未实现

- 模板变量/封面占位符、未知变量阻断、模板预览、公司级/组织级模板、模板版本历史与整本文档模板。
- legacy `POST /api/bids` 与 CLI 不支持项目模板库（仍只用全局模板）。
- 资质扫描件对象存储、OCR 识别、到期提醒（站内/Webhook/SMTP）与投标文件自动引用。
- DOC 复杂排版与内置模板的像素级一致性仍需人工抽查。
