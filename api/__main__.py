"""Run the API with ``uv run python -m api``."""

from __future__ import annotations

import uvicorn

from .main import app


def main() -> int:
    settings = app.state.api_settings
    uvicorn.run(app, host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
