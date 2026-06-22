import time
from collections import deque
from threading import Lock
from datetime import datetime, timezone

_APP_START_TIME = time.time()
_APP_START_DATETIME = datetime.now(timezone.utc)


class RequestMetrics:
    """Thread-safe sliding-window tracker for API request latency and error counts."""

    def __init__(self, window_size: int = 1000):
        self._lock = Lock()
        self._latencies: deque = deque(maxlen=window_size)
        self._total_requests: int = 0
        self._total_errors: int = 0

    def record(self, latency_ms: float, is_error: bool) -> None:
        with self._lock:
            self._latencies.append(latency_ms)
            self._total_requests += 1
            if is_error:
                self._total_errors += 1

    @property
    def avg_latency_ms(self) -> float:
        with self._lock:
            if not self._latencies:
                return 0.0
            return round(sum(self._latencies) / len(self._latencies), 2)

    @property
    def p95_latency_ms(self) -> float:
        with self._lock:
            if not self._latencies:
                return 0.0
            sorted_lat = sorted(self._latencies)
            idx = max(0, int(len(sorted_lat) * 0.95) - 1)
            return round(sorted_lat[idx], 2)

    @property
    def total_requests(self) -> int:
        return self._total_requests

    @property
    def error_rate_pct(self) -> float:
        if self._total_requests == 0:
            return 0.0
        return round(self._total_errors / self._total_requests * 100, 2)


request_metrics = RequestMetrics()


def get_uptime_seconds() -> float:
    return round(time.time() - _APP_START_TIME, 1)


def get_start_datetime() -> datetime:
    return _APP_START_DATETIME
