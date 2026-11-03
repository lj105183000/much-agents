from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_RE = re.compile(r"<script(?:\s[^>]*)?>(.*?)</script>", re.S)
_CSP_RE = re.compile(r"script-src([^;\"]*)")


def _inline_theme_digest(html: str) -> str:
    for script in _SCRIPT_RE.findall(html):
        if "much-agents:theme:v2" in script:
            digest = hashlib.sha256(script.encode("utf-8")).digest()
            return "sha256-" + base64.b64encode(digest).decode("ascii")
    raise AssertionError("inline theme bootstrap script was not found")


def _configured_script_src() -> str:
    config = (ROOT / "deploy/nginx/conf.d/default.conf").read_text(
        encoding="utf-8"
    )
    match = _CSP_RE.search(config)
    assert match is not None, "CSP script-src directive is missing"
    return match.group(1)


def test_inline_theme_script_hash_matches_nginx_csp() -> None:
    source = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    script_src = _configured_script_src()

    assert _inline_theme_digest(source) in script_src
    assert "unsafe-inline" not in script_src

    dist = ROOT / "frontend/dist/index.html"
    if dist.is_file():
        built = dist.read_text(encoding="utf-8")
        assert _inline_theme_digest(built) in script_src
