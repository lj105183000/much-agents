"""Resilient DeepSeek chat client used by bid-writing agents."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Protocol

from src.parsing.extraction.json_parse import loads_tolerant

from .errors import BidLLMError

logger = logging.getLogger(__name__)


class ChatClient(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str: ...


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _retryable(exc: Exception) -> bool:
    status = _status_code(exc)
    if status in (429, 500, 502, 503, 504):
        return True
    text = str(exc).lower()
    return any(token in text for token in ("timeout", "timed out", "connection reset", "temporarily"))


def _model_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "model" in text and any(
        token in text for token in ("not exist", "not found", "invalid", "unsupported", "unknown")
    )


def _json_mode_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "response_format" in text or "json mode" in text


def _base_settings(settings: Any) -> Any:
    return getattr(settings, "base", settings)


def _escape_control_chars(text: str) -> str:
    """Escape raw newlines/tabs that some models emit inside JSON strings."""
    output: list[str] = []
    in_string = False
    escaped = False
    for char in text or "":
        if in_string:
            if escaped:
                output.append(char)
                escaped = False
            elif char == "\\":
                output.append(char)
                escaped = True
            elif char == '"':
                output.append(char)
                in_string = False
            elif char == "\n":
                output.append("\\n")
            elif char == "\r":
                output.append("\\r")
            elif char == "\t":
                output.append("\\t")
            else:
                output.append(char)
        else:
            output.append(char)
            if char == '"':
                in_string = True
    return "".join(output)


class DeepSeekChatClient:
    def __init__(self, settings: Any, *, client: Any | None = None, concurrency: int = 2) -> None:
        self.settings = _base_settings(settings)
        if client is None:
            if not self.settings.deepseek_api_key:
                raise BidLLMError("DEEPSEEK_API_KEY is not configured")
            from openai import OpenAI

            client = OpenAI(
                api_key=self.settings.deepseek_api_key,
                base_url=self.settings.deepseek_base_url,
                timeout=self.settings.deepseek_timeout,
                max_retries=0,
            )
        self.client = client
        self._semaphore = threading.Semaphore(max(1, int(concurrency)))
        self._call_lock = threading.Lock()
        self.calls = 0

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        models = [self.settings.deepseek_model]
        if self.settings.deepseek_model != "deepseek-chat":
            models.append("deepseek-chat")
        attempts = max(1, int(getattr(self.settings, "deepseek_max_retries", 3)))
        last_error: Exception | None = None

        for model in models:
            model_failed = False
            for use_json in ((True, False) if json_mode else (False,)):
                for attempt in range(attempts):
                    try:
                        kwargs: dict[str, Any] = {
                            "model": model,
                            "messages": messages,
                            "temperature": temperature,
                            "max_tokens": max_tokens or 4096,
                        }
                        if use_json:
                            kwargs["response_format"] = {"type": "json_object"}
                        with self._call_lock:
                            self.calls += 1
                        with self._semaphore:
                            response = self.client.chat.completions.create(**kwargs)
                        return response.choices[0].message.content or ""
                    except TypeError as exc:
                        last_error = exc
                        break
                    except Exception as exc:  # noqa: BLE001 - SDK exception types vary
                        last_error = exc
                        if _model_error(exc):
                            model_failed = True
                            break
                        if _json_mode_error(exc):
                            break
                        if _retryable(exc) and attempt + 1 < attempts:
                            time.sleep(min(2 ** attempt, 4))
                            continue
                        break
        raise BidLLMError(f"DeepSeek completion failed: {last_error}")

    def complete_json(self, *, system: str, user: str, **kwargs: Any) -> Any:
        text = self.complete(system=system, user=user, json_mode=True, **kwargs)
        try:
            return loads_tolerant(text)
        except Exception:
            try:
                return loads_tolerant(_escape_control_chars(text))
            except Exception as exc:  # noqa: BLE001
                raise BidLLMError(f"LLM did not return valid JSON: {exc}") from exc


class MeteredChatClient:
    def __init__(self, inner: ChatClient) -> None:
        self.inner = inner
        self._lock = threading.Lock()
        self.calls = 0

    def complete(self, **kwargs: Any) -> str:
        with self._lock:
            self.calls += 1
        return self.inner.complete(**kwargs)
