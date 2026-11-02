# L1 评测集标注流程

> 状态：已实现（2026-11-02）。CSV 导出/导入 + 交叉校验 + 完成度状态；真实标书与标注只存 `eval-data/`（gitignored），不进入公开仓库。

## 1. 建集

```bash
uv run python -m scripts.eval_dataset build --all
uv run python -m scripts.eval_dataset list
uv run python -m scripts.eval_dataset seed-points
```

`build` 从已解析产物生成 draft labels：

```text
eval-data/
  tenders/<case_id>/{extraction,toc,chunks,full,meta}.json|md|jsonl
  labels/<case_id>/
    evaluation_factors.json   # 评分因素
    key_facts.json            # 关键事实
    must_cover_points.json    # 必须覆盖点（draft/空）
    reference_outline.json
    manifest.json
```

## 2. 导出 CSV

```bash
uv run python -m scripts.eval_annotate export --case-id <doc_id>
# 或全部 case（省略 --case-id）
uv run python -m scripts.eval_annotate export
```

输出 `eval-data/annotations/<case_id>.csv`（UTF-8 BOM，Excel/LibreOffice 可直接打开）。列：

| 列 | 说明 |
|---|---|
| `kind` | `factor` / `point` / `fact` |
| `id` | 因素/覆盖点/事实 ID；新增 point/fact 可留空，导入时自动生成 |
| `factor_id` | point 所属评分因素 |
| `category` / `factor` / `max_score` / `text` | 评分因素字段，text 为评分细则 |
| `label` / `value` / `page` | 关键事实字段 |
| `status` | `draft` / `reviewed` |

- 每个 factor 都会导出；没有覆盖点的 factor 会带一行空白 point，直接填写 `text` 即可。
- 不要删除 factor 行；导入要求每个 factor 恰好出现一次。
- 新增 point/fact 时保留 `kind`，point 填 `factor_id` + `text`；fact 填 `label` + `value` + 可选 `page`。
- 评分因素行只允许修改文本/分值/状态，ID 不可新增或删除。

## 3. 导入与校验

```bash
uv run python -m scripts.eval_annotate import --case-id <doc_id>
uv run python -m scripts.eval_annotate validate --case-id <doc_id>
```

- 导入会校验：未知 factor、重复 point/fact ID、reviewed point 缺文本、fact 缺 label/value、非法 status。
- 导入后按行状态自动设置三个文件顶层 `status`；全部 reviewed 且 points 非空时可用：

```bash
uv run python -m scripts.eval_annotate import --case-id <doc_id> --mark-reviewed
```

- `--mark-reviewed` 要求 factors/points/facts 全部 reviewed，并同步 `manifest.json`。
- `validate` 检查交叉引用、ID 唯一、文本/状态与完成度；存在错误退出 1。

## 4. 完成度

```bash
uv run python -m scripts.eval_annotate status
```

每个 case 输出 factors/factors_reviewed、factors_with_points、points/points_reviewed、facts/facts_reviewed、manifest label_status 与 `ready`。建议把 `ready` 作为 L1 使用门槛。

## 5. 人工复核与隐私

```bash
uv run python -m scripts.eval_review
```

生成 Markdown 复核清单，适合会议/双人交叉复核；最终以 CSV 导入为准。

- 至少 5–10 份脱敏标书，覆盖不同行业与评分结构。
- 关键事实双人交叉复核 20%，记录分歧；不要追求完全一致。
- 不提交 `profile.json`、公司真实信息、证书号、银行账号、完整招标正文或标注 CSV。
- CSV 导出不含正文，仅因素/事实/覆盖点；导入日志不打印真实文本。

## 6. 当前边界

- 标注仅覆盖评分因素、关键事实与必须覆盖点；章节 1–5 分人工样本仍可单独保存。
- 没有 Web UI；流程以 CLI + CSV 为主。
- L1 与回归脚本尚未自动消费 reviewed points 计算需求召回；当前回归仍以 L0 指标为主。
