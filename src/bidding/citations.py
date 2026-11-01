"""Evidence citation contract and deterministic validation.

Writers are asked to append ``[E1]`` markers to sentences that state key
facts.  The markers are validated against the retrieval snapshot that was
actually shown to the writer; unsupported or out-of-range ids are reported and
the markers are stripped from exported Markdown so delivered bids stay clean.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Mapping

from .models import BidOutline

_MARKER_RE = re.compile(r"[\[【]\s*E(\d+)\s*[\]】]")
_SENTENCE_RE = re.compile(r"[^。！？；\n]+[。！？；]?")
_AMOUNT_RE = re.compile(
    r"(?<![A-Za-z0-9【.])(\d[\d,]*(?:\.\d+)?\s*(?:万元|亿元|元))"
)
_DATE_RE = re.compile(
    r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"(?:\s*\d{1,2}\s*[时:]\s*\d{1,2}\s*分?)?"
    r"|\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)"
)
_CERT_RE = re.compile(
    r"(?:证号|证书编号|身份证号|统一社会信用代码)\s*[:：]?\s*([A-Za-z0-9Xx\-]{6,})"
)


def strip_citation_markers(markdown: str) -> str:
    """Remove ``[E1]`` markers from exported text without touching other text."""

    return _MARKER_RE.sub("", markdown or "")


def _key_claim_reasons(sentence: str) -> list[str]:
    reasons: list[str] = []
    if _AMOUNT_RE.search(sentence):
        reasons.append("amount")
    if _DATE_RE.search(sentence):
        reasons.append("date")
    if _CERT_RE.search(sentence):
        reasons.append("certificate")
    return reasons


def _evidence_entry(index: int, hit: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": f"E{index}",
        "chunk_id": str(hit.get("chunk_id") or ""),
        "page_start": hit.get("page_start"),
        "page_end": hit.get("page_end"),
        "section_path": str(hit.get("section_path") or ""),
        "score": hit.get("score"),
        "text_sha256": hit.get("text_sha256"),
        "text_chars": hit.get("text_chars"),
    }


def _section_payload(
    section_id: str,
    title: str,
    markdown: str,
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    hits = list((snapshot or {}).get("hits") or [])
    evidence = [
        _evidence_entry(index, hit)
        for index, hit in enumerate(hits, start=1)
        if isinstance(hit, dict)
    ]
    by_id = {item["id"]: item for item in evidence}
    body = markdown or ""

    markers = list(_MARKER_RE.finditer(body))
    valid_markers: list[dict[str, Any]] = []
    invalid_markers: list[str] = []
    seen: set[str] = set()
    for match in markers:
        marker = match.group(0)
        evidence_id = f"E{int(match.group(1))}"
        if evidence_id not in by_id:
            invalid_markers.append(marker)
            continue
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        valid_markers.append(
            {"marker": marker, "evidence_id": evidence_id, **by_id[evidence_id]}
        )

    claims = 0
    cited = 0
    uncited: list[dict[str, Any]] = []
    for raw in _SENTENCE_RE.findall(body):
        sentence = raw.strip()
        reasons = _key_claim_reasons(sentence)
        if not reasons:
            continue
        claims += 1
        sentence_has_citation = any(
            f"E{int(match.group(1))}" in by_id
            for match in _MARKER_RE.finditer(sentence)
        )
        if sentence_has_citation:
            cited += 1
        else:
            quote = strip_citation_markers(sentence)[:200]
            uncited.append({"quote": quote, "reasons": reasons})

    warnings: list[str] = []
    if body.strip() and not snapshot:
        warnings.append("章节没有检索快照，无法校验引用")
    if invalid_markers:
        warnings.append(
            "发现无效引用编号：" + "、".join(sorted(set(invalid_markers)))
        )
    return {
        "section_id": section_id,
        "title": title,
        "written": bool(body.strip()),
        "mode": (snapshot or {}).get("mode"),
        "evidence": evidence,
        "citation_markers": len(markers),
        "citations": valid_markers,
        "invalid_markers": sorted(set(invalid_markers)),
        "key_claims": claims,
        "cited_key_claims": cited,
        "coverage": round(cited / claims, 4) if claims else 1.0,
        "uncited_claims": uncited,
        "warnings": warnings,
    }


def build_citations(
    *,
    run_id: str,
    outline: BidOutline,
    section_markdowns: Mapping[str, str],
    snapshots: Mapping[str, Mapping[str, Any]],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build ``citations.json`` for all selected narrative sections."""

    sections = [
        _section_payload(
            section.id,
            section.title,
            section_markdowns.get(section.id, ""),
            snapshots.get(section.id),
        )
        for section in outline.sections
        if section.kind == "narrative" and section.selected
    ]
    claims = sum(item["key_claims"] for item in sections)
    cited = sum(item["cited_key_claims"] for item in sections)
    markers = sum(item["citation_markers"] for item in sections)
    invalid = sum(len(item["invalid_markers"]) for item in sections)
    warnings = [
        f"{item['title']}: {warning}"
        for item in sections
        for warning in item["warnings"]
    ]
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "generated_at": (
            generated_at or datetime.now(timezone.utc)
        ).isoformat(),
        "summary": {
            "sections": len(sections),
            "sections_with_evidence": sum(
                1 for item in sections if item["evidence"]
            ),
            "evidence_items": sum(len(item["evidence"]) for item in sections),
            "citation_markers": markers,
            "invalid_markers": invalid,
            "key_claims": claims,
            "cited_key_claims": cited,
            "coverage": round(cited / claims, 4) if claims else 1.0,
            "sections_with_uncited_claims": sum(
                1 for item in sections if item["uncited_claims"]
            ),
        },
        "sections": sections,
        "warnings": warnings,
    }


__all__ = ["build_citations", "strip_citation_markers"]
