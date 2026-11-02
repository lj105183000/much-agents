from __future__ import annotations

import json

from scripts.eval_generate import build_command, main


def _case(tmp_path):
    labels = tmp_path / "eval-data" / "labels" / "doc-1"
    labels.mkdir(parents=True)
    (labels / "manifest.json").write_text(
        json.dumps({"case_id": "doc-1", "doc_id": "doc-1"}),
        encoding="utf-8",
    )


def test_build_command_uses_private_parsed_dir(tmp_path) -> None:
    command = build_command(
        python="python",
        doc_id="doc-1",
        profile="profile.json",
        max_sections=1,
        run_id="run-1",
        bids_dir=tmp_path / "bids",
    )
    assert command[:4] == ["python", "-m", "src.cli", "write"]
    assert "--no-chroma" in command
    assert "--out" in command


def test_dry_run_lists_cases(tmp_path, capsys) -> None:
    _case(tmp_path)
    code = main(
        [
            "--data-dir",
            str(tmp_path / "eval-data"),
            "--bids-dir",
            str(tmp_path / "bids"),
            "--dry-run",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["doc_id"] == "doc-1"
    assert payload["results"][0]["run_id"].startswith("eval-nightly-")
