"""Internal observability — bounded histograms, circular-buffer time-series, and query interface.

Provides lightweight, local-only metrics collection for the NoUsClaWW agent
stack. Tracks arbitrary named metrics as bounded histograms and periodic
time-series snapshots. All data is structured (JSON-serializable) and
queryable by the agent itself — the foundation for self-monitoring.

Architecture:
  1. BoundedHistogram — fixed-memory histogram with O(1) insertion
  2. CircularBuffer — time-series storage that evicts oldest entries
  3. MetricsTracker — general-purpose record/query API

Invariants:
  1. All monitoring is local — no data leaves the machine
  2. Histograms are bounded — fixed bucket count, O(1) insertion
  3. Time-series is bounded — circular buffer, oldest evicted
  4. Monitoring data is JSON-serializable and queryable
  5. Duck-typed acceptance — record() accepts any value, no type coupling

SYNTH:
    purpose: General-purpose metrics tracking with bounded histograms and circular-buffer time-series for local self-monitoring
    axioms: [local_first, evidence_over_intuition, open_process, epistemic_boundary]
    objective: Any module can record a metric in O(1) and query distributions or trends without unbounded memory growth; all data stays local and is JSON-serializable
    anti_patterns:
        - Importing domain-specific result types (duck-typed acceptance only)
        - Unbounded lists for latency or value tracking
        - Sending monitoring data to any remote service
        - Blocking the caller during collection (must be O(1) for record)
        - Using numpy or other heavy dependencies (pure Python only)
"""
#C Adapted from NoUs-fordge Nous-hub mvp_local_core core/monitoring.py

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


# ── Constants ───────────────────────────────────────────────────────────────

DEFAULT_HISTOGRAM_BUCKETS = 20
DEFAULT_TIME_SERIES_MAX = 1000
DEFAULT_LATENCY_MAX_MS = 100_000.0  # values above this go in the last bucket


# ── Bounded Histogram ───────────────────────────────────────────────────────

@dataclass
class BoundedHistogram:
    """Fixed-memory histogram for tracking value distributions.

    Bounded to a fixed number of buckets. O(1) insertion, O(n_buckets)
    percentile calculation. No unbounded growth — suitable for long-running
    processes.

    Attributes:
        min_val: Minimum value represented by the histogram range
        max_val: Maximum value represented by the histogram range
        num_buckets: Number of buckets (fixed at creation)
    """

    min_val: float = 0.0
    max_val: float = 1.0
    num_buckets: int = DEFAULT_HISTOGRAM_BUCKETS
    _buckets: list[int] = field(default_factory=list)
    _count: int = 0
    _sum: float = 0.0
    _min_seen: float = field(default_factory=lambda: float("inf"))
    _max_seen: float = field(default_factory=lambda: float("-inf"))

    def __post_init__(self) -> None:
        if not self._buckets:
            self._buckets = [0] * self.num_buckets
        if self.max_val <= self.min_val:
            self.max_val = self.min_val + 1.0

    def add(self, value: float) -> None:
        """Add a value to the histogram in O(1).

        Args:
            value: The numeric value to record
        """
        if self._count >= (1 << 31):
            # Prevent overflow on very long runs — halve counters
            self._sum = self._sum / 2.0
            self._count = self._count // 2
            self._buckets = [c // 2 for c in self._buckets]

        self._count += 1
        self._sum += float(value)
        v = float(value)
        if v < self._min_seen:
            self._min_seen = v
        if v > self._max_seen:
            self._max_seen = v

        if v <= self.min_val:
            self._buckets[0] += 1
            return
        if v >= self.max_val:
            self._buckets[-1] += 1
            return

        bucket = int(
            (v - self.min_val) / (self.max_val - self.min_val) * self.num_buckets
        )
        bucket = min(bucket, self.num_buckets - 1)
        self._buckets[bucket] += 1

    def percentile(self, p: float) -> float:
        """Approximate the p-th percentile (0-100).

        Args:
            p: Percentile to compute (0-100)

        Returns:
            Approximate percentile value, or 0.0 if empty
        """
        if self._count == 0:
            return 0.0
        target = self._count * (p / 100.0)
        cumulative = 0
        span = self.max_val - self.min_val
        for i, count in enumerate(self._buckets):
            cumulative += count
            if cumulative >= target:
                bucket_start = self.min_val + i * span / self.num_buckets
                bucket_end = self.min_val + (i + 1) * span / self.num_buckets
                return (bucket_start + bucket_end) / 2.0
        return self.max_val

    def mean(self) -> float:
        """Return the arithmetic mean of all values added."""
        if self._count == 0:
            return 0.0
        return self._sum / self._count

    def count(self) -> int:
        """Return the total number of values added."""
        return self._count

    def min_seen(self) -> float:
        """Return the smallest value ever recorded."""
        if self._count == 0:
            return 0.0
        return self._min_seen

    def max_seen(self) -> float:
        """Return the largest value ever recorded."""
        if self._count == 0:
            return 0.0
        return self._max_seen

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for JSON output."""
        return {
            "count": self._count,
            "mean": round(self.mean(), 6),
            "p50": round(self.percentile(50), 6),
            "p95": round(self.percentile(95), 6),
            "p99": round(self.percentile(99), 6),
            "min": round(self.min_seen(), 6) if self._count > 0 else 0.0,
            "max": round(self.max_seen(), 6) if self._count > 0 else 0.0,
            "range_min": self.min_val,
            "range_max": self.max_val,
        }


# ── Time-Series Snapshot ────────────────────────────────────────────────────

@dataclass
class TimeSeriesSnapshot:
    """A single point in time for the metrics time-series.

    Attributes:
        timestamp: Unix timestamp of the snapshot
        values: Dict of metric name to value at this point in time
    """

    timestamp: float
    values: dict[str, Any]


# ── Metrics Tracker ─────────────────────────────────────────────────────────

class MetricsTracker:
    """General-purpose metrics tracker with bounded histograms and time-series.

    Provides a simple record/query API. Any module can record a named metric
    value; the tracker maintains a bounded histogram per metric and a
    circular-buffer time-series of periodic snapshots.

    Usage::

        tracker = MetricsTracker()
        tracker.record("retrieval_latency_ms", 5.2)
        tracker.record("retrieval_latency_ms", 8.1)
        hist = tracker.get_histogram("retrieval_latency_ms")
        print(hist["p99"])  # 99th percentile latency

        tracker.snapshot()  # capture current state
        ts = tracker.get_timeseries("retrieval_latency_ms", window=10)

    Invariants:
        1. record() is O(1) — never blocks the caller
        2. Histograms are bounded — fixed bucket count per metric
        3. Time-series is bounded — circular buffer, oldest evicted
        4. All data is JSON-serializable
        5. Duck-typed — accepts any numeric value, no domain coupling
    """

    def __init__(
        self,
        time_series_max: int = DEFAULT_TIME_SERIES_MAX,
        histogram_buckets: int = DEFAULT_HISTOGRAM_BUCKETS,
    ) -> None:
        """Initialize the metrics tracker.

        Args:
            time_series_max: Maximum number of time-series snapshots to retain
            histogram_buckets: Number of buckets per histogram
        """
        self._time_series_max = time_series_max
        self._histogram_buckets = histogram_buckets
        self._histograms: dict[str, BoundedHistogram] = {}
        self._time_series: deque[TimeSeriesSnapshot] = deque(maxlen=time_series_max)
        self._start_time: float = time.time()
        self._last_snapshot_time: float = 0.0

        # Counters for simple integer metrics
        self._counters: dict[str, int] = {}

        # Per-metric min/max range hints (auto-detected on first record if unset)
        self._metric_ranges: dict[str, tuple[float, float]] = {}

    # ── Recording ──────────────────────────────────────────────────────

    def record(self, metric: str, value: float | int) -> None:
        """Record a value for a named metric.

        Creates a bounded histogram for the metric on first use. The
        histogram range is auto-detected from the first few values if
        no explicit range was set via set_metric_range().

        Args:
            metric: Dot-separated metric name (e.g., "retrieval.latency_ms")
            value: Numeric value to record
        """
        v = float(value)

        if metric not in self._histograms:
            lo, hi = self._metric_ranges.get(metric, (0.0, DEFAULT_LATENCY_MAX_MS))
            self._histograms[metric] = BoundedHistogram(
                min_val=lo, max_val=hi, num_buckets=self._histogram_buckets
            )

        self._histograms[metric].add(v)

    def record_counter(self, metric: str, increment: int = 1) -> None:
        """Increment a monotonic counter.

        Counters are simple integers that only increase. They are tracked
        separately from histograms and included in snapshots.

        Args:
            metric: Counter name
            increment: Amount to add (default 1)
        """
        self._counters[metric] = self._counters.get(metric, 0) + increment

    def set_metric_range(self, metric: str, min_val: float, max_val: float) -> None:
        """Set the histogram range for a metric before first recording.

        If the metric already has a histogram, this re-creates it (clearing
        existing data). Call this before recording values for best results.

        Args:
            metric: Metric name
            min_val: Minimum value for the histogram range
            max_val: Maximum value for the histogram range
        """
        self._metric_ranges[metric] = (min_val, max_val)
        if metric in self._histograms:
            # Re-create histogram with new range
            self._histograms[metric] = BoundedHistogram(
                min_val=min_val, max_val=max_val, num_buckets=self._histogram_buckets
            )

    # ── Querying ───────────────────────────────────────────────────────

    def get_histogram(self, metric: str) -> dict[str, Any]:
        """Get histogram statistics for a metric.

        Args:
            metric: Metric name

        Returns:
            Dict with count, mean, p50, p95, p99, min, max.
            Returns an empty-stats dict if metric was never recorded.
        """
        if metric not in self._histograms:
            return {
                "count": 0,
                "mean": 0.0,
                "p50": 0.0,
                "p95": 0.0,
                "p99": 0.0,
                "min": 0.0,
                "max": 0.0,
            }
        return self._histograms[metric].to_dict()

    def get_timeseries(
        self, metric: str, window: int = 0
    ) -> list[dict[str, Any]]:
        """Get time-series values for a metric.

        Args:
            metric: Metric name to extract from snapshots
            window: Number of most recent snapshots (0 = all)

        Returns:
            List of {"timestamp": float, "value": float} dicts.
            Only snapshots that contain the metric are included.
        """
        snapshots = (
            list(self._time_series)[-window:]
            if window > 0 and window < len(self._time_series)
            else list(self._time_series)
        )
        result: list[dict[str, Any]] = []
        for snap in snapshots:
            val = self._extract_value(snap.values, metric)
            if val is not None:
                result.append({"timestamp": snap.timestamp, "value": val})
        return result

    def get_counter(self, metric: str) -> int:
        """Get the current value of a counter.

        Args:
            metric: Counter name

        Returns:
            Current counter value, or 0 if never incremented
        """
        return self._counters.get(metric, 0)

    def get_all_counters(self) -> dict[str, int]:
        """Return all counters as a dict."""
        return dict(self._counters)

    def get_all_histograms(self) -> dict[str, dict[str, Any]]:
        """Return histogram stats for all tracked metrics."""
        return {m: h.to_dict() for m, h in self._histograms.items()}

    # ── Snapshot ───────────────────────────────────────────────────────

    def snapshot(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Take a snapshot of current metrics and add to time-series.

        Captures all histogram summaries and counter values into a single
        dict, stores it in the circular buffer, and returns it.

        Args:
            extra: Additional data to include in the snapshot

        Returns:
            The snapshot dict
        """
        stats: dict[str, Any] = {
            "histograms": self.get_all_histograms(),
            "counters": self.get_all_counters(),
            "uptime_seconds": round(time.time() - self._start_time, 1),
        }
        if extra:
            stats["extra"] = extra

        snap = TimeSeriesSnapshot(timestamp=time.time(), values=stats)
        self._time_series.append(snap)
        self._last_snapshot_time = snap.timestamp
        return stats

    def get_time_series(self, last_n: int = 0) -> list[dict[str, Any]]:
        """Return raw time-series snapshots.

        Args:
            last_n: Number of most recent snapshots (0 = all)

        Returns:
            List of {"timestamp": float, "values": dict} snapshots
        """
        if last_n == 0 or last_n >= len(self._time_series):
            items = list(self._time_series)
        else:
            items = list(self._time_series)[-last_n:]
        return [{"timestamp": s.timestamp, "values": s.values} for s in items]

    # ── Query interface (self-monitoring) ──────────────────────────────

    def query(self, area: str) -> dict[str, Any]:
        """Query a specific monitoring area programmatically.

        This is the self-monitoring interface — the agent can read its
        own metrics to make decisions.

        Args:
            area: One of "histograms", "counters", "timeseries", "all"

        Returns:
            Dict of metrics for the requested area
        """
        area = area.lower().strip()
        if area == "histograms":
            return self.get_all_histograms()
        elif area == "counters":
            return self.get_all_counters()
        elif area == "timeseries":
            return {"snapshots": self.get_time_series(), "count": len(self._time_series)}
        elif area == "all":
            return {
                "histograms": self.get_all_histograms(),
                "counters": self.get_all_counters(),
                "uptime_seconds": round(time.time() - self._start_time, 1),
                "time_series_count": len(self._time_series),
                "last_snapshot_time": self._last_snapshot_time,
            }
        else:
            return {"error": f"unknown area: {area}"}

    # ── Trend analysis ─────────────────────────────────────────────────

    def detect_trend(self, metric: str) -> dict[str, Any]:
        """Detect trend for a metric across time-series snapshots.

        Uses simple linear regression on the snapshot values. Pure Python —
        no numpy dependency.

        Args:
            metric: Dot-separated metric path within snapshot values

        Returns:
            Dict with direction (increasing/decreasing/stable), slope,
            r_squared, and sample count
        """
        points = self.get_timeseries(metric)
        if len(points) < 3:
            return {
                "direction": "stable",
                "slope": 0.0,
                "r_squared": 0.0,
                "samples": len(points),
            }

        timestamps = [p["timestamp"] for p in points]
        values = [p["value"] for p in points]
        n = len(points)

        x_mean = sum(timestamps) / n
        y_mean = sum(values) / n

        ss_xx = sum((x - x_mean) ** 2 for x in timestamps)
        if ss_xx == 0:
            return {
                "direction": "stable",
                "slope": 0.0,
                "r_squared": 0.0,
                "samples": n,
            }

        ss_xy = sum(
            (timestamps[i] - x_mean) * (values[i] - y_mean) for i in range(n)
        )
        slope = ss_xy / ss_xx
        ss_yy = sum((y - y_mean) ** 2 for y in values)
        r_squared = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy > 0 else 0.0

        relative_slope = (
            abs(slope) / max(abs(y_mean), 1e-10) if y_mean != 0 else abs(slope)
        )
        if relative_slope < 0.001:
            direction = "stable"
        elif slope > 0:
            direction = "increasing"
        else:
            direction = "decreasing"

        return {
            "direction": direction,
            "slope": round(slope, 8),
            "r_squared": round(r_squared, 6),
            "samples": n,
            "first_value": round(values[0], 6),
            "last_value": round(values[-1], 6),
        }

    # ── Persistence ────────────────────────────────────────────────────

    def save_to_disk(self, path: str) -> None:
        """Save tracker state to disk as JSON.

        Args:
            path: File path to write to
        """
        data = {
            "version": "1.0",
            "saved_at": time.time(),
            "start_time": self._start_time,
            "histograms": self.get_all_histograms(),
            "counters": self.get_all_counters(),
            "time_series": [
                {"timestamp": s.timestamp, "values": s.values}
                for s in self._time_series
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def load_from_disk(self, path: str) -> None:
        """Load tracker state from a JSON file saved by save_to_disk().

        Restores counters and time-series. Histograms are restored as
        summary stats (the raw bucket distribution is not preserved).

        Args:
            path: File path to load from
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._start_time = data.get("start_time", time.time())
        self._counters = dict(data.get("counters", {}))

        self._time_series.clear()
        for entry in data.get("time_series", []):
            self._time_series.append(
                TimeSeriesSnapshot(
                    timestamp=entry["timestamp"],
                    values=entry["values"],
                )
            )

    # ── Reset ──────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset all metrics data."""
        self._histograms.clear()
        self._counters.clear()
        self._time_series.clear()
        self._start_time = time.time()
        self._last_snapshot_time = 0.0

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_value(data: dict[str, Any], path: str) -> float | None:
        """Extract a nested numeric value using a dot-separated path.

        Args:
            data: Dict to search
            path: Dot-separated path (e.g., "histograms.latency.p99")

        Returns:
            Float value if found and numeric, else None
        """
        parts = path.split(".")
        current: Any = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        if isinstance(current, (int, float)):
            return float(current)
        return None

    # ── Representation ─────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"MetricsTracker(histograms={len(self._histograms)}, "
            f"counters={len(self._counters)}, "
            f"time_series={len(self._time_series)})"
        )
