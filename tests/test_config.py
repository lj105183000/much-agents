from __future__ import annotations

from pathlib import Path

from src.parsing.config import Settings


def test_defaults_are_safe() -> None:
    settings = Settings.from_env(env={})
    assert settings.chunk_size == 700
    assert settings.chunk_overlap == 120
    assert settings.mineru_max_pages_per_file == 200
    assert settings.chroma_collection == "bidding_documents"
    assert settings.mineru_api_key is None


def test_env_overrides(tmp_path: Path) -> None:
    settings = Settings.from_env(
        env={
            "PARSED_DIR": str(tmp_path / "out"),
            "CHUNK_SIZE": "500",
            "EXTRACTION_ENABLED": "false",
            "MINERU_API_KEY": "key",
        }
    )
    assert settings.parsed_dir == tmp_path / "out"
    assert settings.chunk_size == 500
    assert settings.extraction_enabled is False
    assert settings.mineru_api_key == "key"


def test_invalid_numbers_fall_back() -> None:
    settings = Settings.from_env(
        env={"CHUNK_SIZE": "abc", "MINERU_POLL_INTERVAL": "-3", "MINERU_MAX_POLLS": "0"}
    )
    assert settings.chunk_size == 700
    assert settings.mineru_poll_interval == 5.0
    assert settings.mineru_max_polls == 360


def test_bool_values() -> None:
    assert Settings.from_env(env={"EXTRACTION_ENABLED": "YES"}).extraction_enabled is True
    assert Settings.from_env(env={"EXTRACTION_ENABLED": "off"}).extraction_enabled is False
    assert Settings.from_env(env={"PYMUPDF_TABLES": "true"}).pymupdf_tables is True


def test_relative_parsed_dir_is_under_project_root(tmp_path: Path) -> None:
    settings = Settings.from_env(env={"PARSED_DIR": "./data/parsed"})
    assert settings.parsed_dir.is_absolute()
    assert settings.parsed_dir.name == "parsed"
