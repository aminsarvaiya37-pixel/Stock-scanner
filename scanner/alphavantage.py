"""Alpha Vantage for Canada (.TRT) and India (.BSE).

The free key allows 25 calls a day and about 1 call a second, so this source rotates:
each run spends its budget on (1) stocks the Google Sheet feed could not price, then
(2) the stocks Alpha Vantage refreshed longest ago. A premium key just raises
`daily_calls` in config.json.
"""
from __future__ import annotations

import csv
import logging
import time
from datetime import date
from pathlib import Path
from typing import Callable

from .common import DATA, to_float
from .scoring import price_metrics

log = logging.getLogger(__name__)

API = "https://www.alphavantage.co/query"
CACHE_PATH = DATA / "intl_av_cache.csv"
CACHE_FIELDS = ["Market", "Ticker", "close", "close_date", "chg_5d", "chg_1m", "below_100d_high", "scanned_on"]
SUFFIX = {"Canada": ".TRT", "India": ".BSE"}


class LimitReached(Exception):
    pass


def av_symbol(market: str, ticker: str) -> str | None:
    suffix = SUFFIX.get(market)
    if not suffix:
        return None
    base = ticker.replace(".", "-") if market == "Canada" else ticker
    return base + suffix


def read_cache(path: Path = CACHE_PATH) -> dict[tuple[str, str], dict]:
    if not path.exists():
        return {}
    with path.open() as fh:
        return {(r["Market"], r["Ticker"]): r for r in csv.DictReader(fh)}


def write_cache(cache: dict[tuple[str, str], dict], path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CACHE_FIELDS)
        w.writeheader()
        for key in sorted(cache):
            w.writerow({k: cache[key].get(k, "") for k in CACHE_FIELDS})


def pick_targets(universe: list[tuple[str, str]], missing: set[tuple[str, str]],
                 cache: dict[tuple[str, str], dict], budget: int) -> list[tuple[str, str]]:
    """Gaps in the sheet feed first, then never-scanned, then oldest-scanned."""
    def order(key):
        return (key not in missing, cache.get(key, {}).get("scanned_on", ""), universe.index(key))
    return sorted(universe, key=order)[:budget]


class AlphaVantage:
    def __init__(self, session, api_key: str, cfg: dict, sleep: Callable[[float], None] = time.sleep):
        self.session = session
        self.api_key = api_key
        self.budget = cfg["daily_calls"]
        self.pause = cfg["seconds_between_calls"]
        self.sleep = sleep
        self.calls = 0

    def daily_closes(self, symbol: str) -> list[tuple[str, float]]:
        if self.calls:
            self.sleep(self.pause)
        self.calls += 1
        resp = self.session.get(API, params={
            "function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "compact", "apikey": self.api_key,
        }, timeout=60)
        payload = resp.json()
        series = payload.get("Time Series (Daily)")
        if series:
            rows = []
            for day, bar in series.items():
                close = to_float(bar.get("4. close"))
                if close is not None:
                    rows.append((day[:10], close))
            return sorted(rows)
        message = payload.get("Note") or payload.get("Information") or payload.get("Error Message") or "no data"
        if "rate limit" in message.lower() or "requests per day" in message.lower() or "premium" in message.lower():
            raise LimitReached(message)
        raise ValueError(message)

    def refresh(self, targets: list[tuple[str, str]], cache: dict, today: date) -> tuple[int, dict[tuple[str, str], str]]:
        ok, errors = 0, {}
        for market, ticker in targets:
            symbol = av_symbol(market, ticker)
            if not symbol:
                continue
            try:
                metrics = price_metrics(self.daily_closes(symbol))
            except LimitReached as exc:
                errors[(market, ticker)] = "daily limit reached"
                log.warning("Alpha Vantage limit reached: %s", exc)
                break
            except Exception as exc:
                errors[(market, ticker)] = str(exc)[:120]
                continue
            if metrics:
                cache[(market, ticker)] = {"Market": market, "Ticker": ticker, **metrics, "scanned_on": today.isoformat()}
                ok += 1
        return ok, errors
