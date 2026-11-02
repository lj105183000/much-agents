from __future__ import annotations

import json

from src.bidding.content_hash import compute_run_content_sha256


def _hash(run_dir, *, profile="profile-hash", revision=1):
    return compute_run_content_sha256(
        run_dir=run_dir,
        profile_sha256=profile,
        outline_revision=revision,
        figure_manifest=run_dir / "figures" / "manifest.json",
    )


def test_content_hash_tracks_markdown_sections_profile_and_outline(tmp_path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "sections").mkdir(parents=True)
    (run_dir / "figures").mkdir()
    (run_dir / "bid.md").write_text("# bid\n", encoding="utf-8")
    (run_dir / "sections" / "a.md").write_text("section a\n", encoding="utf-8")
    (run_dir / "figures" / "manifest.json").write_text("{}", encoding="utf-8")

    baseline = _hash(run_dir)
    assert len(baseline) == 64

    (run_dir / "sections" / "a.md").write_text("changed\n", encoding="utf-8")
    assert _hash(run_dir) != baseline

    baseline = _hash(run_dir)
    assert _hash(run_dir, profile="other-profile") != baseline
    assert _hash(run_dir, revision=2) != baseline


def test_content_hash_changes_when_figure_manifest_changes(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "bid.md").write_text("# bid\n", encoding="utf-8")
    (run_dir / "figures").mkdir()
    manifest = run_dir / "figures" / "manifest.json"
    manifest.write_text('{"figures": []}', encoding="utf-8")

    baseline = _hash(run_dir)
    manifest.write_text('{"figures": [1]}', encoding="utf-8")
    assert _hash(run_dir) != baseline


def test_content_hash_ignores_figure_manifest_timestamp(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "bid.md").write_text("# bid\n", encoding="utf-8")
    (run_dir / "figures").mkdir()
    manifest = run_dir / "figures" / "manifest.json"
    payload = {"generated_at": "2026-11-01T00:00:00+00:00", "figures": [{"id": "fig-1"}]}
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    baseline = _hash(run_dir)

    payload["generated_at"] = "2026-11-01T01:00:00+00:00"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert _hash(run_dir) == baseline

    payload["figures"] = [{"id": "fig-2"}]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert _hash(run_dir) != baseline
