# L2 judge 与人工标签校准

> 状态：已实现（2026-11-02）。从 run 导出盲评样本 → 人工 CSV 标注 → LLM judge → 一致率/kappa/维度校准 → `calibration.json` 门禁结论。真实正文只存 `eval-data/`（gitignored）。

## 1. 流程

```bash
# 1. 从已组装 run 导出样本 + 空白人工表
uv run python -m scripts.eval_l2 export \
  --run-dir upload/bids/<doc_id>/<run_id>

# 2. 人工填写 eval-data/l2/<run_id>/human.csv
#    verdict: pass | revise | reject；维度/overall 可选 1-5/1-5 平均分

# 3. 运行盲评 judge（需要 DEEPSEEK_API_KEY；--limit 控制成本）
uv run python -m scripts.eval_l2 judge \
  --dir eval-data/l2/<run_id> --limit 20

# 4. 校准并生成门禁结论（未达阈值时 --require-ready 退出 1）
uv run python -m scripts.eval_l2 import \
  --dir eval-data/l2/<run_id> --min-samples 20 \
  --min-agreement 0.8 --min-kappa 0.6 --require-ready
```

## 2. 样本与盲评边界

`export` 从 run 目录读取：

- `outline.json`：narrative + selected 章节、标题、requirements、评分因素
- `sections/<id>.md`：章节正文（截断到 `--max-chars`，默认 20000）
- `citations.json`：章节 evidence 的 chunk id；通过 `--parsed-dir`（默认 `upload/parsed`）从 `chunks.jsonl` 取回证据文本，截断 6000 字
- 不读取 `profile.json`、模型名、prompt 版本、报价或其他投标人敏感资料

产物：

```text
eval-data/l2/<run_id>/
  samples.jsonl      # 盲评输入（标题/要求/正文/证据）——私有
  human.csv          # 人工标注表（不含正文）
  judge.jsonl        # judge 结果（不保存输入正文）
  calibration.json   # 一致率/kappa/维度/门禁
  calibration.md     # 人类可读摘要
  manifest.json
```

`human.csv` 列：`sample_id,section_title,verdict,relevance,evidence_support,coverage,structure,factual_safety,overall,notes`。空白 verdict 视为未标注。

## 3. judge

- 复用 `src/evaluation/judge.py` 的 `judge-v1` rubric：相关性、证据支撑、覆盖度、结构、事实安全，5 个 1–5 分维度 + issues + verdict。
- judge 只接收样本字段；输出 `judge.jsonl`，不保存输入正文。
- `--dry-run` 只统计样本数；`--limit` 限制调用量。
- 单节旧入口 `scripts/eval_judge.py` 仍保留。

## 4. 校准与门禁

`scripts/eval_l2.py import`：

- 以 `sample_id` 对齐人工与 judge；缺失人工/缺失 judge 都列入报告。
- 一致率与 Cohen's kappa（`pass|revise|reject` 标签）。
- 每个维度的 MAE 与 ±1 一致率（仅双方都填写的样本）。
- `overall` MAE（人工填写 overall 时）。
- **ready 条件**：共同样本 ≥ `--min-samples`、无缺失、agreement ≥ 0.8、kappa ≥ 0.6。
- 默认 exit 0（报告 always written）；传 `--require-ready` 未达标 exit 1，用于发布/夜间门禁。
- 结论写入 `calibration.json` 与 `calibration.md`。

## 5. 使用边界

- 校准只证明 judge 在**当前人工样本与 rubric 版本**上一致；换模型、prompt、rubric 或样本分布后需重新校准。
- 人工评分仍有主观性；建议双人交叉复核 20% 样本并记录分歧。
- 未校准前 judge 不得作为唯一发布门禁；L0/L1 回归与人工复核仍需保留。
- 当前没有 Web 标注 UI、自动 judge 调度或 judge 成本预算；`--limit` 是唯一成本闸门。

## 6. 测试

`tests/test_eval_l2.py` 覆盖样本/证据构建、盲评调用、人工表解析、一致率/kappa、维度 MAE、缺失样本和 gate 降级。
