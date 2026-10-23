"""Background job execution abstractions."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Protocol


class JobRunner(Protocol):
    def submit(self, job_id: str, task: Callable[[], None]) -> None: ...

    def shutdown(self, *, wait: bool = False) -> None: ...


class InlineRunner:
    """Run jobs immediately; useful for deterministic tests."""

    def submit(self, job_id: str, task: Callable[[], None]) -> None:
        task()

    def shutdown(self, *, wait: bool = False) -> None:
        return None


class ThreadJobRunner:
    """Bounded thread pool for blocking parse jobs."""

    def __init__(self, max_workers: int = 1) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="parse-job",
        )
        self._lock = threading.Lock()
        self._closed = False

    def submit(self, job_id: str, task: Callable[[], None]) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("job runner is shut down")
            self._executor.submit(task)

    def shutdown(self, *, wait: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=True)
