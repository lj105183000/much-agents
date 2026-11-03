# LLM 基准测试、价格生效日期与成本对账

> 状态：已实现（2026-11-02）。新增 `scripts/eval_benchmark.py`、`scripts/reconcile_costs.py`；`src/bidding/llm_usage.py` 的 `estimate_cost` 支持 `effective_from` 多版本价格。2026-11-03 起，结构化抽取的 LLM 调用也写入 `llm_call_log`（`run_id=NULL`、带 `job_id`/`project_id`），并计入项目月度成本与运营看板；生产环境要求价格表覆盖当前模型（启用回退时含 `deepseek-chat`），除非显式设置 `ALLOW_UNPRICED_LLM=true`。

## 1. 真实 LLM 基准测试（预算封顶）

`scripts/eval_benchmark.py` 使用内置的合成提示词（writer + reviewer JSON）调用真实 DeepSeek，记录每次调用的 token、延迟、成本、`finish_reason`，并汇总：

- `cases / ok / failed / skipped / unpriced`
- prompt/completion/total tokens
- 总成本、p50/p95 延迟、tokens/秒、每千 completion token 成本
- 每用例状态与输出长度（不保存 prompt/正文）

### 1.1 运行

```bash
# 只看用例与上限，不调用模型
uv run python -m scripts.eval_benchmark --dry-run

# 生成基线（需要 DEEPSEEK_API_KEY 与 LLM_PRICE_TABLE）
uv run python -m scripts.eval_benchmark \
  --max-cost-cny 0.5 \
  --update-baseline eval-data/benchmarks/llm-baseline.json

# 与基线比较；p95/成本/token 任一超过阈值退出 1
uv run python -m scripts.eval_benchmark \
  --max-cost-cny 0.5 \
  --baseline eval-data/benchmarks/llm-baseline.json \
  --output /tmp/llm-benchmark.json
```

- `--max-cost-cny`（默认 0.5）在每次调用前检查：达到后不再发起下一次调用（单次调用可能使总额略超），结果标记 `budget_exhausted`，退出码 2；设为 0 表示不设上限。
- 默认要求 `LLM_PRICE_TABLE` 对当前模型有生效价格；否则退出 2。仅测延迟时可加 `--allow-unpriced`（成本字段为 null）；未加该参数时出现 `unpriced` 用例会使退出码为 1。
- 回归阈值：`--max-latency-regression`（默认 0.5）、`--max-cost-regression`（默认 0.3）、`--max-token-regression`（默认 0.5）。
- 自定义用例：`--cases path.json`，结构为数组或 `{"cases": [...]}`，字段 `case_id/system/user/operation/max_tokens/temperature/json_mode`。

### 1.2 夜间 workflow

`.github/workflows/eval-nightly.yml` 新增 `benchmark` job，默认不运行。启用条件：

- 仓库变量 `LLM_BENCHMARK_ENABLED=true`
- Secrets：`DEEPSEEK_API_KEY`、`LLM_PRICE_TABLE`（JSON 文本或文件路径）
- 可选变量：`BENCHMARK_MAX_COST_CNY`（默认 0.5）、`BENCHMARK_BASELINE`（runner 上的基线路径）
- 报告以 `llm-benchmark` artifact 上传，失败也会上传

首次运行没有基线时，下载 artifact 后作为下一次的 `BENCHMARK_BASELINE`（或提交到私有 runner 的固定路径）。

## 2. 价格生效日期

`load_price_entries` / `select_price_entry` / `estimate_cost(..., as_of=date)` 支持同一模型多版本价格：

```json
{
  "models": {
    "deepseek-flash": [
      {"input_per_1k_cny": 0.001, "output_per_1k_cny": 0.002,
       "effective_from": "2026-01-01"},
      {"input_per_1k_cny": 0.0012, "output_per_1k_cny": 0.0024,
       "effective_from": "2026-07-01"}
    ]
  }
}
```

- 选择 `effective_from` 不晚于调用日期的最新条目；未写 `effective_from` 的条目始终生效。
- 若调用日期早于所有条目，`estimate_cost` 返回 `None`（不会静默套用未来价格）。
- 兼容旧格式：模型直接映射单个对象时行为不变。
- `MeteredChatClient` 在调用时按当天选择价格；`scripts.reconcile_costs` 按每条 `llm_call_log.ts` 的日期重算。

## 3. 成本对账

`scripts/reconcile_costs.py` 只读 `llm_call_log` 与 `external_usage`，按时间窗口和可选项目过滤，输出：

- LLM：调用数、已记录成本、按 token + 生效价格重算的成本、差额、未定价调用数、按模型拆分
- 外部用量：按 provider 的记录数/单位/成本（MinerU、Zhipu Embedding）
- 发票核对：每个 provider 的 `metered`、`billed`、差额、差额百分比、是否在容差内；`metered` 使用账本已记录成本
- 重算核对：`stored` 与 `recomputed` 是否在容差内（`--strict-reprice` 时参与退出码）

```bash
# 用发票总额核对本月
uv run python -m scripts.reconcile_costs \
  --since 2026-11-01 --until 2026-11-30 \
  --llm-invoice-cny 12.34 \
  --mineru-invoice-cny 2.00 \
  --zhipu-invoice-cny 0.40

# 等价写法
uv run python -m scripts.reconcile_costs \
  --invoice llm=12.34 --invoice mineru=2.00 --invoice zhipu=0.40

# 用 JSON 发票文件 + 严格重算
uv run python -m scripts.reconcile_costs \
  --invoice-json invoices/2026-11.json \
  --price-table deploy/prices.json \
  --strict-reprice
```

- 容差：`--tolerance-pct`（默认 5%），每个 provider 另有 0.01 元绝对下限。
- 退出码：0 在容差内；1 发票或严格重算不匹配；2 输入/配置错误；3 数据库不可用。
- 只打印聚合值，不输出 prompt、正文、profile、token 或调用 id。
- 成本仍是按价格表估算；发票金额需从供应商账单人工填入。价格表未配置时重算不可用，但发票核对仍会执行。

## 4. 边界

- 夜间基准只覆盖 2 个合成用例，不代表完整 11 节生成；完整质量回归仍用 `eval_generate`/`eval_regression`。
- 未自动抓取 DeepSeek/MinerU/智谱账单，也未做币种换算或税额处理。
- fallback 到未定价模型时该次调用会标记 `unpriced`，需在价格表中补该模型。
- 基准/对账报告不含真实标书或投标人资料；可安全上传 CI artifact。
