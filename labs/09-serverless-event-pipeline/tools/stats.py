"""Latency statistics (pure; tests/test_stats.py)."""

from __future__ import annotations

import math


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile (the definition CloudWatch's pNN also approximates).

    p in (0, 100]. Returns None for an empty list.
    """
    if not values:
        return None
    if not 0 < p <= 100:
        raise ValueError("p must be in (0, 100]")
    ordered = sorted(values)
    rank = math.ceil(p / 100.0 * len(ordered))
    return ordered[max(0, rank - 1)]


def summarize(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": min(values),
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
        "p99": percentile(values, 99),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


def max_in_flight(intervals: list[tuple[float, float]]) -> int:
    """Peak number of overlapping [start, end) intervals: the client's real max concurrency."""
    events = []
    for start, end in intervals:
        events.append((start, 1))
        events.append((end, -1))
    events.sort(key=lambda e: (e[0], e[1]))  # an end at t sorts before a start at t
    peak = cur = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak
