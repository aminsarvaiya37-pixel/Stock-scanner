from datetime import date

import pytest

from scanner import run, scoring, universe
from scanner.astro import load_astro
from scanner.common import to_float
from scanner.twelvedata import TwelveData


def closes(values):
    return [(f"2026-01-{i + 1:02d}", v) for i, v in enumerate(values)]


def test_price_metrics():
    vals = [100 + i for i in range(30)]  # 100..129
    m = scoring.price_metrics(closes(vals))
    assert m["close"] == 129
    assert m["chg_5d"] == pytest.approx((129 / 124 - 1) * 100)
    assert m["chg_1m"] == pytest.approx((129 / 108 - 1) * 100)
    assert m["below_100d_high"] == pytest.approx(0)


def test_price_metrics_short_history():
    m = scoring.price_metrics(closes([10, 8]))
    assert m["chg_5d"] is None and m["chg_1m"] is None
    assert m["below_100d_high"] == pytest.approx(20)


def test_percentile_ranks_missing_is_worst():
    assert scoring.percentile_ranks([3, None, 1, 2]) == [1.0, 0.0, 0.0, 0.5]


def test_earnings_yield_ignores_losses_and_junk():
    assert scoring.earnings_yield("20") == pytest.approx(0.05)
    assert scoring.earnings_yield("-5") is None
    assert scoring.earnings_yield("0.01") is None  # e.g. BRK.B bad feed value
    assert scoring.earnings_yield("") is None


def _rows():
    return [
        {"market": "USA", "ticker": "AAA", "pe": 10, "chg_1m": 5, "chg_5d": 1, "below_100d_high": 1},
        {"market": "USA", "ticker": "BBB", "pe": 40, "chg_1m": -5, "chg_5d": -1, "below_100d_high": 20},
    ]


WEIGHTS = {"earnings_yield": 0.4, "momentum_1m": 0.3, "momentum_5d": 0.1, "near_100d_high": 0.2}


def test_score_without_astro_is_pure_fundamental():
    rows = scoring.score_market(_rows(), WEIGHTS, {}, 70)
    assert [r["ticker"] for r in rows] == ["AAA", "BBB"]
    assert rows[0]["combined_score"] == pytest.approx(100)
    assert rows[0]["fundamental_weight_pct"] == 100
    assert rows[1]["astro_score"] is None


def test_astro_can_flip_ranking():
    astro = {("USA", "BBB"): (100.0, "Jupiter"), ("USA", "AAA"): (0.0, "Saturn")}
    rows = scoring.score_market(_rows(), WEIGHTS, astro, 30)
    assert rows[0]["ticker"] == "BBB"
    assert rows[0]["astro_reason"] == "Jupiter"


def test_classify():
    th = {"USA": 10e9}
    assert scoring.classify("2e10", "USA", th) == "Blue chip"
    assert scoring.classify(5e9, "USA", th) == "Small cap"
    assert scoring.classify("", "USA", th) == "Unknown"


def test_batches_cover_universe_once():
    uni = [f"T{i}" for i in range(800)]
    batches = [universe.batch_for_day(uni, k, 3) for k in range(3)]
    assert sum(len(b) for b in batches) == 800
    assert set().union(*map(set, batches)) == set(uni)
    assert max(len(b) for b in batches) <= 267


def test_merge_unique_keeps_core_first():
    assert universe.merge_unique(["b", "a"], ["A", "C", "D"], limit=3) == ["B", "A", "C"]


def test_to_float():
    assert to_float("1,234.5") == 1234.5
    assert to_float("#N/A") is None
    assert to_float("12%") == 12


def test_astro_file(tmp_path):
    p = tmp_path / "astro.csv"
    p.write_text(
        "Market,Ticker,Astro score,Reason,Valid until\n"
        "USA,aapl,120,strong,\n"
        "USA,MSFT,40,old,2020-01-01\n"
        "India,TCS,x,bad,\n"
    )
    scores = load_astro(date(2026, 11, 10), p)
    assert scores == {("USA", "AAPL"): (100.0, "strong")}


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params["symbol"])
        return FakeResp(self.payloads.pop(0))


TD_CFG = {"symbols_per_request": 2, "seconds_between_requests": 0, "daily_credit_cap": 3, "history_sessions": 130}


def series(*vals):
    return {"status": "ok", "values": [{"datetime": f"2026-01-0{i + 1}", "close": str(v)} for i, v in enumerate(vals)]}


def test_twelvedata_batches_retries_and_cap():
    session = FakeSession([
        {"code": 429, "status": "error"},
        {"AAA": series(1, 2), "BBB": {"status": "error", "message": "not found"}},
        series(5),
    ])
    sleeps = []
    td = TwelveData(session, "k", TD_CFG, sleep=sleeps.append)
    data, errors = td.histories(["AAA", "BBB", "CCC", "DDD"])
    assert data["AAA"][-1] == ("2026-01-02", 2.0)
    assert errors["BBB"] == "not found"
    # cap of 3 credits: AAA+BBB (2) then CCC alone would make 4 with DDD -> chunk [CCC, DDD] skipped
    assert "DDD" in errors and errors["DDD"] == "daily credit cap reached"
    assert 65 in sleeps


def test_intl_rows_parse_feed():
    feed = [
        ["Market", "Ticker", "Google symbol", "Name", "PE", "Market cap", "Currency", "Close date", "Close",
         "Chg 5 sessions %", "Chg 1 month %", "Below 100-day high %"],
        ["Canada", "RY", "TSE:RY", "Royal Bank", "15.2", "4.1E+11", "CAD", "2026-11-09", "288.32", "0.73", "6.4", "5.7"],
        ["India", "XYZ", "NSE:XYZ", "", "", "", "", "", "", "", "", ""],
    ]
    rows = run.intl_rows(feed)
    assert len(rows) == 1
    assert rows[0]["close"] == 288.32 and rows[0]["market_cap"] == 4.1e11


# ---- Alpha Vantage -------------------------------------------------------
from scanner import alphavantage as avmod


def test_av_symbols():
    assert avmod.av_symbol("Canada", "TECK.B") == "TECK-B.TRT"
    assert avmod.av_symbol("India", "RELIANCE") == "RELIANCE.BSE"
    assert avmod.av_symbol("USA", "AAPL") is None


def test_av_targets_gaps_first_then_oldest():
    uni = [("Canada", "A"), ("Canada", "B"), ("India", "C"), ("India", "D")]
    cache = {("Canada", "A"): {"scanned_on": "2026-11-01"}, ("Canada", "B"): {"scanned_on": "2026-11-05"},
             ("India", "D"): {"scanned_on": "2026-11-09"}}
    picked = avmod.pick_targets(uni, {("India", "D")}, cache, 3)
    assert picked == [("India", "D"), ("India", "C"), ("Canada", "A")]


class AVSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)

    def get(self, url, params=None, timeout=None):
        return FakeResp(self.payloads.pop(0))


def av_series(n):
    return {"Time Series (Daily)": {f"2026-10-{i + 1:02d}": {"4. close": str(100 + i)} for i in range(n)}}


def test_av_refresh_stops_at_limit():
    session = AVSession([av_series(25), {"Information": "Invalid API call"}, {"Note": "25 requests per day"}, av_series(5)])
    client = avmod.AlphaVantage(session, "k", {"daily_calls": 25, "seconds_between_calls": 0}, sleep=lambda s: None)
    cache = {}
    targets = [("Canada", "RY"), ("India", "BAD"), ("India", "TCS"), ("India", "NEVER")]
    ok, errors = client.refresh(targets, cache, date(2026, 11, 10))
    assert ok == 1 and cache[("Canada", "RY")]["close"] == 124
    assert errors[("India", "TCS")] == "daily limit reached"
    assert ("India", "NEVER") not in errors and client.calls == 3


def test_merge_av_fills_gaps_and_flags_mismatch():
    feed = [{"market": "Canada", "ticker": "RY", "close": 100.0, "close_date": "2026-11-09", "price_source": "GF"},
            {"market": "India", "ticker": "TCS", "close": 50.0, "close_date": "2026-11-09", "price_source": "GF"}]
    cache = {
        ("Canada", "RY"): {"close": "100.5", "close_date": "2026-11-09", "scanned_on": "2026-11-10"},
        ("India", "TCS"): {"close": "60", "close_date": "2026-11-09", "scanned_on": "2026-11-10"},
        ("India", "NEW"): {"close": "10", "close_date": "2026-11-09", "chg_1m": "5", "scanned_on": "2026-11-08"},
        ("India", "OLD"): {"close": "10", "close_date": "2026-10-01", "scanned_on": "2026-10-01"},
    }
    uni = [("Canada", "RY"), ("India", "TCS"), ("India", "NEW"), ("India", "OLD")]
    notes = []
    rows, info = run.merge_av(feed, uni, cache, date(2026, 11, 10), 14, notes)
    assert info == {"filled_gaps": 1, "mismatches": 1}
    assert [r["ticker"] for r in rows] == ["RY", "TCS", "NEW"]
    assert "checked by Alpha Vantage" in rows[0]["price_source"]
    assert "TCS" in notes[0]


class _FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class _FakeSession:
    def __init__(self, payload):
        self.payload = payload

    def get(self, url, **kwargs):
        return _FakeResp(self.payload)


def test_largest_us_sorted_and_filtered():
    rows = [
        {"symbol": "SMALL", "name": "Small Co", "lastsale": "$10", "marketCap": "1,000"},
        {"symbol": "BRK/B", "name": "Berkshire Hathaway", "lastsale": "$480", "marketCap": "1,000,000"},
        {"symbol": "PENNY", "name": "Penny Inc", "lastsale": "$1.20", "marketCap": "5,000"},
        {"symbol": "ABCW", "name": "ABC Warrant", "lastsale": "$5", "marketCap": "9,000"},
        {"symbol": "XYZ^A", "name": "XYZ Pref", "lastsale": "$25", "marketCap": "9,000"},
        {"symbol": "MID", "name": "Mid Co", "lastsale": "$40", "marketCap": "50,000"},
    ]
    got = universe.fetch_largest_us(_FakeSession({"data": {"rows": rows}}))
    assert got == ["BRK.B", "MID", "SMALL"]


def test_universe_fills_to_size_with_largest(monkeypatch):
    monkeypatch.setattr(universe, "read_tickers", lambda path: ["CORE1"])
    monkeypatch.setattr(universe, "fetch_index_members",
                        lambda s, url: ["SP1", "CORE1"] if "500" in url else ["SP2"])
    monkeypatch.setattr(universe, "fetch_largest_us", lambda s: ["SP1", "BIG1", "BIG2", "BIG3"])
    assert universe.build_universe(None, 6) == ["CORE1", "SP1", "SP2", "BIG1", "BIG2", "BIG3"]
