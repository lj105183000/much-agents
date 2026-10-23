from __future__ import annotations

import json
from pathlib import Path

from src.bidding.errors import BidContextError
from src.cli import main
from tests.fakes import make_bid_result


def _summary(capsys) -> dict:
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    return json.loads(lines[-1])


def test_cli_success(tmp_path: Path, monkeypatch, capsys) -> None:
    result = make_bid_result(tmp_path)
    monkeypatch.setattr("src.cli.run_bid_writing", lambda *args, **kwargs: result)
    code = main(["write", "doc-1", "--run-id", "cli-run"])
    assert code == 0
    summary = _summary(capsys)
    assert summary["kind"] == "bid"
    assert summary["ok"] is True
    assert summary["run_id"] == "run-1"


def test_cli_invalid_max_sections(tmp_path: Path, capsys) -> None:
    code = main(["write", "doc-1", "--max-sections", "-1"])
    assert code == 2
    assert _summary(capsys)["exit_code"] == 2


def test_cli_missing_profile(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing-profile.json"
    code = main(["write", "doc-1", "--profile", str(missing)])
    assert code == 2
    assert _summary(capsys)["exit_code"] == 2


def test_cli_generation_error(tmp_path: Path, monkeypatch, capsys) -> None:
    def fail(*args, **kwargs):
        raise BidContextError("parsed artifacts missing")

    monkeypatch.setattr("src.cli.run_bid_writing", fail)
    code = main(["write", "missing-doc"])
    assert code == 2
    summary = _summary(capsys)
    assert summary["ok"] is False
    assert "parsed artifacts missing" in summary["error"]
