from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
from minio.error import S3Error

from src.storage import (
    StorageLayout,
    StorageObjectNotFound,
    StoragePathError,
    StorageSettings,
    StorageUnavailable,
    build_storage,
    is_safe_id,
    private_profile_key,
    run_prefix,
    validate_object_key,
)
from src.storage.local import LocalStorage
from src.storage.minio import MinioStorage


def make_layout(tmp_path: Path) -> StorageLayout:
    return StorageLayout(
        upload_dir=tmp_path / "upload",
        parsed_dir=tmp_path / "parsed",
        bids_dir=tmp_path / "bids",
    )


def make_settings(**overrides: Any) -> StorageSettings:
    values: dict[str, Any] = {
        "backend": "minio",
        "endpoint": "localhost:9000",
        "access_key": "key",
        "secret_key": "secret",
        "bucket": "much-agents-test",
    }
    values.update(overrides)
    return StorageSettings(**values)


# ---------------------------------------------------------------------------
# Settings and keys
# ---------------------------------------------------------------------------


def test_storage_settings_defaults_and_fallbacks() -> None:
    defaults = StorageSettings.from_env({})
    assert defaults.backend == "minio"
    assert defaults.endpoint == "localhost:9000"
    assert defaults.bucket == "much-agents"
    assert defaults.secure is False

    invalid = StorageSettings.from_env(
        {
            "STORAGE_BACKEND": "ftp",
            "MINIO_BUCKET": "INVALID_BUCKET",
            "MINIO_ACCESS_KEY": "",
            "MINIO_ROOT_USER": "root",
            "MINIO_SECRET_KEY": "",
            "MINIO_ROOT_PASSWORD": "pass",
        }
    )
    assert invalid.backend == "minio"
    assert invalid.bucket == "much-agents"
    assert invalid.access_key == "root"
    assert invalid.secret_key == "pass"

    local = StorageSettings.from_env({"STORAGE_BACKEND": "local"})
    assert local.backend == "local"


def test_https_endpoint_forces_secure() -> None:
    settings = StorageSettings.from_env(
        {"MINIO_ENDPOINT": "https://storage.example.com:9443/"}
    )
    assert settings.endpoint == "storage.example.com:9443"
    assert settings.secure is True


def test_object_key_validation() -> None:
    assert validate_object_key("parsed/doc-1/meta.json") == "parsed/doc-1/meta.json"
    assert is_safe_id("6055594244948-abcdef123456")
    assert is_safe_id("项目-1")
    assert not is_safe_id("..")
    for bad in (
        "",
        "/parsed/doc/meta.json",
        "parsed/../secret",
        "parsed\\doc\\meta.json",
        "unknown/doc/meta.json",
        "parsed//meta.json",
    ):
        with pytest.raises(StoragePathError):
            validate_object_key(bad)
    assert private_profile_key("doc-1", "run-1") == (
        "private/profiles/doc-1/run-1.json"
    )
    assert run_prefix("doc-1", "run-1") == "bids/doc-1/run-1"


def test_layout_maps_independent_roots(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    assert layout.local_path("parsed/doc-1/meta.json") == (
        tmp_path / "parsed" / "doc-1" / "meta.json"
    )
    assert layout.local_path("bids/doc-1/run-1/bid.md") == (
        tmp_path / "bids" / "doc-1" / "run-1" / "bid.md"
    )
    assert layout.local_path("inbox/job-1/source.pdf") == (
        tmp_path / "upload" / "inbox" / "job-1" / "source.pdf"
    )
    assert layout.local_path("private/profiles/doc-1/run-1.json") == (
        tmp_path / "upload" / "private" / "profiles" / "doc-1" / "run-1.json"
    )
    with pytest.raises(StoragePathError):
        layout.local_path("parsed/../escape")


def test_build_storage_selects_backend(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    local = build_storage(
        make_settings(backend="local"), layout
    )
    assert isinstance(local, LocalStorage)
    remote = build_storage(make_settings(), layout)
    assert isinstance(remote, MinioStorage)


# ---------------------------------------------------------------------------
# LocalStorage
# ---------------------------------------------------------------------------


def test_local_storage_roundtrip(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    storage = LocalStorage(layout)
    storage.put_bytes("parsed/doc-1/meta.json", b'{"doc_id": "doc-1"}')
    source = tmp_path / "bid.md"
    source.write_text("# bid", encoding="utf-8")
    storage.put_file("bids/doc-1/run-1/bid.md", source)

    assert storage.backend == "local"
    assert storage.exists("parsed/doc-1/meta.json")
    assert storage.get_bytes("parsed/doc-1/meta.json") == b'{"doc_id": "doc-1"}'
    info = storage.stat("bids/doc-1/run-1/bid.md")
    assert info is not None and info.size == 5
    assert {item.key for item in storage.list("parsed")} == {
        "parsed/doc-1/meta.json"
    }
    assert {item.key for item in storage.list("bids/doc-1/run-1")} == {
        "bids/doc-1/run-1/bid.md"
    }
    assert {item.key for item in storage.list("")} == {
        "parsed/doc-1/meta.json",
        "bids/doc-1/run-1/bid.md",
    }
    assert b"".join(
        storage.open_stream("bids/doc-1/run-1/bid.md", offset=2, length=1)
    ) == b"b"
    assert list(storage.iter_lines("bids/doc-1/run-1/bid.md")) == ["# bid"]

    target = tmp_path / "copy.md"
    storage.download_file("bids/doc-1/run-1/bid.md", target)
    assert target.read_text(encoding="utf-8") == "# bid"

    storage.delete("bids/doc-1/run-1/bid.md")
    storage.delete("bids/doc-1/run-1/bid.md")
    assert not storage.exists("bids/doc-1/run-1/bid.md")
    assert storage.delete_prefix("parsed") == 1
    assert not (tmp_path / "parsed" / "doc-1" / "meta.json").exists()


def test_local_storage_ensure_files_and_sync_are_noops(tmp_path: Path) -> None:
    storage = LocalStorage(make_layout(tmp_path))
    root = tmp_path / "parsed" / "doc-1"
    root.mkdir(parents=True)
    (root / "meta.json").write_text("{}", encoding="utf-8")
    missing = storage.ensure_files(
        "parsed/doc-1", root, ["meta.json", "full.md"]
    )
    assert missing == {"full.md"}
    with pytest.raises(StorageObjectNotFound):
        storage.ensure_files(
            "parsed/doc-1", root, ["full.md"], required=True
        )
    before = {path for path in tmp_path.rglob("*")}
    assert storage.sync_up(root, "parsed/doc-1").uploaded == 0
    assert storage.sync_down("parsed/doc-1", root).downloaded == 0
    assert {path for path in tmp_path.rglob("*")} == before


def test_local_storage_rejects_missing_objects(tmp_path: Path) -> None:
    storage = LocalStorage(make_layout(tmp_path))
    with pytest.raises(StorageObjectNotFound):
        storage.get_bytes("parsed/doc-1/none.json")
    assert storage.stat("parsed/doc-1/none.json") is None


# ---------------------------------------------------------------------------
# Fake MinIO client
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.closed = False
        self.released = False

    def read(self, size: int | None = None) -> bytes:
        if size is None:
            chunk = self._data
            self._data = b""
        else:
            chunk = self._data[:size]
            self._data = self._data[size:]
        return chunk

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class FakeStat:
    def __init__(self, key: str, record: "FakeRecord") -> None:
        self.object_name = key
        self.size = len(record.data)
        self.etag = "etag"
        self.last_modified = record.modified
        self.content_type = record.content_type
        self.metadata = {"X-Amz-Meta-Mtime-Ns": str(record.mtime_ns)}


class FakeRecord:
    def __init__(self, data: bytes, content_type: str, mtime_ns: int) -> None:
        self.data = data
        self.content_type = content_type
        self.mtime_ns = mtime_ns
        self.modified = datetime.now(timezone.utc)


class FakeMinio:
    def __init__(self, *, bucket: str = "much-agents-test", existing: bool = False):
        self.bucket = bucket
        self.bucket_present = existing
        self.objects: dict[str, FakeRecord] = {}
        self.created_buckets = 0
        self.get_calls: list[tuple[str, int, int | None]] = []

    def _require_bucket(self) -> None:
        if not self.bucket_present:
            raise self._error("NoSuchBucket", "bucket missing")

    @staticmethod
    def _error(code: str, message: str) -> S3Error:
        return S3Error(None, code, message, None, None, None)  # type: ignore[arg-type]

    def bucket_exists(self, bucket_name: str) -> bool:
        return bucket_name == self.bucket and self.bucket_present

    def make_bucket(self, bucket_name: str, **kwargs: Any) -> None:
        assert bucket_name == self.bucket
        self.bucket_present = True
        self.created_buckets += 1

    def _record(self, key: str) -> FakeRecord:
        self._require_bucket()
        record = self.objects.get(key)
        if record is None:
            raise self._error("NoSuchKey", "missing")
        return record

    def stat_object(self, bucket_name: str, key: str) -> FakeStat:
        assert bucket_name == self.bucket
        return FakeStat(key, self._record(key))

    def put_object(
        self,
        bucket_name: str,
        key: str,
        data: Any,
        length: int,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        assert bucket_name == self.bucket
        self._require_bucket()
        payload = data.read()
        assert len(payload) == length
        mtime = int((metadata or {}).get("mtime-ns") or 0)
        self.objects[key] = FakeRecord(payload, content_type, mtime)

    def fput_object(
        self,
        bucket_name: str,
        key: str,
        file_path: str,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        assert bucket_name == self.bucket
        self._require_bucket()
        payload = Path(file_path).read_bytes()
        mtime = int((metadata or {}).get("mtime-ns") or 0)
        self.objects[key] = FakeRecord(payload, content_type, mtime)

    def fget_object(self, bucket_name: str, key: str, file_path: str) -> None:
        assert bucket_name == self.bucket
        Path(file_path).write_bytes(self._record(key).data)

    def get_object(
        self,
        bucket_name: str,
        key: str,
        offset: int = 0,
        length: int | None = None,
    ) -> FakeResponse:
        assert bucket_name == self.bucket
        self.get_calls.append((key, offset, length))
        data = self._record(key).data[offset:]
        if length is not None:
            data = data[:length]
        return FakeResponse(data)

    def list_objects(
        self,
        bucket_name: str,
        prefix: str | None = None,
        recursive: bool = True,
        **kwargs: Any,
    ):
        assert bucket_name == self.bucket
        self._require_bucket()
        for key in sorted(self.objects):
            if prefix and not key.startswith(prefix):
                continue
            yield FakeStat(key, self.objects[key])

    def remove_object(self, bucket_name: str, key: str, **kwargs: Any) -> None:
        assert bucket_name == self.bucket
        self._require_bucket()
        self.objects.pop(key, None)


def make_remote(
    tmp_path: Path,
    *,
    existing: bool = True,
    client: FakeMinio | None = None,
) -> tuple[MinioStorage, FakeMinio]:
    fake = client or FakeMinio(existing=existing)
    storage = MinioStorage(
        make_settings(),
        make_layout(tmp_path),
        client=fake,  # type: ignore[arg-type]
    )
    return storage, fake


# ---------------------------------------------------------------------------
# MinioStorage
# ---------------------------------------------------------------------------


def test_minio_bucket_is_created_once(tmp_path: Path) -> None:
    storage, fake = make_remote(tmp_path, existing=False)
    assert not fake.bucket_present
    storage.ensure_ready()
    storage.ensure_ready()
    assert fake.bucket_present
    assert fake.created_buckets == 1
    assert storage.ping() is True


def test_ensure_ready_builds_client_without_deadlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[FakeMinio] = []

    class LazyFakeMinio(FakeMinio):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(existing=True)
            created.append(self)

    monkeypatch.setattr("src.storage.minio.Minio", LazyFakeMinio)
    storage = MinioStorage(make_settings(), make_layout(tmp_path))
    result: list[bool] = []

    def run() -> None:
        storage.ensure_ready()
        result.append(True)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "ensure_ready deadlocked acquiring its own lock"
    assert result == [True]
    assert len(created) == 1
    assert storage.ping() is True


def test_minio_put_get_stat_list(tmp_path: Path) -> None:
    storage, fake = make_remote(tmp_path)
    storage.put_bytes("parsed/doc-1/meta.json", b"{}")
    source = tmp_path / "bid.md"
    source.write_text("# bid", encoding="utf-8")
    storage.put_file("bids/doc-1/run-1/bid.md", source)

    assert storage.get_bytes("parsed/doc-1/meta.json") == b"{}"
    info = storage.stat("bids/doc-1/run-1/bid.md")
    assert info is not None
    assert info.size == 5
    assert info.content_type == "text/markdown; charset=utf-8"
    assert {item.key for item in storage.list("parsed")} == {
        "parsed/doc-1/meta.json"
    }
    storage.delete("parsed/doc-1/meta.json")
    storage.delete("parsed/doc-1/meta.json")
    assert "parsed/doc-1/meta.json" not in fake.objects


def test_minio_reads_fall_back_to_local_cache(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    legacy = layout.parsed_dir / "doc-1" / "meta.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"legacy": true}', encoding="utf-8")
    storage = MinioStorage(make_settings(), layout, client=FakeMinio())

    assert storage.get_bytes("parsed/doc-1/meta.json") == b'{"legacy": true}'
    info = storage.stat("parsed/doc-1/meta.json")
    assert info is not None and info.size == len(b'{"legacy": true}')
    assert "parsed/doc-1/meta.json" in {
        item.key for item in storage.list("parsed")
    }

    storage.put_bytes("parsed/doc-1/meta.json", b'{"remote": true}')
    assert storage.get_bytes("parsed/doc-1/meta.json") == b'{"remote": true}'


def test_minio_enumerates_inbox_and_private_prefixes(tmp_path: Path) -> None:
    storage, fake = make_remote(tmp_path)
    storage.put_bytes("inbox/job-1/source.pdf", b"%PDF-1.4")
    storage.put_bytes("private/profiles/doc-1/run-1.json", b"{}")
    assert {item.key for item in storage.list("inbox")} == {
        "inbox/job-1/source.pdf"
    }
    assert {item.key for item in storage.list("private/profiles")} == {
        "private/profiles/doc-1/run-1.json"
    }
    assert set(fake.objects) == {
        "inbox/job-1/source.pdf",
        "private/profiles/doc-1/run-1.json",
    }


def test_minio_error_mapping(tmp_path: Path) -> None:
    storage, fake = make_remote(tmp_path)

    def denied(*args: Any, **kwargs: Any):
        raise FakeMinio._error("AccessDenied", "denied")

    fake.stat_object = denied  # type: ignore[assignment]
    with pytest.raises(StorageUnavailable):
        storage.stat("parsed/doc-1/meta.json")


def test_minio_open_stream_offset_length_and_utf8_lines(tmp_path: Path) -> None:
    storage, fake = make_remote(tmp_path)
    text = ("a" * (64 * 1024 - 1)) + "中" + "\n第二行\n"
    storage.put_bytes("parsed/doc-1/chunks.jsonl", text.encode("utf-8"))
    lines = list(storage.iter_lines("parsed/doc-1/chunks.jsonl"))
    assert lines[0] == ("a" * (64 * 1024 - 1)) + "中"
    assert lines[1] == "第二行"

    data = b"0123456789"
    storage.put_bytes("parsed/doc-1/full.md", data)
    assert b"".join(
        storage.open_stream("parsed/doc-1/full.md", offset=3, length=4)
    ) == b"3456"
    assert fake.get_calls[-1] == ("parsed/doc-1/full.md", 3, 4)

    local = tmp_path / "parsed" / "doc-1" / "local.txt"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("abcdef", encoding="utf-8")
    assert b"".join(
        storage.open_stream("parsed/doc-1/local.txt", offset=2, length=3)
    ) == b"cde"


def test_minio_ensure_files_skips_matching_cache(tmp_path: Path) -> None:
    storage, fake = make_remote(tmp_path)
    source = tmp_path / "meta.json"
    source.write_text("{}", encoding="utf-8")
    storage.put_file("parsed/doc-1/meta.json", source)
    target_root = tmp_path / "cache"
    missing = storage.ensure_files(
        "parsed/doc-1", target_root, ["meta.json", "full.md"]
    )
    assert missing == {"full.md"}
    assert (target_root / "meta.json").is_file()

    first = storage.ensure_files("parsed/doc-1", target_root, ["meta.json"])
    assert first == set()

    with pytest.raises(StorageObjectNotFound):
        storage.ensure_files(
            "parsed/doc-1", target_root, ["full.md"], required=True
        )


def test_minio_sync_up_is_incremental_and_prunes(tmp_path: Path) -> None:
    storage, fake = make_remote(tmp_path)
    run_dir = make_layout(tmp_path).bids_dir / "doc-1" / "run-1"
    (run_dir / "figures").mkdir(parents=True)
    (run_dir / "bid.md").write_text("# bid", encoding="utf-8")
    (run_dir / "profile.json").write_text('{"secret": true}', encoding="utf-8")
    (run_dir / "figures" / "fig-01.png").write_bytes(b"png")

    stats = storage.sync_up(
        run_dir, "bids/doc-1/run-1", exclude={"profile.json"}
    )
    assert stats.uploaded == 2
    assert "bids/doc-1/run-1/profile.json" not in fake.objects
    assert "bids/doc-1/run-1/figures/fig-01.png" in fake.objects

    again = storage.sync_up(
        run_dir, "bids/doc-1/run-1", exclude={"profile.json"}
    )
    assert again.uploaded == 0
    assert again.skipped == 2

    (run_dir / "figures" / "fig-01.png").unlink()
    pruned = storage.sync_up(
        run_dir / "figures",
        "bids/doc-1/run-1/figures",
        delete_missing=True,
    )
    assert pruned.deleted == 1
    assert "bids/doc-1/run-1/figures/fig-01.png" not in fake.objects


def test_minio_sync_down_roundtrip(tmp_path: Path) -> None:
    storage, fake = make_remote(tmp_path)
    storage.put_bytes("parsed/doc-1/meta.json", b"{}")
    storage.put_bytes("parsed/doc-1/full.md", b"# full")
    target = tmp_path / "cache"
    stats = storage.sync_down("parsed/doc-1", target)
    assert stats.downloaded == 2
    assert (target / "meta.json").read_bytes() == b"{}"
    assert (target / "full.md").read_bytes() == b"# full"

    second = storage.sync_down("parsed/doc-1", target)
    assert second.downloaded == 0
    assert second.skipped == 2

    (target / "stale.md").write_text("stale", encoding="utf-8")
    pruned = storage.sync_down("parsed/doc-1", target, prune=True)
    assert pruned.deleted == 1
    assert not (target / "stale.md").exists()


def test_minio_get_bytes_enforces_max_size(tmp_path: Path) -> None:
    storage, fake = make_remote(tmp_path)
    storage.put_bytes("parsed/doc-1/full.md", b"0123456789")
    with pytest.raises(StoragePathError):
        storage.get_bytes("parsed/doc-1/full.md", max_bytes=4)


def test_missing_object_raises_not_found(tmp_path: Path) -> None:
    storage, fake = make_remote(tmp_path)
    with pytest.raises(StorageObjectNotFound):
        storage.get_bytes("parsed/doc-1/missing.json")
    assert storage.stat("parsed/doc-1/missing.json") is None


def test_layout_reverse_mapping(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    assert PurePosixPath("parsed/doc-1/meta.json").parts[0] == "parsed"
    assert layout.key_for_path(tmp_path / "parsed" / "doc-1" / "meta.json") == (
        "parsed/doc-1/meta.json"
    )
    assert layout.key_for_path(tmp_path / "bids" / "doc-1" / "run-1" / "bid.md") == (
        "bids/doc-1/run-1/bid.md"
    )
    assert layout.key_for_path(tmp_path / "elsewhere" / "file.md") is None
