from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from api.artifacts import (
    BID_PATTERNS,
    ArtifactError,
    job_artifact_root,
    list_artifacts,
    resolve_artifact,
)
from api.jobs import Job
from tests.fakes import make_bid_settings, make_parse_settings


def test_staged_job_artifacts_use_run_id_and_hide_profile(tmp_path: Path) -> None:
    run_id = "run-abc"
    run_dir = tmp_path / "bids" / "doc-1" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "bid.md").write_text("# bid", encoding="utf-8")
    (run_dir / "bid.docx").write_bytes(b"PK")
    (run_dir / "gate.json").write_text("{}", encoding="utf-8")
    (run_dir / "figures").mkdir()
    (run_dir / "figures" / "fig-01.png").write_bytes(b"\x89PNG\r\n\x1a\nimage")
    (run_dir / "figures" / "manifest.json").write_text("{}", encoding="utf-8")
    (run_dir / "profile.json").write_text('{"secret": "PROFILE-SECRET"}', encoding="utf-8")

    job = Job(
        job_id="job-1",
        filename="doc-1",
        doc_id="doc-1",
        parser="",
        extract=False,
        ingest=False,
        created_at=datetime.now(timezone.utc),
        kind="bid",
        payload={"mode": "staged", "run_id": run_id},
    )
    root, patterns, _prefix = job_artifact_root(
        job,
        make_parse_settings(tmp_path),
        make_bid_settings(tmp_path),
    )
    assert root == run_dir.resolve()
    assert "gate.json" in patterns
    assert "profile.json" not in patterns

    artifacts = list_artifacts(root, patterns, "/artifacts")
    assert {item.path for item in artifacts} == {
        "bid.md",
        "gate.json",
        "figures/fig-01.png",
        "figures/manifest.json",
    }
    image = next(item for item in artifacts if item.path.endswith(".png"))
    assert image.content_type == "image/png"
    assert image.previewable is True
    assert resolve_artifact(root, patterns, "gate.json").is_file()
    assert resolve_artifact(root, patterns, "figures/fig-01.png").is_file()
    with pytest.raises(ArtifactError):
        resolve_artifact(root, patterns, "profile.json")
    with pytest.raises(ArtifactError):
        resolve_artifact(root, patterns, "bid.docx")
    with pytest.raises(ArtifactError):
        resolve_artifact(root, patterns, "figures/../profile.json")


def test_bid_patterns_never_include_profile_or_docx() -> None:
    assert "gate.json" in BID_PATTERNS
    assert "figures/manifest.json" in BID_PATTERNS
    assert "figures/*.png" in BID_PATTERNS
    assert "profile.json" not in BID_PATTERNS
    assert "bid.docx" not in BID_PATTERNS
