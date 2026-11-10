"""Daily scan: one third of the US universe via Twelve Data, Canada + India via the Google Sheet feed.

Usage:
    TWELVE_DATA_API_KEY=... python -m scanner.run
    python -m scanner.run --skip-twelvedata      # re-rank from cached US data only
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
from datetime import date, datetime, timezone

import requests

from . import alphavantage as av
from . import performance
from . import sheetfeed
from .astro import load_astro
from .common import DATA, DOCS_DATA, clean, load_config, load_state, save_state, to_float
from .scoring import classify, price_metrics, score_market
from .twelvedata import TwelveData
from .universe import batch_for_day, load_universe

log = logging.getLogger("scanner")

US_CACHE = DATA / "us_cache.csv"
CACHE_FIELDS = ["Ticker", "close", "close_date", "chg_5d", "chg_1m", "below_100d_high", "scanned_on"]

RANKINGS_COLUMNS = [
    "Logged at (UTC)", "Cycle start", "Market", "Rank in market", "Type", "Ticker", "Company",
    "Combined score", "Fundamental score", "Astro score", "Fundamental weight %", "Last close",
    "Currency", "Close date", "5-session change %", "1-month change %", "Below 100-day high %",
    "Trailing P/E", "Price source",
]
CURRENCY = {"USA": "USD", "Canada": "CAD", "India": "INR"}


def read_cache() -> dict[str, dict]:
    if not US_CACHE.exists():
        return {}
    with US_CACHE.open() as fh:
        return {r["Ticker"]: r for r in csv.DictReader(fh)}


def write_cache(cache: dict[str, dict]) -> None:
    with US_CACHE.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CACHE_FIELDS)
        w.writeheader()
        for t in sorted(cache):
            w.writerow({k: cache[t].get(k, "") for k in CACHE_FIELDS})


def read_feed(session, cfg: dict, notes: list[str]) -> dict[str, list]:
    feed = {}
    for key, gid in cfg["feed_tabs"].items():
        try:
            feed[key] = sheetfeed.fetch_rows(session, cfg["feed_sheet_id"], gid)
        except Exception as exc:
            feed[key] = []
            reason = str(exc) if isinstance(exc, sheetfeed.FeedUnavailable) else type(exc).__name__
            notes.append(f"Sheet feed '{key}' unavailable ({reason}).")
    return feed


def us_rows(universe, cache, us_feed) -> list[dict]:
    fundamentals = {r.get("Ticker", "").upper(): r for r in sheetfeed.rows_to_dicts(us_feed)}
    rows = []
    for t in universe:
        c = cache.get(t)
        if not c or to_float(c.get("close")) is None:
            continue
        f = fundamentals.get(t, {})
        rows.append({
            "market": "USA", "ticker": t, "company": f.get("Name", ""),
            "pe": to_float(f.get("PE")), "market_cap": to_float(f.get("Market cap")),
            "currency": "USD",
            "close": to_float(c["close"]), "close_date": c.get("close_date", ""),
            "chg_5d": to_float(c.get("chg_5d")), "chg_1m": to_float(c.get("chg_1m")),
            "below_100d_high": to_float(c.get("below_100d_high")),
            "price_source": f"Twelve Data, scanned {c.get('scanned_on', '')}",
        })
    return rows


def intl_rows(intl_feed) -> list[dict]:
    rows = []
    for r in sheetfeed.rows_to_dicts(intl_feed):
        market, ticker = r.get("Market", ""), r.get("Ticker", "")
        close = to_float(r.get("Close"))
        if not market or not ticker or close is None:
            continue
        rows.append({
            "market": market, "ticker": ticker, "company": r.get("Name", ""),
            "pe": to_float(r.get("PE")), "market_cap": to_float(r.get("Market cap")),
            "currency": r.get("Currency") or CURRENCY.get(market, ""),
            "close": close, "close_date": r.get("Close date", ""),
            "chg_5d": to_float(r.get("Chg 5 sessions %")), "chg_1m": to_float(r.get("Chg 1 month %")),
            "below_100d_high": to_float(r.get("Below 100-day high %")),
            "price_source": "Google Finance (sheet feed)",
        })
    return rows


def intl_universe(feed_rows: list[dict]) -> list[tuple[str, str]]:
    """Canada + India tickers: the sheet feed's list (your Screen tab) plus data/universe_intl.csv."""
    keys = [(r["market"], r["ticker"]) for r in feed_rows]
    path = DATA / "universe_intl.csv"
    if path.exists():
        with path.open() as fh:
            keys += [(r["Market"], r["Ticker"]) for r in csv.DictReader(fh)]
    return list(dict.fromkeys(k for k in keys if k[0] in av.SUFFIX))


def scan_intl_av(session, cfg, universe, feed_rows, cache, today, notes) -> dict:
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    info = {"refreshed": 0, "errors": 0, "budget": cfg["alphavantage"]["daily_calls"]}
    if not api_key:
        notes.append("ALPHA_VANTAGE_API_KEY not set: Canada/India use the Google Sheet feed only.")
        return info
    priced = {(r["market"], r["ticker"]) for r in feed_rows}
    missing = {k for k in universe if k not in priced}
    targets = av.pick_targets(universe, missing, cache, cfg["alphavantage"]["daily_calls"])
    client = av.AlphaVantage(session, api_key, cfg["alphavantage"])
    ok, errors = client.refresh(targets, cache, today)
    info.update(refreshed=ok, errors=len(errors), calls=client.calls)
    if errors:
        sample = ", ".join(f"{t}: {e}" for (_, t), e in list(errors.items())[:3])
        notes.append(f"Alpha Vantage: {len(errors)} of {len(targets)} lookups failed (e.g. {sample}).")
    return info


def merge_av(feed_rows, universe, cache, today, max_age_days, notes) -> tuple[list[dict], dict]:
    """Fill sheet-feed gaps from Alpha Vantage and flag price disagreements."""
    rows = list(feed_rows)
    have = {(r["market"], r["ticker"]): r for r in feed_rows}
    filled, mismatches = 0, []
    for key in universe:
        c = cache.get(key)
        if not c or to_float(c.get("close")) is None:
            continue
        age = (today - date.fromisoformat(c["scanned_on"])).days if c.get("scanned_on") else 999
        row = have.get(key)
        if row is None:
            if age > max_age_days:
                continue
            market, ticker = key
            rows.append({
                "market": market, "ticker": ticker, "company": "", "pe": None, "market_cap": None,
                "currency": CURRENCY.get(market, ""), "close": to_float(c["close"]), "close_date": c.get("close_date", ""),
                "chg_5d": to_float(c.get("chg_5d")), "chg_1m": to_float(c.get("chg_1m")),
                "below_100d_high": to_float(c.get("below_100d_high")),
                "price_source": f"Alpha Vantage, scanned {c.get('scanned_on', '')}",
            })
            filled += 1
        elif row.get("close_date") == c.get("close_date"):
            gap = abs(row["close"] / to_float(c["close"]) - 1) * 100
            if gap > 3:
                mismatches.append(f"{key[1]} ({gap:.1f}%)")
            else:
                row["price_source"] += " · checked by Alpha Vantage"
    if mismatches:
        notes.append(f"Google Finance and Alpha Vantage disagree by >3% on: {', '.join(mismatches[:8])}.")
    return rows, {"filled_gaps": filled, "mismatches": len(mismatches)}


def tracker_section(feed) -> dict:
    picks = [p for p in sheetfeed.rows_to_dicts(feed.get("tracker_picks", [])) if p.get("Ticker")]
    summary = [row for row in feed.get("tracker_summary", []) if any(c.strip() for c in row)]
    return {"picks": picks, "summary": summary}


def scan_us_batch(session, cfg, state, universe, cache, today, notes) -> dict:
    cycle_days = cfg["cycle_days"]
    batch_index = state.get("next_batch", 0) % cycle_days
    batch = batch_for_day(universe, batch_index, cycle_days)
    api_key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
    info = {"index": batch_index + 1, "of": cycle_days, "requested": len(batch), "scanned": 0, "errors": 0}
    if not api_key:
        notes.append("TWELVE_DATA_API_KEY not set: US prices not refreshed this run.")
        return info

    client = TwelveData(session, api_key, cfg["twelvedata"])
    histories, errors = client.histories(batch)
    for t, closes in histories.items():
        m = price_metrics(closes)
        if m:
            cache[t] = {"Ticker": t, **{k: m[k] for k in m}, "scanned_on": today.isoformat()}
    info.update(scanned=len(histories), errors=len(errors), credits=client.credits_used)
    if errors:
        sample = ", ".join(f"{k}: {v}" for k, v in list(errors.items())[:5])
        notes.append(f"{len(errors)} US symbols failed this batch (e.g. {sample}).")
    if batch_index == 0:
        state["cycle_start"] = today.isoformat()
    state["next_batch"] = (batch_index + 1) % cycle_days
    return info


def to_rankings_row(r, logged_at, cycle_start) -> list:
    def num(v, nd=2):
        return "" if v is None else round(v, nd)
    return [
        logged_at, cycle_start, r["market"], r["rank"], r["type"], r["ticker"], r["company"],
        num(r["combined_score"], 1), num(r["fundamental_score"], 1), num(r["astro_score"], 1),
        num(r["fundamental_weight_pct"], 0), num(r["close"]), r["currency"], r["close_date"],
        num(r["chg_5d"]), num(r["chg_1m"]), num(r["below_100d_high"]), num(r["pe"]), r["price_source"],
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-twelvedata", action="store_true")
    parser.add_argument("--skip-alphavantage", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    cfg = load_config()
    state = load_state()
    now = datetime.now(timezone.utc)
    today = now.date()
    notes: list[str] = []
    session = requests.Session()

    at_cycle_start = state.get("next_batch", 0) % cfg["cycle_days"] == 0
    universe = load_universe(session, cfg, state, today, allow_refresh=at_cycle_start)
    cache = read_cache()

    if args.skip_twelvedata:
        batch = {"index": state.get("next_batch", 0) + 1, "of": cfg["cycle_days"], "requested": 0, "scanned": 0, "errors": 0}
    else:
        batch = scan_us_batch(session, cfg, state, universe, cache, today, notes)
    write_cache(cache)

    feed = read_feed(session, cfg, notes)
    feed_intl = intl_rows(feed.get("intl", []))
    universe_intl = intl_universe(feed_intl)
    av_cache = av.read_cache()
    if args.skip_alphavantage:
        av_info = {"refreshed": 0, "errors": 0, "budget": cfg["alphavantage"]["daily_calls"]}
    else:
        av_info = scan_intl_av(session, cfg, universe_intl, feed_intl, av_cache, today, notes)
    av.write_cache(av_cache)
    merged_intl, merge_info = merge_av(feed_intl, universe_intl, av_cache, today,
                                      cfg["alphavantage"]["max_age_days"], notes)
    av_info.update(merge_info, covered=sum(1 for k in universe_intl if k in av_cache))

    astro = load_astro(today)
    rows = us_rows(universe, cache, feed.get("us", [])) + merged_intl

    markets: dict[str, list[dict]] = {}
    for r in rows:
        r["type"] = classify(r["market_cap"], r["market"], cfg["blue_chip_market_cap"])
        markets.setdefault(r["market"], []).append(r)
    for market, mrows in markets.items():
        score_market(mrows, cfg["fundamental_components"], astro, cfg["fundamental_weight_pct"])

    logged_at = now.strftime("%Y-%m-%d %H:%M")
    cycle_start = state.get("cycle_start", today.isoformat())
    order = ["USA", "Canada", "India"] + sorted(m for m in markets if m not in ("USA", "Canada", "India"))
    table = [to_rankings_row(r, logged_at, cycle_start) for m in order for r in markets.get(m, [])]

    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    with (DOCS_DATA / "rankings.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(RANKINGS_COLUMNS)
        w.writerows(table)

    history = DATA / "history" / f"{today:%Y-%m}.csv"
    history.parent.mkdir(parents=True, exist_ok=True)
    new_file = not history.exists()
    with history.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new_file:
            w.writerow(RANKINGS_COLUMNS)
        w.writerows(table)

    picks_added = performance.log_picks(markets, today)
    results = performance.evaluate(performance.read_log(), performance.load_prices(), today)
    perf = {"generated_at": now.isoformat(timespec="seconds"), "picks_logged_today": picks_added,
            "horizons_trading_days": performance.HORIZONS, "verdict": performance.verdict(results),
            "results": results}
    (DOCS_DATA / "performance.json").write_text(json.dumps(perf, indent=1))

    keys = ["rank", "ticker", "company", "type", "combined_score", "fundamental_score", "astro_score",
            "astro_reason", "close", "currency", "close_date", "chg_5d", "chg_1m", "below_100d_high", "pe",
            "price_source"]
    latest = {
        "generated_at": now.isoformat(timespec="seconds"),
        "cycle_start": cycle_start,
        "batch": batch,
        "us_universe": len(universe),
        "us_with_data": len(markets.get("USA", [])),
        "intl_universe": len(universe_intl),
        "alpha_vantage": av_info,
        "astro_enabled": bool(astro),
        "fundamental_weight_pct": cfg["fundamental_weight_pct"] if astro else 100,
        "notes": notes,
        "markets": {m: [{k: clean(r.get(k)) for k in keys} for r in markets.get(m, [])] for m in order if m in markets},
        "tracker": tracker_section(feed),
        "performance_verdict": perf["verdict"],
    }
    (DOCS_DATA / "latest.json").write_text(json.dumps(latest, separators=(",", ":")))
    save_state(state)

    for n in notes:
        log.warning(n)
    log.info("Ranked %s", {m: len(v) for m, v in markets.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
