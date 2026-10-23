"""Uvicorn entry point: ``uvicorn api.main:app``."""

from src.parsing.logging_utils import configure_stdio

from .app import create_app

configure_stdio()

app = create_app()
