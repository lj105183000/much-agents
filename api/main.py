"""Uvicorn entry point: ``uvicorn api.main:app``."""

from src.observability.logging import configure_logging
from src.parsing.logging_utils import configure_stdio

from .app import create_app

configure_stdio()
configure_logging()

app = create_app()
