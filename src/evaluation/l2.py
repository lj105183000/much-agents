"""L2 judge sampling and human-label calibration.

Workbook layout under ``eval-data/l2/<run_id>/``:

- ``samples.jsonl``: private blinded judge inputs (title/requirements/text/evidence)
- ``human.csv``: reviewer sheet with blank verdict/score columns
- ``judge.jsonl``: blinded judge output, no input text
- ``calibration.json``: agreement/kappa/dimension report and gate decision
- ``calibration.md``: human-readable summary

The judge never receives bidder profile data, model names or prompt versions.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from .calibration import calibration_report
from .judge import DIMENSIONS, RUBRIC_VERSION, judge_section

WORKBOOK_DIR = Path("eval-data") / "l2"
HUMAN_HEADERS = (
    "sample_id",
    "section_title",
    "verdict",
    "relevance",
    "evidence_support",
    "coverage",
    "structure",
    "factual_safety",
    "overall",
    "notes",
)
VERDICTS = {"pass", "revise", "reject"}


class L2Error(ValueError):
    """Raised for invalid L2 workbooks or labels."""


@dataclass(frozen=True, slots=True)
class HumanLabel:
    sample_id: str
    verdict: str
    scores: dict[str, int] = field(default_factory=dict)
    overall: float | None = None
    notes: str = ""


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise L2Error(f"missing file: {path}") from exc
    except ValueError as exc:
        raise L2Error(f"invalid JSON: {path}: {exc}") from exc


def _load_chunks(parsed_dir: Path | None, doc_id: str) -> dict[str, str]:
    if parsed_dir is None:
        return {}
    path = parsed_dir / doc_id / "chunks.jsonl"
    if not path.is_file():
        return {}
    chunks: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict):
                continue
            chunk_id = str(item.get("id") or "")
            text = str(item.get("text") or "")
            if chunk_id and text:
                chunks[chunk_id] = text
    return chunks


def build_l2_samples(
    run_dir: Path,
    *,
    parsed_dir: Path | None = None,
    max_sections: int = 0,
    max_chars: int = 20000,
) -> list[dict[str, Any]]:
    outline = _load(run_dir / "outline.json")
    if not isinstance(outline, dict):
        raise L2Error("outline.json must be an object")
    doc_id = str(outline.get("doc_id") or run_dir.parent.name)
    citations_path = run_dir / "citations.json"
    citations = _load(citations_path) if citations_path.is_file() else {}
    citation_sections = {
        str(item.get("section_id")): item
        for item in (citations.get("sections") or [])
        if isinstance(item, dict)
    } if isinstance(citations, dict) else {}
    chunks = _load_chunks(parsed_dir, doc_id)

    samples: list[dict[str, Any]] = []
    for section in outline.get("sections") or []:
        if not isinstance(section, dict):
            continue
        if section.get("kind") != "narrative" or not section.get("selected"):
            continue
        section_id = str(section.get("id") or "")
        path = run_dir / "sections" / f"{section_id}.md"
        if not section_id or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            continue
        requirements = [
            str(item).strip()
            for item in section.get("requirements") or []
            if str(item).strip()
        ]
        for factor in section.get("evaluation_factors") or []:
            if not isinstance(factor, dict):
                continue
            name = str(factor.get("factor") or "").strip()
            if name and name not in requirements:
                requirements.append(name)
        evidence_items = []
        citation = citation_sections.get(section_id) or {}
        for item in citation.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            evidence_text = chunks.get(str(item.get("chunk_id") or ""))
            if evidence_text:
                evidence_items.append(evidence_text)
        evidence = "\n\n".join(evidence_items)[:6000]
        samples.append(
            {
                "sample_id": section_id,
                "section_id": section_id,
                "section_title": str(section.get("title") or section_id),
                "requirements": requirements,
                "text": text[: max(1000, int(max_chars))],
                "evidence": evidence,
            }
        )
        if max_sections and len(samples) >= max_sections:
            break
    return samples


def export_workbook(
    run_dir: Path,
    *,
    output_dir: Path | None = None,
    parsed_dir: Path | None = None,
    max_sections: int = 0,
    max_chars: int = 20000,
) -> dict[str, Any]:
    samples = build_l2_samples(
        run_dir,
        parsed_dir=parsed_dir,
        max_sections=max_sections,
        max_chars=max_chars,
    )
    run_id = run_dir.name
    target = output_dir or (WORKBOOK_DIR / run_id)
    target.mkdir(parents=True, exist_ok=True)
    samples_path = target / "samples.jsonl"
    with samples_path.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
    human_path = target / "human.csv"
    with human_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HUMAN_HEADERS)
        writer.writeheader()
        for sample in samples:
            writer.writerow(
                {
                    "sample_id": sample["sample_id"],
                    "section_title": sample["section_title"],
                    "verdict": "",
                    "notes": "",
                }
            )
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "doc_id": str((_load(run_dir / "outline.json") or {}).get("doc_id") or ""),
        "rubric_version": RUBRIC_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "samples": len(samples),
        "files": {
            "samples": samples_path.name,
            "human": human_path.name,
        },
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "run_id": run_id,
        "output_dir": str(target),
        "samples": len(samples),
        "samples_path": str(samples_path),
        "human_path": str(human_path),
    }


def load_samples(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict) or not item.get("sample_id"):
                    raise L2Error("each sample needs sample_id")
                rows.append(item)
    except FileNotFoundError as exc:
        raise L2Error(f"missing samples file: {path}") from exc
    except ValueError as exc:
        raise L2Error(f"invalid samples JSONL: {path}: {exc}") from exc
    return rows


class JsonJudgeClient(Protocol):
    def complete_json(self, *, system: str, user: str, **kwargs: Any) -> Any: ...


def judge_samples(
    llm: JsonJudgeClient,
    samples: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for sample in samples:
        result = judge_section(
            llm,
            section_title=str(sample.get("section_title") or ""),
            requirements=sample.get("requirements") or [],
            text=str(sample.get("text") or ""),
            evidence=str(sample.get("evidence") or ""),
        )
        results.append(
            {
                "sample_id": str(sample.get("sample_id")),
                "section_title": str(sample.get("section_title") or ""),
                "rubric_version": result.get("rubric_version") or RUBRIC_VERSION,
                **result,
            }
        )
    return results


def _score(value: str, *, sample_id: str, dimension: str) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        number = int(text)
    except ValueError as exc:
        raise L2Error(
            f"human score must be an integer: {sample_id}/{dimension}"
        ) from exc
    if not 1 <= number <= 5:
        raise L2Error(
            f"human score must be 1-5: {sample_id}/{dimension}"
        )
    return number


def load_human_sheet(path: Path) -> tuple[dict[str, HumanLabel], list[str]]:
    labels: dict[str, HumanLabel] = {}
    missing: list[str] = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if "sample_id" not in (reader.fieldnames or []):
                raise L2Error("human.csv needs a sample_id column")
            for row in reader:
                sample_id = (row.get("sample_id") or "").strip()
                if not sample_id:
                    continue
                verdict = (row.get("verdict") or "").strip().lower()
                if not verdict:
                    missing.append(sample_id)
                    continue
                if verdict not in VERDICTS:
                    raise L2Error(
                        f"invalid human verdict for {sample_id}: {verdict}"
                    )
                scores = {
                    dimension: _score(
                        row.get(dimension) or "",
                        sample_id=sample_id,
                        dimension=dimension,
                    )
                    for dimension in DIMENSIONS
                }
                overall_text = (row.get("overall") or "").strip()
                overall = None
                if overall_text:
                    try:
                        overall = float(overall_text)
                    except ValueError as exc:
                        raise L2Error(
                            f"human overall must be numeric: {sample_id}"
                        ) from exc
                labels[sample_id] = HumanLabel(
                    sample_id=sample_id,
                    verdict=verdict,
                    scores={
                        key: value for key, value in scores.items() if value is not None
                    },
                    overall=overall,
                    notes=(row.get("notes") or "").strip(),
                )
    except FileNotFoundError as exc:
        raise L2Error(f"missing human sheet: {path}") from exc
    return labels, missing


def load_judge_results(path: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict) or not item.get("sample_id"):
                    raise L2Error("each judge result needs sample_id")
                verdict = str(item.get("verdict") or "").strip().lower()
                if verdict not in VERDICTS:
                    raise L2Error(
                        f"invalid judge verdict for {item.get('sample_id')}: "
                        f"{verdict!r}"
                    )
                item["verdict"] = verdict
                results[str(item["sample_id"])] = item
    except FileNotFoundError as exc:
        raise L2Error(f"missing judge results: {path}") from exc
    except ValueError as exc:
        raise L2Error(f"invalid judge JSONL: {path}: {exc}") from exc
    return results


def _mean_absolute_error(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    return round(sum(abs(left - right) for left, right in pairs) / len(pairs), 3)


def _within_one(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    return round(
        sum(1 for left, right in pairs if abs(left - right) <= 1) / len(pairs),
        4,
    )


def calibrate_l2(
    human: dict[str, HumanLabel],
    judge: dict[str, dict[str, Any]],
    *,
    min_samples: int = 20,
    min_agreement: float = 0.8,
    min_kappa: float = 0.6,
) -> dict[str, Any]:
    common = sorted(set(human) & set(judge))
    missing_human = sorted(set(judge) - set(human))
    missing_judge = sorted(set(human) - set(judge))
    pairs = [(human[sample_id].verdict, str(judge[sample_id].get("verdict") or "")) for sample_id in common]
    report = calibration_report(pairs)
    dimensions: dict[str, dict[str, Any]] = {}
    overall_pairs: list[tuple[float, float]] = []
    for dimension in DIMENSIONS:
        score_pairs = [
            (float(human[sample_id].scores[dimension]), float(judge[sample_id].get("scores", {}).get(dimension)))
            for sample_id in common
            if dimension in human[sample_id].scores
            and isinstance(judge[sample_id].get("scores"), dict)
            and judge[sample_id]["scores"].get(dimension) is not None
        ]
        dimensions[dimension] = {
            "n": len(score_pairs),
            "mae": _mean_absolute_error(score_pairs),
            "within_one": _within_one(score_pairs),
        }
    for sample_id in common:
        human_overall = human[sample_id].overall
        judge_overall = judge[sample_id].get("overall")
        if human_overall is not None and judge_overall is not None:
            overall_pairs.append((float(human_overall), float(judge_overall)))
    agreement = report.get("agreement")
    kappa = report.get("cohens_kappa")
    ready = bool(
        len(common) >= max(1, min_samples)
        and not missing_human
        and not missing_judge
        and agreement is not None
        and float(agreement) >= min_agreement
        and kappa is not None
        and float(kappa) >= min_kappa
    )
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n": len(common),
        "min_samples": min_samples,
        "agreement": agreement,
        "cohens_kappa": kappa,
        "labels": report.get("labels") or [],
        "missing_human": missing_human,
        "missing_judge": missing_judge,
        "thresholds": {
            "agreement": min_agreement,
            "cohens_kappa": min_kappa,
        },
        "ready": ready,
        "dimensions": dimensions,
        "overall": {
            "n": len(overall_pairs),
            "mae": _mean_absolute_error(overall_pairs),
        },
    }


def render_calibration(report: dict[str, Any]) -> str:
    lines = [
        "# L2 judge 校准报告",
        "",
        f"- 样本数：{report.get('n')} / 最低 {report.get('min_samples')}",
        f"- 一致率：{report.get('agreement')}",
        f"- Cohen's kappa：{report.get('cohens_kappa')}",
        f"- 结论：{'可作门禁' if report.get('ready') else '未达门禁'}",
        f"- 缺失人工标签：{len(report.get('missing_human') or [])}",
        f"- 缺失 judge 结果：{len(report.get('missing_judge') or [])}",
        "",
        "## 维度一致性",
        "",
        "| 维度 | n | MAE | ±1 一致率 |",
        "|---|---:|---:|---:|",
    ]
    for dimension, item in (report.get("dimensions") or {}).items():
        lines.append(
            f"| {dimension} | {item.get('n')} | {item.get('mae')} | {item.get('within_one')} |"
        )
    lines += [
        "",
        "> 阈值：一致率 ≥ "
        f"{report.get('thresholds', {}).get('agreement')}，kappa ≥ "
        f"{report.get('thresholds', {}).get('cohens_kappa')}，且无缺失样本。",
        "",
    ]
    return "\n".join(lines)


__all__ = [
    "HUMAN_HEADERS",
    "HumanLabel",
    "L2Error",
    "calibrate_l2",
    "build_l2_samples",
    "export_workbook",
    "judge_samples",
    "load_human_sheet",
    "load_judge_results",
    "load_samples",
    "render_calibration",
]
