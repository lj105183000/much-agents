# Private evaluation dataset

This directory contains the schema, scripts and synthetic samples for the L1
evaluation set. **Real tender data must not be committed here.** Build it under
the gitignored `eval-data/` directory (or point `EVAL_DATA_DIR` at a private
volume).

## Layout

```text
eval-data/
  tenders/<case_id>/
    extraction.json
    toc.json
    chunks.jsonl
    full.md
    content_list.json        # optional
    meta.json                # optional
  labels/<case_id>/
    evaluation_factors.json  # generated draft, human-reviewed
    key_facts.json           # generated draft, human-reviewed
    must_cover_points.json   # empty template, filled by domain experts
    reference_outline.json   # generated from toc.json
    manifest.json
```

## Build

```bash
uv run python -m scripts.eval_dataset build --all
uv run python -m scripts.eval_dataset build --doc-id 6055594244948 --overwrite
uv run python -m scripts.eval_dataset list
uv run python -m scripts.eval_dataset validate
```

The builder only copies already-parsed artifacts and generates draft labels from
`extraction.json` / `toc.json`. It never calls an LLM.

## Human annotation

```bash
uv run python -m scripts.eval_annotate export --case-id <doc_id>
# edit eval-data/annotations/<doc_id>.csv in Excel/LibreOffice
uv run python -m scripts.eval_annotate import --case-id <doc_id> --mark-reviewed
uv run python -m scripts.eval_annotate validate --case-id <doc_id>
uv run python -m scripts.eval_annotate status
```

The CSV has `factor`, `point` and `fact` rows. Confirm factor text/max_score,
fill 3–8 judgeable points per high-value factor, and review fact values/pages.
Set the row `status` to `reviewed` when done. Do not delete factor rows; new
points/facts may leave `id` blank. `--mark-reviewed` requires all three label
files reviewed and syncs `manifest.json`. See `docs/eval-annotation.md` for the
full contract. Keep labels and CSV in the private directory only.

## Human review checklist

```bash
uv run python -m scripts.eval_review
```

Writes `eval-data/reviews/<case_id>.md` with factors, candidate points and key
facts as checkboxes. Reviewers should edit the labels and set
`must_cover_points.json` to `reviewed`; the checklist itself is not a label.

## L2 judge and calibration

The judge is blinded and uncalibrated by default. The workbook flow is:

```bash
uv run python -m scripts.eval_l2 export --run-dir upload/bids/<doc>/<run>
# fill eval-data/l2/<run>/human.csv (verdict + optional 1-5 scores)
uv run python -m scripts.eval_l2 judge --dir eval-data/l2/<run> --limit 20
uv run python -m scripts.eval_l2 import --dir eval-data/l2/<run> \
  --min-samples 20 --min-agreement 0.8 --min-kappa 0.6 --require-ready
```

`export` builds `samples.jsonl` (title/requirements/text/evidence), a blank
`human.csv`, and a manifest. `judge` writes only rubric scores/verdicts (no
input text). `import` aligns by `sample_id`, reports agreement, Cohen's kappa,
per-dimension MAE/±1 and overall MAE, and fails with `--require-ready` until
sample size and thresholds pass. Single-section `eval_judge` and pair-based
`eval_calibrate` remain available. See `docs/l2-judge-calibration.md`.

## L0 synthetic fixture and CI gate

`tests/eval/fixtures/l0/` is checked in and contains only synthetic data:

```text
tests/eval/fixtures/l0/
  bids/case-l0/run-l0/report.json
  baseline.json
```

The normal CI backend job runs:

```bash
uv run python -m scripts.eval_rule_baseline check \
  --fixtures tests/eval/fixtures/l0 \
  --baseline tests/eval/fixtures/l0/baseline.json
```

The scorecard reads persisted `report.json` only. It checks weighted/narrative
coverage, placeholder and issue density, fact consistency, compliance and
consistency counts, gate blocks, requirement/citation coverage, and invalid
citations. Update the baseline only after an intentional fixture or metric
change:

```bash
uv run python -m scripts.eval_rule_baseline build \
  --fixtures tests/eval/fixtures/l0 \
  --output tests/eval/fixtures/l0/baseline.json
```

Do not add real tender text, real company/profile data, or L1 labels here.

## Nightly regression

`.github/workflows/eval-nightly.yml` is disabled unless the repository variable
`EVAL_NIGHTLY_ENABLED=true` and a self-hosted runner provides
`EVAL_DATA_DIR`, `EVAL_BIDS_DIR`, `EVAL_BASELINE`, plus the model secrets.
It validates the private dataset, checks `EVAL_BASELINE` with
`scripts.eval_regression --check-baseline --require-l1`, runs
`scripts.eval_generate` with `EVAL_SECTIONS` (default 1), then compares with
`--require-baseline`. If the baseline is missing or incomplete, the workflow
bootstraps it from the current runs with `--update-baseline`, uploads it as the
`eval-baseline` artifact, and skips comparison for that run. Manual dispatch can
set `refresh_baseline=true` to rebuild it intentionally. `EVAL_SECTIONS` must
match the baseline; changing it requires a refresh.

```bash
# Rebuild the baseline manually (same section count as the nightly run)
uv run python -m scripts.eval_regression \
  --data-dir "$EVAL_DATA_DIR" \
  --bids-dir "$EVAL_BIDS_DIR" \
  --baseline "$EVAL_BASELINE" \
  --update-baseline --require-l1
```

Reviewed labels feed L1 scoring: must-cover points are matched against
`bid.md`/`sections/*.md` with deterministic 2-gram coverage, facts by value
substring, plus requirements/citation summaries. `eval_regression` compares
`avg_point_recall`, `avg_weighted_point_recall`, `avg_fact_coverage`,
`avg_requirement_coverage` and `avg_citation_coverage` with
`--max-l1-drop`. `scripts.baseline_metrics` records the same L1 metrics. This
is a lexical proxy, not semantic judgment; keep L2/human review as the
authority. See `docs/eval-annotation.md`.

## Privacy rules

- Never commit `profile.json`, real company names, phone numbers, certificate
  numbers, bank accounts, addresses or complete tender text.
- `eval-data/` is gitignored; the public repository only keeps this README,
  builder code, schemas and synthetic fixtures.
- CI must use synthetic cases or replay cassettes; real-model night runs mount
  the private directory explicitly.
