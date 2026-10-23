"""Typed errors and CLI exit codes."""

from __future__ import annotations

from enum import Enum

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_INPUT = 2
EXIT_PARSE = 3
EXIT_EXTRACT = 4
EXIT_INGEST = 5
EXIT_SKIPPED = 6


class ParsingError(Exception):
    """Base class for expected pipeline failures."""


class InputError(ParsingError):
    """Bad CLI input or unreadable source file."""


class ConfigError(ParsingError):
    """Invalid or missing configuration."""


class ParseError(ParsingError):
    """Both parsing engines failed, or a forced engine failed."""


class ExtractionError(ParsingError):
    """Requested extraction produced no usable output."""


class IngestError(ParsingError):
    """Chroma or embedding failure."""

    def __init__(self, message: str, *, skipped: bool = False) -> None:
        super().__init__(message)
        self.skipped = skipped


class MineruErrorKind(str, Enum):
    AUTH = "auth"
    QUOTA = "quota"
    PAGE_LIMIT = "page_limit"
    NETWORK = "network"
    SERVER = "server"
    TIMEOUT = "timeout"
    OUTPUT = "output"
    UNKNOWN = "unknown"


class MineruError(ParsingError):
    def __init__(self, kind: MineruErrorKind, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable

    def __str__(self) -> str:
        return f"{self.kind.value}: {super().__str__()}"
