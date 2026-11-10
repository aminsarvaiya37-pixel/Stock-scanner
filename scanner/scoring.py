"""Price metrics and scores. Pure functions, easy to test."""
from __future__ import annotations

from .common import to_float


def price_metrics(closes: list[tuple[str, float]]) -> dict | None:
    """From ascending (date, close) pairs: last close, 5-session and 1-month change, gap to 100-day high."""
    if not closes:
        return None
    values = [c for _, c in closes]
    last = values[-1]
    n = len(values)
    recent = values[-100:]
    return {
        "close": last,
        "close_date": closes[-1][0],
        "chg_5d": (last / values[-6] - 1) * 100 if n > 5 else None,
        "chg_1m": (last / values[-22] - 1) * 100 if n > 21 else None,
        "below_100d_high": (1 - last / max(recent)) * 100,
    }


def percentile_ranks(values: list[float | None]) -> list[float]:
    """Rank in [0,1] (1 = best = highest value). Missing values get 0."""
    present = sorted(v for v in values if v is not None)
    if not present:
        return [0.0 for _ in values]
    if len(present) == 1:
        return [1.0 if v is not None else 0.0 for v in values]
    out = []
    for v in values:
        if v is None:
            out.append(0.0)
            continue
        below = sum(1 for p in present if p < v)
        equal = sum(1 for p in present if p == v)
        out.append((below + (equal - 1) / 2) / (len(present) - 1))
    return out


def earnings_yield(pe) -> float | None:
    """1/PE. Loss-makers and nonsense P/Es (< 1) count as missing."""
    pe = to_float(pe)
    if pe is None or pe < 1:
        return None
    return 1 / pe


def score_market(rows: list[dict], weights: dict, astro: dict, fundamental_weight_pct: float) -> list[dict]:
    """Score and rank one market's rows in place; returns them sorted by combined score."""
    if not rows:
        return rows
    ey = percentile_ranks([earnings_yield(r.get("pe")) for r in rows])
    m1 = percentile_ranks([r.get("chg_1m") for r in rows])
    m5 = percentile_ranks([r.get("chg_5d") for r in rows])
    near = percentile_ranks([-r["below_100d_high"] if r.get("below_100d_high") is not None else None for r in rows])

    use_astro = bool(astro)
    fw = fundamental_weight_pct if use_astro else 100.0
    for i, r in enumerate(rows):
        fundamental = 100 * (
            weights["earnings_yield"] * ey[i]
            + weights["momentum_1m"] * m1[i]
            + weights["momentum_5d"] * m5[i]
            + weights["near_100d_high"] * near[i]
        )
        a_score, a_reason = astro.get((r["market"], r["ticker"].upper()), (None, ""))
        astro_for_mix = a_score if a_score is not None else 50.0  # neutral when no reading
        r["fundamental_score"] = fundamental
        r["astro_score"] = a_score
        r["astro_reason"] = a_reason
        r["fundamental_weight_pct"] = fw
        r["combined_score"] = fundamental * fw / 100 + (astro_for_mix * (100 - fw) / 100 if use_astro else 0)

    rows.sort(key=lambda r: r["combined_score"], reverse=True)
    for rank, r in enumerate(rows, start=1):
        r["rank"] = rank
    return rows


def classify(market_cap, market: str, thresholds: dict) -> str:
    cap = to_float(market_cap)
    if cap is None:
        return "Unknown"
    return "Blue chip" if cap >= thresholds.get(market, float("inf")) else "Small cap"
