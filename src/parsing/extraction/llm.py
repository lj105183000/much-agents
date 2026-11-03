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


def _usage_payload(
    response,
    *,
    model_requested: str,
    model_actual: str | None,
    latency_ms: int,
    retry_count: int,
    fallback: bool,
    status: str = "ok",
    error_type: str | None = None,
) -> dict[str, object]:
    usage = getattr(response, "usage", None) if response is not None else None
    choices = getattr(response, "choices", None) if response is not None else None
    finish_reason = None
    if choices:
        finish_reason = getattr(choices[0], "finish_reason", None)
    return {
        "model_requested": model_requested,
        "model_actual": (
            getattr(response, "model", None) or model_actual or model_requested
        ),
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
        "finish_reason": finish_reason,
        "latency_ms": max(0, int(latency_ms)),
        "retry_count": max(0, int(retry_count)),
        "fallback": bool(fallback),
        "status": status,
        "error_type": error_type,
    }


class DeepSeekExtractor:
    def __init__(self, settings: Settings, client=None) -> None:
        self.settings = settings
        self.last_usage: dict[str, object] | None = None
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
        if (
            self.settings.deepseek_allow_fallback
            and self.settings.deepseek_model != "deepseek-chat"
        ):
            models.append("deepseek-chat")

        last_error: Exception | None = None
        for model in models:
            model_failed = False
            for json_mode in (True, False):
                attempts = max(1, self.settings.deepseek_max_retries)
                for attempt in range(attempts):
                    started = time.perf_counter()
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
                        self.last_usage = _usage_payload(
                            response,
                            model_requested=model,
                            model_actual=model,
                            latency_ms=(time.perf_counter() - started) * 1000,
                            retry_count=attempt,
                            fallback=model != self.settings.deepseek_model,
                        )
                        content = response.choices[0].message.content or ""
                        return loads_tolerant(content)
                    except TypeError as exc:
                        # Fake or older SDK clients may not accept response_format.
                        self.last_usage = _usage_payload(
                            None,
                            model_requested=model,
                            model_actual=model,
                            latency_ms=(time.perf_counter() - started) * 1000,
                            retry_count=attempt,
                            fallback=model != self.settings.deepseek_model,
                            status="error",
                            error_type=type(exc).__name__,
                        )
                        last_error = exc
                        break
                    except Exception as exc:  # noqa: BLE001 - SDK exception hierarchy varies
                        self.last_usage = _usage_payload(
                            None,
                            model_requested=model,
                            model_actual=model,
                            latency_ms=(time.perf_counter() - started) * 1000,
                            retry_count=attempt,
                            fallback=model != self.settings.deepseek_model,
                            status="error",
                            error_type=type(exc).__name__,
                        )
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
