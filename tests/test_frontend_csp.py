from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_RE = re.compile(r"<script(?:\s[^>]*)?>(.*?)</script>", re.S)
_CSP_RE = re.compile(r"script-src([^;\"]*)")


def _configured_script_src() -> str:
    config = (ROOT / "deploy/nginx/conf.d/default.conf").read_text(
        encoding="utf-8"
    )
    match = _CSP_RE.search(config)
    assert match is not None, "CSP script-src directive is missing"
    return match.group(1)


def test_theme_bootstrap_is_external_and_csp_allows_self() -> None:
    source = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    script_src = _configured_script_src()

    assert (ROOT / "frontend/public/theme-init.js").is_file()
    assert 'src="./theme-init.js"' in source
    assert all("much-agents:theme:v2" not in body for body in _SCRIPT_RE.findall(source))
    assert script_src.strip() == "'self'"
    assert "sha256-" not in script_src

    dist = ROOT / "frontend/dist/index.html"
    if dist.is_file():
        built = dist.read_text(encoding="utf-8")
        assert 'src="./theme-init.js"' in built
        assert (ROOT / "frontend/dist/theme-init.js").is_file()
