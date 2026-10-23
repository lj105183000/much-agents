"""HTTP client for the MinerU v4 precision parsing API."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from ...config import Settings
from ...errors import MineruError, MineruErrorKind

logger = logging.getLogger(__name__)

TERMINAL_STATES = {"done", "failed"}


@dataclass(slots=True)
class UploadSpec:
    name: str
    data_id: str
    path: Path


@dataclass(slots=True)
class BatchInfo:
    batch_id: str
    file_urls: list[str]


@dataclass(slots=True)
class BatchFileResult:
    file_name: str
    data_id: str | None
    state: str
    full_zip_url: str | None
    err_msg: str | None = None


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _message(payload: object) -> str:
    if isinstance(payload, dict):
        for key in ("msg", "message", "error"):
            value = payload.get(key)
            if value:
                return str(value)
    return ""


def _classify(status_code: int, payload: object) -> MineruError:
    data = payload if isinstance(payload, dict) else {}
    code = str(data.get("code", ""))
    message = _message(payload)
    lowered = message.lower()
    if status_code in (401, 403) or code in {"A0202", "A0211"} or "token" in lowered:
        return MineruError(MineruErrorKind.AUTH, f"MinerU authentication failed: {message or code}")
    if code == "-60018" or "quota" in lowered or "daily" in lowered:
        return MineruError(MineruErrorKind.QUOTA, f"MinerU quota exhausted: {message or code}")
    if code == "-60006" or ("page" in lowered and "limit" in lowered):
        return MineruError(MineruErrorKind.PAGE_LIMIT, f"MinerU page limit exceeded: {message or code}")
    if status_code == 429 or status_code >= 500:
        return MineruError(
            MineruErrorKind.SERVER,
            f"MinerU server error ({status_code}): {message or code}",
            retryable=True,
        )
    if status_code >= 400:
        return MineruError(MineruErrorKind.UNKNOWN, f"MinerU request failed ({status_code}): {message or code}")
    if data and code not in {"", "0"}:
        return MineruError(MineruErrorKind.UNKNOWN, f"MinerU error {code}: {message}")
    return MineruError(MineruErrorKind.UNKNOWN, "MinerU returned an unexpected response")


class MineruClient:
    def __init__(self, settings: Settings, http_client: httpx.Client | None = None) -> None:
        self.settings = settings
        if not settings.mineru_api_key:
            raise MineruError(MineruErrorKind.AUTH, "MINERU_API_KEY is not configured")
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=settings.mineru_http_timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "MineruClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        return _auth_headers(self.settings.mineru_api_key or "")

    def create_batch(self, files: list[UploadSpec]) -> BatchInfo:
        if not files:
            raise MineruError(MineruErrorKind.UNKNOWN, "no files to upload")
        body = {
            "files": [{"name": item.name, "data_id": item.data_id} for item in files],
            "model_version": self.settings.mineru_model_version,
            "language": "ch",
            "enable_table": True,
            "enable_formula": True,
        }
        url = f"{self.settings.mineru_base_url}/file-urls/batch"
        try:
            response = self._client.post(url, headers=self._headers(), json=body)
        except httpx.TimeoutException as exc:
            raise MineruError(MineruErrorKind.TIMEOUT, f"MinerU batch request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise MineruError(MineruErrorKind.NETWORK, f"MinerU batch request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code != 200 or (isinstance(payload, dict) and str(payload.get("code")) not in {"0", "None"}):
            raise _classify(response.status_code, payload)

        data = (payload or {}).get("data") or {}
        batch_id = data.get("batch_id")
        raw_urls = data.get("file_urls") or []
        urls: list[str] = []
        for item in raw_urls:
            if isinstance(item, str):
                urls.append(item)
            elif isinstance(item, dict):
                value = item.get("url") or item.get("file_url")
                if value:
                    urls.append(str(value))
        if not batch_id or len(urls) < len(files):
            raise MineruError(MineruErrorKind.OUTPUT, "MinerU did not return upload URLs")
        return BatchInfo(batch_id=str(batch_id), file_urls=urls[: len(files)])

    def upload_file(self, url: str, path: Path) -> None:
        try:
            response = self._client.put(url, content=Path(path).read_bytes())
        except httpx.TimeoutException as exc:
            raise MineruError(MineruErrorKind.TIMEOUT, f"MinerU upload timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise MineruError(MineruErrorKind.NETWORK, f"MinerU upload failed: {exc}") from exc
        if response.status_code >= 400:
            raise _classify(response.status_code, {})

    def poll_batch(
        self,
        batch_id: str,
        progress: Callable[[str, int, str], None] | None = None,
    ) -> list[BatchFileResult]:
        url = f"{self.settings.mineru_base_url}/extract-results/batch/{batch_id}"
        deadline = time.monotonic() + max(0.0, self.settings.mineru_job_timeout)
        attempts = max(1, self.settings.mineru_max_polls)
        last: list[BatchFileResult] = []
        for attempt in range(attempts):
            try:
                response = self._client.get(url, headers=self._headers())
            except httpx.TimeoutException as exc:
                raise MineruError(MineruErrorKind.TIMEOUT, f"MinerU polling timed out: {exc}") from exc
            except httpx.RequestError as exc:
                raise MineruError(MineruErrorKind.NETWORK, f"MinerU polling failed: {exc}") from exc

            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if response.status_code != 200:
                raise _classify(response.status_code, payload)

            data = (payload or {}).get("data") or {}
            raw_results = data.get("extract_result") or data.get("extract_results") or []
            if isinstance(raw_results, dict):
                raw_results = [raw_results]
            last = [
                BatchFileResult(
                    file_name=str(item.get("file_name") or ""),
                    data_id=item.get("data_id"),
                    state=str(item.get("state") or "pending").lower(),
                    full_zip_url=item.get("full_zip_url"),
                    err_msg=item.get("err_msg"),
                )
                for item in raw_results
                if isinstance(item, dict)
            ]
            if last:
                if progress:
                    done = sum(1 for item in last if item.state in TERMINAL_STATES)
                    progress("mineru", int(done * 100 / max(1, len(last))),
                             f"poll {attempt + 1}: {done}/{len(last)} finished")
                if all(item.state in TERMINAL_STATES for item in last):
                    return last

            if time.monotonic() + self.settings.mineru_poll_interval > deadline:
                break
            if self.settings.mineru_poll_interval > 0:
                time.sleep(self.settings.mineru_poll_interval)

        states = ", ".join(f"{item.file_name}:{item.state}" for item in last) or "no results"
        raise MineruError(
            MineruErrorKind.TIMEOUT,
            f"MinerU job did not finish in {self.settings.mineru_job_timeout:.0f}s ({states})",
        )

    def download_zip(self, url: str, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / "mineru_result.zip"
        try:
            response = self._client.get(url, follow_redirects=True)
        except httpx.TimeoutException as exc:
            raise MineruError(MineruErrorKind.TIMEOUT, f"MinerU download timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise MineruError(MineruErrorKind.NETWORK, f"MinerU download failed: {exc}") from exc
        if response.status_code != 200:
            raise MineruError(
                MineruErrorKind.OUTPUT,
                f"MinerU result download failed ({response.status_code})",
            )
        target.write_bytes(response.content)
        return target
