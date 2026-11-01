"""Fail when production Compose uses floating ``:latest`` images."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

IMAGE_RE = re.compile(r"^\s*image:\s*[\"']?([^\"'\s#]+)")


def find_unpinned(path: Path) -> list[str]:
    unpinned: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = IMAGE_RE.match(line)
        if not match:
            continue
        image = match.group(1)
        if image.endswith(":latest"):
            unpinned.append(image)
    return unpinned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "compose_file", nargs="?", default="docker-compose.prod.yml"
    )
    args = parser.parse_args(argv)
    path = Path(args.compose_file)
    if not path.is_file():
        print(f"missing compose file: {path}", file=sys.stderr)
        return 2
    unpinned = find_unpinned(path)
    if unpinned:
        print("floating :latest images are not allowed in production:", file=sys.stderr)
        for image in unpinned:
            print(f"  - {image}", file=sys.stderr)
        return 1
    print(f"all images are pinned: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
