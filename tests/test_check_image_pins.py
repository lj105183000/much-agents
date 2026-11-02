from __future__ import annotations

from scripts.check_image_pins import find_unpinned, main


def test_find_unpinned_flags_latest_only(tmp_path) -> None:
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "\n".join(
            [
                "services:",
                "  a:",
                "    image: postgres:16-alpine",
                "  b:",
                "    image: minio/minio:latest",
                "  c:",
                "    image: chromadb/chroma@sha256:abc",
            ]
        ),
        encoding="utf-8",
    )

    assert find_unpinned(compose) == ["minio/minio:latest"]
    assert main([str(compose)]) == 1


def test_main_accepts_pinned_compose(tmp_path) -> None:
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  a:\n    image: postgres:16-alpine\n", encoding="utf-8"
    )
    assert main([str(compose)]) == 0
