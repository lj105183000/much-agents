"""DeepSeek chat extraction with bounded retries and model fallback."""

from __future__ import annotations

import logging
import time

from ..config import Settings
from ..errors import ExtractionError
from .json_parse import loads_tolerant
from .pruning import Payload
from .prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)


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
    return "response_format" in str(exc).lower() or "json mode" in str(exc).lower()


class DeepSeekExtractor:
    def __init__(self, settings: Settings, client=None) -> None:
        self.settings = settings
        if client is None:
            if not settings.deepseek_api_key:
                raise ExtractionError("DEEPSEEK_API_KEY is not configured")
            from openai import OpenAI

            client = OpenAI(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                timeout=settings.deepseek_timeout,
                max_retries=0,
            )
        self.client = client

    def extract(self, payload: Payload, schema_hint: str) -> dict:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(payload, schema_hint)},
        ]
        models = [self.settings.deepseek_model]
        if self.settings.deepseek_model != "deepseek-chat":
            models.append("deepseek-chat")

        last_error: Exception | None = None
        for model in models:
            model_failed = False
            for json_mode in (True, False):
                attempts = max(1, self.settings.deepseek_max_retries)
                for attempt in range(attempts):
                    try:
                        kwargs = {
                            "model": model,
                            "messages": messages,
                            "temperature": 0,
                            "max_tokens": self.settings.extraction_max_tokens,
                        }
                        if json_mode:
                            kwargs["response_format"] = {"type": "json_object"}
                        response = self.client.chat.completions.create(**kwargs)
                        content = response.choices[0].message.content or ""
                        return loads_tolerant(content)
                    except TypeError as exc:
                        # Fake or older SDK clients may not accept response_format.
                        last_error = exc
                        break
                    except Exception as exc:  # noqa: BLE001 - SDK exception hierarchy varies
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
                if model_failed:
                    break
        raise ExtractionError(f"DeepSeek extraction failed: {last_error}")
