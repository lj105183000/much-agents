"""MinIO/S3 backend with read-through local-cache fallback."""

from __future__ import annotations

import codecs
import logging
import os
import threading
from datetime import timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from uuid import uuid4

from urllib3 import PoolManager, Timeout
from urllib3.exceptions import HTTPError
from urllib3.util import Retry

from minio import Minio
from minio.error import S3Error

from .errors import (
    StorageError,
    StorageObjectNotFound,
    StoragePathError,
    StorageUnavailable,
)
from .keys import (
    join_key,
    normalize_prefix,
    relative_to_prefix,
    validate_object_key,
)
from .layout import StorageLayout
from .local import LocalStorage
from .models import ObjectInfo, SyncStats, content_type_for_name
from .settings import StorageSettings

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 64 * 1024
_NOT_FOUND_CODES = {"NoSuchKey", "NoSuchObject", "NoSuchVersion", "InvalidRange"}
_TRANSIENT_CODES = {"InternalError", "ServiceUnavailable", "SlowDown"}


def _translate(exc: S3Error) -> StorageError:
    if exc.code in _NOT_FOUND_CODES:
        return StorageObjectNotFound(str(exc))
    return StorageUnavailable(f"{exc.code}: {exc.message}")


def _metadata_value(metadata: Any, name: str) -> str | None:
    if not metadata:
        return None
    wanted = name.lower()
    prefixed = f"x-amz-meta-{wanted}"
    try:
        items = metadata.items()
    except AttributeError:
        return None
    for key, value in items:
        normalized = str(key).strip().lower()
        if normalized in {wanted, prefixed}:
            return str(value)
    return None


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class MinioStorage:
    backend = "minio"

    def __init__(
        self,
        settings: StorageSettings,
        layout: StorageLayout,
        *,
        client: Minio | None = None,
    ) -> None:
        self.settings = settings
        self.layout = layout
        self._local = LocalStorage(layout)
        self._client_instance = client
        self._lock = threading.Lock()
        self._ready = False

    # -- lifecycle -----------------------------------------------------
    def _raw_client(self) -> Minio:
        if self._client_instance is None:
            with self._lock:
                if self._client_instance is None:
                    http_client = PoolManager(
                        timeout=Timeout(
                            connect=2.0,
                            read=self.settings.timeout_seconds,
                        ),
                        retries=Retry(
                            total=2,
                            backoff_factor=0.2,
                            status_forcelist=[500, 502, 503, 504],
                            allowed_methods=frozenset(
                                {"GET", "HEAD", "PUT", "DELETE"}
                            ),
                        ),
                    )
                    self._client_instance = Minio(
                        self.settings.endpoint,
                        access_key=self.settings.access_key,
                        secret_key=self.settings.secret_key,
                        secure=self.settings.secure,
                        region=self.settings.region,
                        http_client=http_client,
                    )
        return self._client_instance

    def ensure_ready(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            client = self._raw_client()
            try:
                if not client.bucket_exists(self.settings.bucket):
                    kwargs: dict[str, Any] = {}
                    if self.settings.region:
                        kwargs["location"] = self.settings.region
                    try:
                        client.make_bucket(self.settings.bucket, **kwargs)
                    except S3Error as exc:
                        if exc.code not in {
                            "BucketAlreadyOwnedByYou",
                            "BucketAlreadyExists",
                        }:
                            raise
            except S3Error as exc:
                raise _translate(exc) from exc
            except (HTTPError, OSError) as exc:
                raise StorageUnavailable(str(exc)) from exc
            self._ready = True

    def _client(self) -> Minio:
        self.ensure_ready()
        return self._raw_client()

    def ping(self) -> bool:
        try:
            return bool(self._raw_client().bucket_exists(self.settings.bucket))
        except Exception:  # noqa: BLE001 - health must never raise
            return False

    def _call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except S3Error as exc:
            raise _translate(exc) from exc
        except (HTTPError, OSError) as exc:
            raise StorageUnavailable(str(exc)) from exc

    # -- info helpers --------------------------------------------------
    def _object_info(self, key: str, raw: Any) -> ObjectInfo:
        metadata = getattr(raw, "metadata", None)
        mtime_ns = _int_or_none(_metadata_value(metadata, "mtime-ns")) or None
        last_modified = getattr(raw, "last_modified", None)
        if last_modified is not None and last_modified.tzinfo is None:
            last_modified = last_modified.replace(tzinfo=timezone.utc)
        etag = getattr(raw, "etag", None)
        if etag:
            etag = str(etag).strip('"')
        return ObjectInfo(
            key=key,
            size=int(getattr(raw, "size", 0) or 0),
            etag=etag,
            last_modified=last_modified,
            content_type=getattr(raw, "content_type", None)
            or content_type_for_name(key),
            metadata=dict(metadata or {}) if metadata is not None else {},
            mtime_ns=mtime_ns,
        )

    def _stat_remote(self, key: str) -> ObjectInfo:
        validate_object_key(key)
        raw = self._call(
            self._client().stat_object,
            self.settings.bucket,
            key,
        )
        return self._object_info(key, raw)

    def _list_remote(self, prefix: str) -> list[ObjectInfo]:
        clean = normalize_prefix(prefix)
        client = self._client()
        rows: list[ObjectInfo] = []
        try:
            for raw in client.list_objects(
                self.settings.bucket,
                prefix=f"{clean}/" if clean else None,
                recursive=True,
            ):
                name = getattr(raw, "object_name", "")
                if not name or name.endswith("/"):
                    continue
                rows.append(self._object_info(name, raw))
        except S3Error as exc:
            raise _translate(exc) from exc
        except (HTTPError, OSError) as exc:
            raise StorageUnavailable(str(exc)) from exc
        return rows

    # -- objects -------------------------------------------------------
    def put_file(
        self,
        key: str,
        source: Path,
        *,
        content_type: str | None = None,
    ) -> None:
        validate_object_key(key)
        stat = source.stat()
        metadata = {"mtime-ns": str(stat.st_mtime_ns)}
        self._call(
            self._client().fput_object,
            self.settings.bucket,
            key,
            str(source),
            content_type=content_type or content_type_for_name(key),
            metadata=metadata,
        )

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str | None = None,
    ) -> None:
        validate_object_key(key)
        self._call(
            self._client().put_object,
            self.settings.bucket,
            key,
            BytesIO(data),
            len(data),
            content_type=content_type or content_type_for_name(key),
        )

    def _remote_bytes(self, key: str, max_bytes: int | None) -> bytes:
        response = self._call(
            self._client().get_object,
            self.settings.bucket,
            key,
        )
        try:
            if max_bytes is None:
                data = response.read()
            else:
                data = response.read(max_bytes + 1)
        finally:
            try:
                response.close()
            finally:
                response.release_conn()
        if max_bytes is not None and len(data) > max_bytes:
            raise StoragePathError(
                f"object is larger than {max_bytes} bytes: {key}"
            )
        return data

    def get_bytes(self, key: str, *, max_bytes: int | None = None) -> bytes:
        validate_object_key(key)
        try:
            return self._remote_bytes(key, max_bytes)
        except StorageObjectNotFound:
            return self._local.get_bytes(key, max_bytes=max_bytes)

    def download_file(self, key: str, target: Path) -> ObjectInfo:
        validate_object_key(key)
        info = self._stat_remote(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            self._call(
                self._client().fget_object,
                self.settings.bucket,
                key,
                str(temp),
            )
            if info.mtime_ns:
                os.utime(temp, ns=(info.mtime_ns, info.mtime_ns))
            os.replace(temp, target)
        except Exception:
            try:
                temp.unlink()
            except OSError:
                pass
            raise
        return info

    def stat(self, key: str) -> ObjectInfo | None:
        validate_object_key(key)
        try:
            return self._stat_remote(key)
        except StorageObjectNotFound:
            return self._local.stat(key)

    def exists(self, key: str) -> bool:
        try:
            return self.stat(key) is not None
        except StorageUnavailable:
            raise
        except StoragePathError:
            return False

    def list(self, prefix: str) -> list[ObjectInfo]:
        merged = {info.key: info for info in self._list_remote(prefix)}
        for info in self._local.list(prefix):
            merged.setdefault(info.key, info)
        return sorted(merged.values(), key=lambda item: item.key)

    def delete(self, key: str) -> None:
        validate_object_key(key)
        self._call(
            self._client().remove_object,
            self.settings.bucket,
            key,
        )

    def delete_prefix(self, prefix: str) -> int:
        infos = self._list_remote(prefix)
        removed = 0
        for info in infos:
            self._call(
                self._client().remove_object,
                self.settings.bucket,
                info.key,
            )
            removed += 1
        return removed

    def _get_object(self, key: str, offset: int, length: int | None) -> Any:
        kwargs: dict[str, Any] = {"offset": offset}
        if length is not None:
            kwargs["length"] = length
        return self._call(
            self._client().get_object,
            self.settings.bucket,
            key,
            **kwargs,
        )

    def open_stream(
        self,
        key: str,
        *,
        offset: int = 0,
        length: int | None = None,
    ) -> Iterator[bytes]:
        validate_object_key(key)
        if offset < 0 or (length is not None and length < 0):
            raise StoragePathError("offset and length must be non-negative")
        try:
            response = self._get_object(key, offset, length)
        except StorageObjectNotFound:
            yield from self._local.open_stream(key, offset=offset, length=length)
            return
        try:
            while True:
                chunk = response.read(_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                response.close()
            finally:
                response.release_conn()

    def iter_lines(self, key: str, *, encoding: str = "utf-8") -> Iterator[str]:
        decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        buffer = ""
        for chunk in self.open_stream(key):
            buffer += decoder.decode(chunk)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                yield line.rstrip("\r")
        buffer += decoder.decode(b"", final=True)
        if buffer:
            yield buffer.rstrip("\r")

    # -- bulk ----------------------------------------------------------
    def _local_matches(self, target: Path, info: ObjectInfo) -> bool:
        if not target.is_file():
            return False
        try:
            stat = target.stat()
        except OSError:
            return False
        if stat.st_size != info.size:
            return False
        return info.mtime_ns is None or stat.st_mtime_ns == info.mtime_ns

    def ensure_files(
        self,
        prefix: str,
        local_root: Path,
        relative_paths: Sequence[str],
        *,
        required: bool = False,
    ) -> set[str]:
        base = normalize_prefix(prefix)
        missing: set[str] = set()
        for raw in relative_paths:
            key = join_key(base, raw)
            relative = relative_to_prefix(key, base)
            target = local_root / relative
            try:
                info = self._stat_remote(key)
            except StorageObjectNotFound:
                # Legacy local data remains readable in MinIO mode.
                if target.is_file():
                    continue
                missing.add(relative)
                continue
            if self._local_matches(target, info):
                continue
            self.download_file(key, target)
        if required and missing:
            raise StorageObjectNotFound(
                "missing objects: " + ", ".join(sorted(missing))
            )
        return missing

    def sync_up(
        self,
        local_root: Path,
        prefix: str,
        *,
        exclude: set[str] | None = None,
        delete_missing: bool = False,
    ) -> SyncStats:
        base = normalize_prefix(prefix)
        stats = SyncStats()
        if not local_root.is_dir():
            return stats
        remote = {info.key: info for info in self._list_remote(base)}
        excluded = set(exclude or ())
        uploaded_keys: set[str] = set()
        for path in sorted(local_root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(local_root)
            relative_text = relative.as_posix()
            if any(part.startswith(".") for part in relative.parts):
                continue
            if path.name.endswith(".tmp"):
                continue
            if relative_text in excluded or path.name in excluded:
                continue
            key = f"{base}/{relative_text}" if base else relative_text
            uploaded_keys.add(key)
            try:
                stat = path.stat()
            except OSError as exc:
                stats.errors.append(f"{relative_text}: {exc}")
                continue
            current = remote.get(key)
            if (
                current is not None
                and current.size == stat.st_size
                and current.mtime_ns == stat.st_mtime_ns
            ):
                stats.skipped += 1
                continue
            try:
                self.put_file(key, path)
            except StorageError as exc:
                stats.errors.append(f"{relative_text}: {exc}")
                continue
            stats.uploaded += 1
            stats.bytes_uploaded += stat.st_size
        if delete_missing:
            for key in remote:
                if key in uploaded_keys:
                    continue
                try:
                    self.delete(key)
                except StorageError as exc:
                    stats.errors.append(f"delete {key}: {exc}")
                    continue
                stats.deleted += 1
        return stats

    def sync_down(
        self,
        prefix: str,
        local_root: Path,
        *,
        prune: bool = False,
    ) -> SyncStats:
        base = normalize_prefix(prefix)
        stats = SyncStats()
        remote: dict[str, ObjectInfo] = {}
        for info in self._list_remote(base):
            remote[info.key] = info
            relative = relative_to_prefix(info.key, base)
            target = local_root / relative
            if self._local_matches(target, info):
                stats.skipped += 1
                continue
            try:
                self.download_file(info.key, target)
            except StorageError as exc:
                stats.errors.append(f"{relative}: {exc}")
                continue
            stats.downloaded += 1
            stats.bytes_downloaded += info.size
        if prune and local_root.is_dir():
            for path in sorted(local_root.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(local_root).as_posix()
                key = f"{base}/{relative}" if base else relative
                if key in remote:
                    continue
                try:
                    path.unlink()
                except OSError as exc:
                    stats.errors.append(f"{relative}: {exc}")
                    continue
                stats.deleted += 1
        return stats


__all__ = ["MinioStorage"]
