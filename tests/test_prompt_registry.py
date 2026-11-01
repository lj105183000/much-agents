from __future__ import annotations

from src.bidding.provenance import prompt_registry_entries


def test_prompt_registry_entries_hash_prompt_files() -> None:
    entries = prompt_registry_entries(
        model_default="deepseek-flash", temperature_default=0.2
    )
    assert len(entries) >= 3
    for entry in entries:
        assert entry["kind"].endswith("prompts") or "fix_plan" in entry["kind"] or "planner" in entry["kind"]
        assert len(entry["template_sha256"]) == 64
        assert entry["version"] == entry["template_sha256"][:12]
        assert entry["model_default"] == "deepseek-flash"
        assert entry["temperature_default"] == 0.2
    assert len({entry["kind"] for entry in entries}) == len(entries)
