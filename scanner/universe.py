"""US scan universe: your core list, then S&P 500, MidCap 400 and SmallCap 600, then
Russell 1000 and Russell 2000 members (iShares holdings, largest first) up to us_universe_size."""
from __future__ import annotations

import csv
import logging
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
# iShares Russell 1000 / Russell 2000 ETF holdings files, listed largest first
ISHARES_HOLDINGS = [
    "https://www.ishares.com/us/products/239707/ishares-russell-1000-etf/1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund",
    "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/1467271812596.ajax?fileType=csv&fileName=IWM_holdings&dataType=fund",
]


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


def parse_ishares_csv(text: str) -> list[str]:
    """US equity tickers from an iShares holdings CSV (metadata lines come before the header)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.lstrip('﻿"').startswith("Ticker")), None)
    if start is None:
        raise ValueError("no Ticker header in holdings file")
    out = []
    for row in csv.DictReader(StringIO("\n".join(lines[start:]))):
        t = (row.get("Ticker") or "").strip()
        if not t or t == "-" or (row.get("Asset Class") or "").strip() != "Equity":
            continue
        if (row.get("Location") or "United States").strip() != "United States":
            continue
        out.append(t.replace("/", "."))
    return out


def fetch_ishares(session, url: str) -> list[str]:
    resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    return parse_ishares_csv(resp.content.decode("utf-8-sig", errors="replace"))


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
    for url in ISHARES_HOLDINGS:
        if len(merge_unique(core, *index_lists, *extra, limit=size)) >= size:
            break
        try:
            extra.append(fetch_ishares(session, url))
        except Exception as exc:
            log.warning("Skipping iShares holdings (%s): %s", url.split("fileName=")[1][:12], exc)
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
