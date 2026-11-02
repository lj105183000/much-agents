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

The judge is blinded and uncalibrated by default. Run it on one section:

```bash
uv run python -m scripts.eval_judge --input section.json --output judge.json
```

Compare judge labels with human labels before using it as a release gate:

```bash
uv run python -m scripts.eval_calibrate --input pairs.json
```

`pairs.json` is `[["pass","pass"],["revise","pass"],...]` or a list of
`{"human": "...", "judge": "..."}` objects. Require agreement ≥80% or
Cohen's kappa ≥0.6 before treating the judge as a gate.

## Nightly regression

`.github/workflows/eval-nightly.yml` is disabled unless the repository variable
`EVAL_NIGHTLY_ENABLED=true` and a self-hosted runner provides
`EVAL_DATA_DIR`, `EVAL_BIDS_DIR`, `EVAL_BASELINE`, plus the model secrets.
It runs `scripts.eval_generate` (one section per case), then
`scripts.eval_regression` against the baseline.

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
