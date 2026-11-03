# 轻量评测控制台

> 状态：已实现（2026-11-03）。仅管理员可用；只读标注 + 确定性 L1 回归 + JSON 报告；不触发 LLM judge。

## 1. 定位

控制台把现有 CLI 评测能力收进管理员工作台：

- 查看评测用例、评分因素、必须覆盖点、关键事实与完成度
- 导出标注 CSV（编辑仍走 CSV + CLI 导入）
- 运行确定性 L1 回归（基于已落盘的 `report.json`，不调用 LLM）
- 对比/更新 `EVAL_BASELINE`
- 查看 JSON 运行历史与 L2 校准结果

它不是评测中台：没有在线标注编辑、实验管理、任务调度、模型/prompt A/B、L2 judge 触发或独立数据库表。

## 2. 权限与隐私

- 仅 `admin`。API 使用 `require_admin`，前端导航、App 与视图三层守卫；`service` 账号不能调用。
- 控制台是全局视图，不要求 `X-Project-Id`。
- 管理员可以查看/导出 labels（factors/points/facts）与关键事实值；这仍是私有评测数据：
  - 不提供 `full.md`、`chunks.jsonl`、`extraction.json`、`profile.json`、`reviews/*.md`、`samples.jsonl`、`human.csv`、`judge.jsonl`。
  - 不返回 `report`、`run_dir` 等绝对路径。
  - 响应带 `Cache-Control: no-store`；CSV 导出做公式注入转义。
  - 运行、更新基线、CSV 导出的审计 detail 只包含 case 数、run id、指标、是否更新基线，不含 label/正文。
- 控制台只读 `EVAL_BIDS_DIR` 的本地 run 缓存；MinIO 模式下需先完成 hydration，不扫描远程对象。

## 3. 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `EVAL_DATA_DIR` | `<project_root>/eval-data` | 私有评测集（labels、tenders、l2）；生产容器需只读挂载 |
| `EVAL_BIDS_DIR` | `bid_settings.bids_dir` | 待评测的投标 run 目录 |
| `EVAL_BASELINE` | `<UPLOAD_DIR>/baseline/latest.json` | L1 回归基线 |
| `EVAL_REPORTS_DIR` | `<UPLOAD_DIR>/eval-runs` | 控制台 JSON 运行历史，需可写 |

缺失 `eval-data` 不会阻止 API 启动；控制台显示空状态，运行返回 409/503。

### Docker 生产部署

`docker-compose.prod.yml` 的 API 服务已增加：

```yaml
volumes:
  - upload-data:/app/upload
  - ./eval-data:/app/eval-data:ro
```

- `upload-data` 保存 baseline 与 `eval-runs`，可写。
- `./eval-data` 是宿主机私有目录，只读挂载；容器用户必须可读（注意宿主机目录权限）。
- 目录不存在时 Docker 会创建空目录，控制台显示未配置/空状态，不影响其他 API。

## 4. 使用流程

1. 管理员登录，左侧导航打开「评测控制台」。
2. 查看用例表：ready 表示 factors/points/facts 全部 reviewed；点「查看标注」查看明细并导出 CSV。
3. 点「运行 L1 评测」：对 `EVAL_BIDS_DIR` 中每个 case 的最新 run 计算 L0/L1 指标；有基线时做回归比较，无基线时只记录当前指标（`ok=null`）。
4. 点「更新基线」并确认：用当前最新 run 覆盖 `EVAL_BASELINE`；要求至少一个 reviewed L1 case，写入审计。
5. 运行历史表展示 JSON 报告；点「查看」查看 checks、baseline/current/delta 与逐 case 指标。
6. L2 区域只读展示 `eval-data/l2/*/calibration.json` 的一致率、kappa、样本数与 ready；不触发 judge。

## 5. API

所有端点位于 `/api/evaluations`，仅 admin：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/overview` | 可用性、用例/ready、L1 指标、基线、最近报告、L2 计数 |
| GET | `/cases` | 用例摘要与完成度 |
| GET | `/cases/{case_id}` | 用例详情（白名单 labels/manifest/validation） |
| GET | `/cases/{case_id}/annotations.csv` | 标注 CSV（UTF-8 BOM、公式转义） |
| POST | `/runs` | `{update_baseline, confirm, l1_threshold}`；运行 L1，201 |
| GET | `/runs?limit=50` | 运行历史摘要 |
| GET | `/runs/{run_id}` | 单次报告 |
| GET | `/l2` | L2 校准摘要 |

错误：未知 case/run 404；非法 ID 404；baseline 损坏 409；无 run 409；数据目录不可用 503。

## 6. 运行历史

每次运行写入 `EVAL_REPORTS_DIR/<uuid>.json`，原子替换，包含：

```text
schema_version, id, kind, created_at, created_by,
ok, baseline_ready, updated_baseline,
summary, checks[], cases[], warnings[]
```

- 不包含招标正文、chunks、profile、证据原文或绝对路径。
- 无自动清理；列表默认最多 50 条，可手动清理目录。
- 审计事件 `eval.run` / `eval.baseline_update` / `eval.annotations_export` 是二级索引；无数据库时审计为 no-op，JSON 报告才是 durable history。

## 7. 限制与后续

- 标注编辑仍走 `scripts.eval_annotate` 的 CSV 流程；控制台只读 + 导出。
- L2 仅展示已有校准；judge/校准仍由 CLI/夜间流程执行。
- 运行选择最新 run 由 mtime 决定；报告记录选中的 run_id。
- 运行锁是进程内 `threading.Lock`；多 API worker 部署建议只开一个控制台实例。
- 文件历史无保留策略；后续可加清理/分页。
- 若需要更严格的隐私模式，可改为只返回 ID/计数，CSV 单独审批。

## 8. 验证

```bash
uv run pytest tests/test_eval_console_service.py tests/test_eval_console_api.py
uv run ruff check .
cd frontend && npm run lint && npm test && npm run build
```

手工：准备 `eval-data/` 与 `upload/bids/`，管理员登录 → 查看用例 → 运行 L1 → 更新基线（确认）→ 导出 CSV → 查看历史/L2；确认非管理员 403、无 eval-data 时为空状态、响应无绝对路径与 profile/招标正文。
