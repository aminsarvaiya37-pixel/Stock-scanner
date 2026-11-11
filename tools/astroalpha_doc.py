"""Turn docs/data/latest.json into the small 'scan/latest' document the AstroAlpha app reads.

Usage: python3 tools/astroalpha_doc.py docs/data/latest.json > doc.json
"""
import json
import sys

d = json.load(open(sys.argv[1]))
CCY = {"USA": "USD", "Canada": "CAD", "India": "INR"}


def pick(r, market):
    return {"t": r["ticker"], "name": (r.get("company") or "")[:40], "type": r["type"],
            "rank": r["rank"], "score": round(r["combined_score"], 1), "close": r.get("close"),
            "ccy": r.get("currency") or CCY.get(market, ""), "date": r.get("close_date"),
            "chg1m": r.get("chg_1m") and round(r["chg_1m"], 1),
            "chg5d": r.get("chg_5d") and round(r["chg_5d"], 1),
            "belowHigh": r.get("below_100d_high") and round(r["below_100d_high"], 1),
            "pe": r.get("pe") and round(r["pe"], 1), "src": (r.get("price_source") or "")[:90]}


markets = {}
for m, rows in d["markets"].items():
    blue = [r for r in rows if r["type"] == "Blue chip"][:4]
    small = [r for r in rows if r["type"] == "Small cap"][:2]
    markets[m] = {"ranked": len(rows), "picks": [pick(r, m) for r in blue + small]}

b = d.get("batch") or {}
doc = {
    "generated_at": d["generated_at"],
    "us_universe": d.get("us_universe"),
    "us_with_data": d.get("us_with_data"),
    "batch": f"{b.get('index')}/{b.get('of')}: {b.get('scanned', 0)} of {b.get('requested', 0)} US stocks refreshed",
    "notes": [n[:220] for n in d.get("notes", []) if not n.startswith("Universe source skipped")][:6],
    "verdict": d.get("performance_verdict", []),
    "markets": markets,
}
print(json.dumps(doc, separators=(",", ":")))
