"""Safe extraction of MinerU result archives."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

from ...errors import MineruError, MineruErrorKind

MAX_FILES = 2000
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def safe_extract(
    zip_path: Path,
    dest_dir: Path,
    *,
    max_files: int = MAX_FILES,
    max_bytes: int = MAX_UNCOMPRESSED_BYTES,
) -> list[Path]:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise MineruError(MineruErrorKind.OUTPUT, f"invalid MinerU zip: {exc}") from exc

    with archive:
        infos = archive.infolist()
        count = 0
        total = 0
        for info in infos:
            name = info.filename.replace("\\", "/")
            parts = PurePosixPath(name).parts
            if (
                not name
                or name.startswith("/")
                or _DRIVE_RE.match(name)
                or ".." in parts
            ):
                raise MineruError(MineruErrorKind.OUTPUT, f"unsafe zip entry: {name!r}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise MineruError(MineruErrorKind.OUTPUT, f"symlink in zip is not allowed: {name!r}")
            if info.is_dir():
                continue
            count += 1
            total += max(0, info.file_size)
            if count > max_files:
                raise MineruError(MineruErrorKind.OUTPUT, "MinerU zip contains too many files")
            if total > max_bytes:
                raise MineruError(MineruErrorKind.OUTPUT, "MinerU zip is too large when extracted")

        for info in infos:
            if info.is_dir():
                continue
            target = dest_dir / PurePosixPath(info.filename.replace("\\", "/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as sink:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    sink.write(chunk)
            extracted.append(target)
    return extracted


def locate_artifacts(root: Path) -> tuple[Path | None, Path | None, Path | None, Path | None]:
    """Find full.md, content_list.json, layout.json and the images directory."""
    full_md = next(
        (path for path in root.rglob("*") if path.is_file() and path.name.lower() == "full.md"),
        None,
    )
    if full_md is None:
        candidates = [path for path in root.rglob("*.md") if path.is_file()]
        full_md = candidates[0] if candidates else None

    content_list = next(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and "content_list" in path.name.lower() and path.suffix.lower() == ".json"
        ),
        None,
    )
    layout = next(
        (path for path in root.rglob("layout.json") if path.is_file()),
        None,
    )
    images = None
    if full_md is not None and (full_md.parent / "images").is_dir():
        images = full_md.parent / "images"
    elif (root / "images").is_dir():
        images = root / "images"
    return full_md, content_list, layout, images
