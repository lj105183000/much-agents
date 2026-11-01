"""Small thread-safe Prometheus text-format registry.

The project deliberately avoids a new runtime dependency for metrics. The
registry supports counters, gauges and fixed-bucket histograms, which is enough
for the core run/LLM/HTTP signals described in docs/production-readiness.md.
"""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from typing import Iterable, Mapping

DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
)


def _escape(value: object) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def _labels_text(labels: Mapping[str, str]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{key}="{_escape(value)}"' for key, value in sorted(labels.items()))
    return "{" + body + "}"


def _number(value: float) -> str:
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if value == int(value):
        return str(int(value))
    return repr(float(value))


class Counter:
    def __init__(self, name: str, help_text: str, labelnames: Iterable[str] = ()) -> None:
        self.name = name
        self.help_text = help_text
        self.labelnames = tuple(labelnames)
        self._values: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = tuple(sorted((name, str(value)) for name, value in labels.items()))
        with self._lock:
            self._values[key] += float(amount)

    def value(self, **labels: str) -> float:
        key = tuple(sorted((name, str(value)) for name, value in labels.items()))
        with self._lock:
            return float(self._values.get(key, 0.0))

    def render(self) -> list[str]:
        with self._lock:
            items = list(self._values.items())
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} counter"]
        for key, value in items:
            lines.append(f"{self.name}{_labels_text(dict(key))} {_number(value)}")
        return lines


class Gauge:
    def __init__(self, name: str, help_text: str, labelnames: Iterable[str] = ()) -> None:
        self.name = name
        self.help_text = help_text
        self.labelnames = tuple(labelnames)
        self._values: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)
        self._lock = threading.Lock()

    def set(self, value: float, **labels: str) -> None:
        key = tuple(sorted((name, str(label)) for name, label in labels.items()))
        with self._lock:
            self._values[key] = float(value)

    def value(self, **labels: str) -> float:
        key = tuple(sorted((name, str(label)) for name, label in labels.items()))
        with self._lock:
            return float(self._values.get(key, 0.0))

    def render(self) -> list[str]:
        with self._lock:
            items = list(self._values.items())
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} gauge"]
        for key, value in items:
            lines.append(f"{self.name}{_labels_text(dict(key))} {_number(value)}")
        return lines


class Histogram:
    def __init__(
        self,
        name: str,
        help_text: str,
        labelnames: Iterable[str] = (),
        buckets: Iterable[float] = DEFAULT_BUCKETS,
    ) -> None:
        self.name = name
        self.help_text = help_text
        self.labelnames = tuple(labelnames)
        self.buckets = tuple(sorted(float(item) for item in buckets))
        self._counts: dict[tuple[tuple[str, str], ...], list[int]] = {}
        self._sums: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)
        self._totals: dict[tuple[tuple[str, str], ...], int] = defaultdict(int)
        self._lock = threading.Lock()

    def observe(self, value: float, **labels: str) -> None:
        key = tuple(sorted((name, str(label)) for name, label in labels.items()))
        amount = float(value)
        with self._lock:
            counts = self._counts.get(key)
            if counts is None:
                counts = [0] * (len(self.buckets) + 1)
                self._counts[key] = counts
            for index, bucket in enumerate(self.buckets):
                if amount <= bucket:
                    counts[index] += 1
            counts[-1] += 1
            self._sums[key] += amount
            self._totals[key] += 1

    def render(self) -> list[str]:
        with self._lock:
            keys = set(self._counts) | set(self._sums) | set(self._totals)
            snapshot = {
                key: (
                    list(self._counts.get(key, [0] * (len(self.buckets) + 1))),
                    self._sums.get(key, 0.0),
                    self._totals.get(key, 0),
                )
                for key in keys
            }
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} histogram"]
        for key, (counts, total_sum, total_count) in snapshot.items():
            base = dict(key)
            for index, bucket in enumerate(self.buckets):
                labels = {**base, "le": _number(bucket)}
                lines.append(
                    f"{self.name}_bucket{_labels_text(labels)} {counts[index]}"
                )
            labels = {**base, "le": "+Inf"}
            lines.append(f"{self.name}_bucket{_labels_text(labels)} {counts[-1]}")
            lines.append(f"{self.name}_sum{_labels_text(base)} {_number(total_sum)}")
            lines.append(f"{self.name}_count{_labels_text(base)} {total_count}")
        return lines


class MetricsRegistry:
    def __init__(self) -> None:
        self.counters: dict[str, Counter] = {}
        self.gauges: dict[str, Gauge] = {}
        self.histograms: dict[str, Histogram] = {}

    def counter(
        self, name: str, help_text: str, labelnames: Iterable[str] = ()
    ) -> Counter:
        metric = self.counters.get(name)
        if metric is None:
            metric = Counter(name, help_text, labelnames)
            self.counters[name] = metric
        return metric

    def gauge(
        self, name: str, help_text: str, labelnames: Iterable[str] = ()
    ) -> Gauge:
        metric = self.gauges.get(name)
        if metric is None:
            metric = Gauge(name, help_text, labelnames)
            self.gauges[name] = metric
        return metric

    def histogram(
        self,
        name: str,
        help_text: str,
        labelnames: Iterable[str] = (),
        buckets: Iterable[float] = DEFAULT_BUCKETS,
    ) -> Histogram:
        metric = self.histograms.get(name)
        if metric is None:
            metric = Histogram(name, help_text, labelnames, buckets)
            self.histograms[name] = metric
        return metric

    def render(self) -> str:
        lines: list[str] = []
        for metric in self.counters.values():
            lines.extend(metric.render())
        for metric in self.gauges.values():
            lines.extend(metric.render())
        for metric in self.histograms.values():
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"


REGISTRY = MetricsRegistry()

HTTP_REQUESTS = REGISTRY.counter(
    "much_agents_http_requests_total",
    "HTTP requests by method, route and status.",
    ("method", "route", "status"),
)
HTTP_DURATION = REGISTRY.histogram(
    "much_agents_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
)
LLM_CALLS = REGISTRY.counter(
    "much_agents_llm_calls_total",
    "LLM calls by operation, model, status and fallback.",
    ("operation", "model", "status", "fallback"),
)
LLM_TOKENS = REGISTRY.counter(
    "much_agents_llm_tokens_total",
    "LLM tokens by model and kind.",
    ("model", "kind"),
)
LLM_COST = REGISTRY.counter(
    "much_agents_llm_cost_cny_total",
    "Estimated LLM cost in CNY by model.",
    ("model",),
)
RUNS = REGISTRY.counter(
    "much_agents_runs_total",
    "Bid runs by terminal status.",
    ("status",),
)
RUN_DURATION = REGISTRY.histogram(
    "much_agents_run_duration_seconds",
    "Bid run duration in seconds.",
    ("status",),
)
SECTION_FAILURES = REGISTRY.counter(
    "much_agents_section_failures_total",
    "Bid section failures by stage.",
    ("stage",),
)
GATE_STATUS = REGISTRY.counter(
    "much_agents_gate_status_total",
    "Assembled bid gate status.",
    ("status",),
)
READY_CHECKS = REGISTRY.gauge(
    "much_agents_ready_checks",
    "Readiness component status (1=ready, 0=failed).",
    ("component",),
)
QUEUE_DEPTH = REGISTRY.gauge(
    "much_agents_queue_depth",
    "Observed queue depth.",
    ("queue",),
)
WORKER_HEARTBEAT_AGE = REGISTRY.gauge(
    "much_agents_worker_heartbeat_age_seconds",
    "Age of the last worker heartbeat.",
    ("queue", "worker"),
)
BACKUP_LAST_SUCCESS = REGISTRY.gauge(
    "much_agents_backup_last_success_timestamp_seconds",
    "Unix timestamp of the last successful backup.",
    ("target",),
)


def record_llm_usage(
    *,
    operation: str,
    model: str,
    status: str,
    fallback: bool,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    cost_cny: float | None,
) -> None:
    LLM_CALLS.inc(
        operation=operation,
        model=model or "unknown",
        status=status,
        fallback="true" if fallback else "false",
    )
    if prompt_tokens is not None:
        LLM_TOKENS.inc(prompt_tokens, model=model or "unknown", kind="prompt")
    if completion_tokens is not None:
        LLM_TOKENS.inc(completion_tokens, model=model or "unknown", kind="completion")
    if cost_cny is not None:
        LLM_COST.inc(cost_cny, model=model or "unknown")
