"""Scorekeeping: do the picks actually beat the market?

Every run logs three sets of picks per market (4 blue chips + 2 small caps each):
  App               ranked by combined score (what you see in the app)
  Fundamental only  ranked without the astro score (shows whether astro adds anything)
  Random            picked at random from the same list (luck baseline)

Later runs check each logged pick 5, 20 and 60 trading days on, using the
prices saved in data/history/, and compare it with the average stock in the
same market over the same dates.
"""
from __future__ import annotations

import bisect
import csv
import random
from datetime import date, timedelta
from pathlib import Path

from .common import DATA, to_float

PICKS_LOG = DATA / "picks_log.csv"
HISTORY_DIR = DATA / "history"
PICK_FIELDS = ["Pick date", "Market", "Group", "Ticker", "Type", "Entry close", "Entry close date",
               "Combined score", "Fundamental score", "Astro score"]
GROUPS = ["App", "Fundamental only", "Random"]
HORIZONS = [5, 20, 60]
MAX_ENTRY_AGE_DAYS = 6          # skip picks whose last price is older than this


def add_trading_days(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def choose(rows: list[dict], key: str, blue: int = 4, small: int = 2) -> list[dict]:
    ranked = sorted(rows, key=lambda r: r.get(key) or 0, reverse=True)
    return ([r for r in ranked if r.get("type") == "Blue chip"][:blue]
            + [r for r in ranked if r.get("type") == "Small cap"][:small])


def choose_random(rows: list[dict], seed: str, blue: int = 4, small: int = 2) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for kind, n in (("Blue chip", blue), ("Small cap", small)):
        pool = sorted((r for r in rows if r.get("type") == kind), key=lambda r: r["ticker"])
        out += rng.sample(pool, min(n, len(pool)))
    return out


def read_log(path: Path = PICKS_LOG) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as fh:
        return list(csv.DictReader(fh))


def log_picks(markets: dict[str, list[dict]], today: date, path: Path = PICKS_LOG) -> int:
    """Append today's picks once per day. Returns how many rows were added."""
    existing = read_log(path)
    if any(r["Pick date"] == today.isoformat() for r in existing):
        return 0
    fresh_after = today - timedelta(days=MAX_ENTRY_AGE_DAYS)
    new = []
    for market, rows in markets.items():
        usable = [r for r in rows if r.get("close") is not None and r.get("close_date")
                  and date.fromisoformat(str(r["close_date"])[:10]) >= fresh_after]
        sets = {
            "App": choose(usable, "combined_score"),
            "Fundamental only": choose(usable, "fundamental_score"),
            "Random": choose_random(usable, f"{today.isoformat()}-{market}"),
        }
        for group, picks in sets.items():
            for r in picks:
                new.append({
                    "Pick date": today.isoformat(), "Market": market, "Group": group,
                    "Ticker": r["ticker"], "Type": r.get("type", ""),
                    "Entry close": r["close"], "Entry close date": str(r["close_date"])[:10],
                    "Combined score": round(r.get("combined_score") or 0, 1),
                    "Fundamental score": round(r.get("fundamental_score") or 0, 1),
                    "Astro score": "" if r.get("astro_score") is None else r["astro_score"],
                })
    if not new:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PICK_FIELDS)
        if needs_header:
            w.writeheader()
        w.writerows(new)
    return len(new)


def load_prices(history_dir: Path = HISTORY_DIR) -> dict[tuple[str, str], list[tuple[date, float]]]:
    """(market, ticker) -> sorted unique (close date, close) from the daily rankings history."""
    seen: dict[tuple[str, str], dict[date, float]] = {}
    for f in sorted(history_dir.glob("*.csv")):
        with f.open() as fh:
            for r in csv.DictReader(fh):
                close, d = to_float(r.get("Last close")), (r.get("Close date") or "")[:10]
                if close is None or not d:
                    continue
                try:
                    seen.setdefault((r["Market"], r["Ticker"]), {})[date.fromisoformat(d)] = close
                except ValueError:
                    continue
    return {k: sorted(v.items()) for k, v in seen.items()}


def price_on_or_after(series, d: date):
    i = bisect.bisect_left(series, (d, float("-inf")))
    return series[i] if i < len(series) else None


def price_on_or_before(series, d: date):
    i = bisect.bisect_right(series, (d, float("inf")))
    return series[i - 1] if i else None


def market_average(prices, market: str, start: date, end: date, cache: dict) -> float | None:
    key = (market, start, end)
    if key not in cache:
        rets = []
        for (m, _), series in prices.items():
            if m != market:
                continue
            a, b = price_on_or_before(series, start), price_on_or_after(series, end)
            if a and b and a[1] > 0 and b[0] > a[0]:
                rets.append(b[1] / a[1] - 1)
        cache[key] = sum(rets) / len(rets) if len(rets) >= 20 else None
    return cache[key]


def evaluate(picks: list[dict], prices, today: date) -> dict:
    """Average return, return vs the market's average stock, and hit rate per market/group/horizon."""
    stats: dict = {}
    avg_cache: dict = {}
    for p in picks:
        entry, entry_day = to_float(p["Entry close"]), date.fromisoformat(p["Entry close date"])
        series = prices.get((p["Market"], p["Ticker"]), [])
        for h in HORIZONS:
            target = add_trading_days(date.fromisoformat(p["Pick date"]), h)
            if target > today or not entry:
                continue
            exit_ = price_on_or_after(series, target)
            if not exit_:
                continue
            ret = exit_[1] / entry - 1
            bench = market_average(prices, p["Market"], entry_day, target, avg_cache)
            s = stats.setdefault(p["Market"], {}).setdefault(p["Group"], {}).setdefault(
                str(h), {"n": 0, "ret": 0.0, "excess": 0.0, "n_bench": 0, "beat": 0})
            s["n"] += 1
            s["ret"] += ret
            if bench is not None:
                s["n_bench"] += 1
                s["excess"] += ret - bench
                s["beat"] += ret > bench
    out = {}
    for market, groups in stats.items():
        for group, hs in groups.items():
            for h, s in hs.items():
                out.setdefault(market, {}).setdefault(group, {})[h] = {
                    "picks_checked": s["n"],
                    "avg_return_pct": round(100 * s["ret"] / s["n"], 2),
                    "vs_market_avg_pct": round(100 * s["excess"] / s["n_bench"], 2) if s["n_bench"] else None,
                    "beat_market_pct": round(100 * s["beat"] / s["n_bench"]) if s["n_bench"] else None,
                }
    return out


def verdict(results: dict, min_picks: int = 60) -> list[str]:
    """Plain-language lines. Needs enough checked picks before saying anything."""
    lines = []
    for market, groups in results.items():
        app = groups.get("App", {}).get("20")
        rnd = groups.get("Random", {}).get("20")
        fund = groups.get("Fundamental only", {}).get("20")
        if not app or app["picks_checked"] < min_picks or app["vs_market_avg_pct"] is None:
            n = app["picks_checked"] if app else 0
            lines.append(f"{market}: too early - {n} of {min_picks} picks checked at 20 days.")
            continue
        msg = f"{market}: app picks {app['vs_market_avg_pct']:+.2f}% vs the average stock over 20 days"
        if rnd and rnd["vs_market_avg_pct"] is not None:
            msg += f" (random picks {rnd['vs_market_avg_pct']:+.2f}%)"
        if fund and fund["vs_market_avg_pct"] is not None and fund["picks_checked"] >= min_picks:
            diff = app["vs_market_avg_pct"] - fund["vs_market_avg_pct"]
            msg += f"; astro adds {diff:+.2f}%"
        lines.append(msg + ".")
    return lines
