"""Latency / error-rate statistics for eval runs (stdlib only, deterministic given a seed)."""
from __future__ import annotations

import math
import random
from collections import Counter
from typing import Iterable, Sequence


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile, q in [0, 100]. Raises ValueError on empty input."""
    if not values:
        raise ValueError("percentile of empty sequence")
    if not 0 <= q <= 100:
        raise ValueError("q must be within [0, 100]")
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(xs[lo])
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion. (0, 1) for n == 0 (no information)."""
    if n <= 0:
        return (0.0, 1.0)
    if not 0 <= successes <= n:
        raise ValueError("successes must be within [0, n]")
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_ci_median(values: Sequence[float], n_boot: int = 2000, alpha: float = 0.05,
                        seed: int = 12345) -> tuple[float, float]:
    """Percentile-bootstrap confidence interval of the median."""
    if not values:
        raise ValueError("bootstrap of empty sequence")
    rng = random.Random(seed)
    n = len(values)
    meds = []
    for _ in range(n_boot):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        meds.append(percentile(sample, 50))
    return (percentile(meds, 100 * alpha / 2), percentile(meds, 100 * (1 - alpha / 2)))


def summarize_latencies(values: Sequence[float]) -> dict:
    """p50/p90/p95/p99 plus count/min/max and a bootstrap CI of the median."""
    if not values:
        return {"n": 0}
    lo, hi = bootstrap_ci_median(values) if len(values) > 1 else (values[0], values[0])
    return {
        "n": len(values),
        "min": min(values),
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values),
        "median_ci95": [lo, hi],
    }


def error_rates(records: Iterable[dict]) -> dict:
    """Per-class error rates with Wilson intervals. A record with ``error_class`` None/"" is a success."""
    recs = list(records)
    n = len(recs)
    classes = Counter(r.get("error_class") or "ok" for r in recs)
    out = {"n": n, "classes": {}}
    for cls, k in sorted(classes.items()):
        lo, hi = wilson_interval(k, n)
        out["classes"][cls] = {"count": k, "rate": (k / n if n else 0.0), "wilson95": [lo, hi]}
    errs = n - classes.get("ok", 0)
    lo, hi = wilson_interval(errs, n)
    out["error_rate"] = errs / n if n else 0.0
    out["error_wilson95"] = [lo, hi]
    out["fallback_rate"] = (sum(1 for r in recs if r.get("fallback_used")) / n) if n else 0.0
    out["empty_rate"] = (sum(1 for r in recs if r.get("empty")) / n) if n else 0.0
    return out


def compare_paired(base: Sequence[float], cand: Sequence[float], n_boot: int = 2000,
                   seed: int = 777) -> dict:
    """Median improvement (base - cand, positive = faster) with a bootstrap CI over independent samples.

    "Improved" is only claimed when the whole CI is above zero, i.e. the gain exceeds the noise.
    """
    if not base or not cand:
        raise ValueError("both arms need samples")
    rng = random.Random(seed)
    diffs = []
    for _ in range(n_boot):
        b = [base[rng.randrange(len(base))] for _ in base]
        c = [cand[rng.randrange(len(cand))] for _ in cand]
        diffs.append(percentile(b, 50) - percentile(c, 50))
    lo, hi = percentile(diffs, 2.5), percentile(diffs, 97.5)
    return {
        "median_base": percentile(base, 50),
        "median_cand": percentile(cand, 50),
        "median_gain": percentile(base, 50) - percentile(cand, 50),
        "gain_ci95": [lo, hi],
        "improved": lo > 0,
        "regressed": hi < 0,
        "n_base": len(base),
        "n_cand": len(cand),
    }


async def wait_until_idle(get_active_requests, timeout_s: float, poll_s: float = 0.05) -> tuple[bool, float]:
    """Slot-leak check: poll ``get_active_requests()`` (sum of in-flight slots) until it reaches 0.

    Returns (released, waited_seconds). ``get_active_requests`` may be sync or async.
    """
    import asyncio
    import inspect
    import time

    t0 = time.monotonic()
    while True:
        v = get_active_requests()
        if inspect.isawaitable(v):
            v = await v
        if v == 0:
            return True, time.monotonic() - t0
        if time.monotonic() - t0 >= timeout_s:
            return False, time.monotonic() - t0
        await asyncio.sleep(poll_s)
