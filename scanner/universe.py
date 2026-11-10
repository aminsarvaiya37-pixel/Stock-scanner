"""US scan universe: your core list, then S&P 500, MidCap 400, SmallCap 600, then the
largest remaining US stocks by market cap (Nasdaq screener) up to us_universe_size."""
from __future__ import annotations

import csv
import logging
import re
from datetime import date
from io import StringIO
from pathlib import Path

from .common import DATA, DOCS_DATA, USER_AGENT

log = logging.getLogger(__name__)

CORE_PATH = DATA / "core_us.csv"
UNIVERSE_PATH = DATA / "universe_us.csv"
PUBLIC_UNIVERSE_PATH = DOCS_DATA / "universe_us.csv"

WIKI_PAGES = [
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
]
OPTIONAL_WIKI_PAGES = [
    "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
]
NASDAQ_SCREENER = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&download=true"
SKIP_NAME = re.compile(r"warrant|\bunits?\b|\brights?\b|preferred|\bnotes?\b|%", re.I)
MIN_PRICE = 3.0


def read_tickers(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open() as fh:
        return [row["Ticker"].strip() for row in csv.DictReader(fh) if row.get("Ticker", "").strip()]


def write_tickers(path: Path, tickers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Ticker\n" + "\n".join(tickers) + "\n")


def merge_unique(*lists: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tickers in lists:
        for t in tickers:
            t = t.strip().upper()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
                if len(out) >= limit:
                    return out
    return out


def fetch_index_members(session, url: str) -> list[str]:
    import pandas as pd

    resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    for table in pd.read_html(StringIO(resp.text)):
        cols = [str(c) for c in table.columns]
        for name in ("Symbol", "Ticker symbol", "Ticker"):
            if name in cols:
                return [str(s).strip() for s in table[name].dropna().tolist()]
    raise ValueError(f"no ticker column found at {url}")


def _money(text) -> float | None:
    try:
        return float(str(text or "").replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def fetch_largest_us(session) -> list[str]:
    """All US-listed common stocks from Nasdaq's free screener, largest market cap first."""
    resp = session.get(NASDAQ_SCREENER, timeout=60,
                       headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    resp.raise_for_status()
    rows = []
    for row in resp.json()["data"]["rows"]:
        sym = str(row.get("symbol", "")).strip()
        cap, price = _money(row.get("marketCap")), _money(row.get("lastsale"))
        if not sym or "^" in sym or not cap or not price or price < MIN_PRICE:
            continue
        if SKIP_NAME.search(str(row.get("name", ""))):
            continue
        rows.append((cap, sym.replace("/", ".")))
    rows.sort(reverse=True)
    return [s for _, s in rows]


def build_universe(session, size: int) -> list[str] | None:
    core = read_tickers(CORE_PATH)
    try:
        index_lists = [fetch_index_members(session, url) for url in WIKI_PAGES]
    except Exception as exc:  # network or parse failure: keep the old universe
        log.warning("Could not refresh index members: %s", exc)
        return None
    extra: list[list[str]] = []
    for url in OPTIONAL_WIKI_PAGES:
        try:
            extra.append(fetch_index_members(session, url))
        except Exception as exc:
            log.warning("Skipping %s: %s", url, exc)
    if len(merge_unique(core, *index_lists, *extra, limit=size)) < size:
        try:
            extra.append(fetch_largest_us(session))
        except Exception as exc:
            log.warning("Nasdaq screener unavailable, universe stays at index members: %s", exc)
    return merge_unique(core, *index_lists, *extra, limit=size)


def load_universe(session, cfg: dict, state: dict, today: date, allow_refresh: bool) -> list[str]:
    """Return the US universe, rebuilding it when stale (only at a cycle start)."""
    current = read_tickers(UNIVERSE_PATH)
    built = state.get("universe_built")
    age = (today - date.fromisoformat(built)).days if built else None
    stale = not current or age is None or age >= cfg["universe_refresh_days"]

    if stale and allow_refresh:
        fresh = build_universe(session, cfg["us_universe_size"])
        if fresh:
            current = fresh
            state["universe_built"] = today.isoformat()
            log.info("Universe rebuilt: %d tickers", len(current))

    if not current:
        current = read_tickers(CORE_PATH)[: cfg["us_universe_size"]]
        log.warning("Falling back to core list (%d tickers)", len(current))

    write_tickers(UNIVERSE_PATH, current)
    write_tickers(PUBLIC_UNIVERSE_PATH, current)
    return current


def batch_for_day(universe: list[str], batch_index: int, cycle_days: int) -> list[str]:
    """Split the universe into `cycle_days` interleaved batches."""
    return universe[batch_index::cycle_days]
