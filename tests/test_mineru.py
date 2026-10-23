from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from src.parsing.config import Settings
from src.parsing.errors import MineruError, MineruErrorKind
from src.parsing.parsers.mineru.client import BatchFileResult, BatchInfo, MineruClient, UploadSpec
from src.parsing.parsers.mineru.parser import MineruParser
from src.parsing.parsers.mineru.zip_utils import locate_artifacts, safe_extract


def make_zip(markdown: str = "full text", content_list: list | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("full.md", markdown)
        archive.writestr(
            "demo_content_list.json",
            json.dumps(content_list or [{"type": "text", "text": markdown, "page_idx": 0}]),
        )
    return buffer.getvalue()


def make_client(handler, settings: Settings) -> MineruClient:
    transport = httpx.MockTransport(handler)
    return MineruClient(settings, http_client=httpx.Client(transport=transport))


def test_create_batch_request(settings: Settings, tmp_path: Path) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={"code": 0, "data": {"batch_id": "b1", "file_urls": ["https://upload/1"]}},
        )

    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF")
    client = make_client(handler, settings)
    info = client.create_batch([UploadSpec("a.pdf", "d1", path)])
    assert info.batch_id == "b1"
    assert captured["body"]["model_version"] == "vlm"
    assert captured["body"]["files"][0]["data_id"] == "d1"
    assert captured["auth"].startswith("Bearer ")


def test_upload_has_no_content_type(settings: Settings, tmp_path: Path) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers.get("content-type")
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200)

    path = tmp_path / "a.pdf"
    path.write_bytes(b"%PDF")
    make_client(handler, settings).upload_file("https://upload/1", path)
    assert captured["content_type"] is None
    assert captured["auth"] is None


def test_poll_done(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "extract_result": [
                        {
                            "file_name": "a.pdf",
                            "data_id": "d1",
                            "state": "done",
                            "full_zip_url": "https://cdn/result.zip",
                        }
                    ]
                },
            },
        )

    results = make_client(handler, settings).poll_batch("b1")
    assert results[0].state == "done"
    assert results[0].full_zip_url == "https://cdn/result.zip"


def test_auth_error_classified(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": "A0202", "msg": "token invalid"})

    with pytest.raises(MineruError) as exc:
        make_client(handler, settings).create_batch(
            [UploadSpec("a.pdf", "d1", Path("a.pdf"))]
        )
    assert exc.value.kind is MineruErrorKind.AUTH


def test_page_limit_classified(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": -60006, "msg": "page limit"})

    with pytest.raises(MineruError) as exc:
        make_client(handler, settings).create_batch(
            [UploadSpec("a.pdf", "d1", Path("a.pdf"))]
        )
    assert exc.value.kind is MineruErrorKind.PAGE_LIMIT


def test_poll_timeout(settings: Settings) -> None:
    fast = replace(settings, mineru_poll_interval=0.0, mineru_max_polls=2, mineru_job_timeout=0.01)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 0, "data": {"extract_result": [{"file_name": "a.pdf", "state": "running"}]}},
        )

    with pytest.raises(MineruError) as exc:
        make_client(handler, fast).poll_batch("b1")
    assert exc.value.kind is MineruErrorKind.TIMEOUT


def test_download_zip(settings: Settings, tmp_path: Path) -> None:
    payload = make_zip()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    path = make_client(handler, settings).download_zip("https://cdn/result.zip", tmp_path / "dl")
    assert path.is_file()
    assert path.read_bytes() == payload


def test_zip_traversal_rejected(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../evil.txt", "x")
    zip_path = tmp_path / "bad.zip"
    zip_path.write_bytes(buffer.getvalue())
    with pytest.raises(MineruError):
        safe_extract(zip_path, tmp_path / "out")


def test_locate_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "extract"
    (root / "images").mkdir(parents=True)
    (root / "full.md").write_text("md", encoding="utf-8")
    (root / "x_content_list.json").write_text("[]", encoding="utf-8")
    (root / "layout.json").write_text("{}", encoding="utf-8")
    full_md, content_list, layout, images = locate_artifacts(root)
    assert full_md.name == "full.md"
    assert content_list.name == "x_content_list.json"
    assert layout.name == "layout.json"
    assert images.name == "images"


class FakeClient:
    def __init__(self) -> None:
        self.specs = []

    def create_batch(self, specs):
        self.specs = specs
        return BatchInfo("batch-1", [f"https://upload/{index}" for index in range(len(specs))])

    def upload_file(self, url, path) -> None:
        return None

    def poll_batch(self, batch_id, progress=None):
        return [
            BatchFileResult(
                file_name=spec.name,
                data_id=spec.data_id,
                state="done",
                full_zip_url=f"https://cdn/{index}.zip",
            )
            for index, spec in enumerate(self.specs)
        ]

    def download_zip(self, url, dest_dir):
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        index = int(url.rsplit("/", 1)[-1].split(".")[0])
        target = dest_dir / "result.zip"
        if index == 0:
            content = [
                {"type": "text", "text": "第一页内容", "page_idx": 0},
                {"type": "text", "text": "第二页内容", "page_idx": 1},
            ]
            markdown = "第一页内容\n第二页内容"
        else:
            content = [
                {"type": "text", "text": "第三页内容", "page_idx": 0},
                {"type": "text", "text": "第四页内容", "page_idx": 1},
            ]
            markdown = "第三页内容\n第四页内容"
        target.write_bytes(make_zip(markdown, content))
        return target


class PageLimitThenSplit(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def create_batch(self, specs):
        self.calls += 1
        if len(specs) == 1:
            self.specs = specs
            return BatchInfo("batch-1", ["https://upload/0"])
        return super().create_batch(specs)

    def poll_batch(self, batch_id, progress=None):
        if self.calls == 1:
            spec = self.specs[0]
            return [
                BatchFileResult(
                    file_name=spec.name,
                    data_id=spec.data_id,
                    state="failed",
                    full_zip_url=None,
                    err_msg="number of pages exceeds limit (200 pages), please split the file",
                )
            ]
        return super().poll_batch(batch_id, progress)


def test_page_limit_failure_retries_with_split(
    settings: Settings, sample_pdf: Path, tmp_path: Path
) -> None:
    client = PageLimitThenSplit()
    document = MineruParser(settings, client=client).parse(
        sample_pdf, "sample", raw_dir=tmp_path / "raw"
    )
    assert client.calls == 2
    assert document.page_count == 4
    assert {block.page for block in document.content_list} == {1, 2, 3, 4}


def test_mineru_parser_splits_and_offsets(settings: Settings, sample_pdf: Path, tmp_path: Path) -> None:
    split_settings = replace(
        settings,
        mineru_max_pages_per_file=2,
        mineru_split_pages=2,
    )
    raw_dir = tmp_path / "raw"
    document = MineruParser(split_settings, client=FakeClient()).parse(
        sample_pdf, "sample", raw_dir=raw_dir
    )
    assert document.parser_used == "mineru"
    assert document.page_count == 4
    assert {block.page for block in document.content_list} == {1, 2, 3, 4}
    assert "第四页内容" in document.markdown
    assert (raw_dir / "part_001_full.md").is_file()
    assert (raw_dir / "part_002_full.md").is_file()
