# AstroAlpha Stock Scanner

Every weekday this repository ranks about **1,000 stocks**: **800 US** stocks, plus your **Canada** and **India** lists. It publishes the results to a phone web app and to your Google Sheets.

```
                ┌──────────────────────── GitHub Actions (weekdays 22:35 UTC) ────────────────────────┐
Twelve Data ───►│ US batch: 1/3 of 800 tickers per day (≈267, 8 per minute, ≤780 credits) → us_cache │
                │                                                                                      │
Google Sheet ──►│ "AstroAlpha Scanner Feed" (GOOGLEFINANCE):                                           │
 (feed)         │   Intl tab  – Canada + India prices, 5-session / 1-month change, 100-day high, P/E   │
                │   US tab    – P/E, market cap, name for the 800 US tickers                           │
                │   Tracker   – your 30-Day Test picks + summary (IMPORTRANGE)                         │
                │                                                                                      │
astro_scores ──►│ score + rank per market → docs/data/latest.json, rankings.csv, data/history/*.csv   │
                └──────────────────────────────────────────────────────────────────────────────────────┘
                         │                                        │
                         ▼                                        ▼
          Phone app (GitHub Pages, docs/)          "AstroAlpha Rankings Log" → Rankings tab (IMPORTDATA)
```

## How the 800 US stocks are covered in 3 days

The free Twelve Data plan allows **8 credits a minute and 800 a day**. One daily price history costs 1 credit per symbol.

- The universe is built in this order: your 100 core US names (`data/core_us.csv`), then S&P 500 members, then S&P MidCap 400 members. The list is cut off at 800 and rebuilt every 30 days, at the start of a cycle.
- Each weekday run scans one interleaved third of the list, about 267 symbols, in roughly 35 minutes. After 3 weekdays every US stock has been refreshed. The next run starts a new cycle.
- Between scans each US stock keeps its last metrics (`data/us_cache.csv`), so the app always ranks all 800. Each row shows when it was last scanned.
- Canada and India come from GOOGLEFINANCE in the feed sheet. Twelve Data's free plan doesn't cover TSX or NSE, and Alpha Vantage's free plan allows only 25 calls a day.

## Scores

| Score | What it is |
|---|---|
| **Fundamental** (0–100) | A percentile rank within the market: 40% earnings yield (1/P/E; losses count as worst), 30% 1-month change, 10% 5-session change, 20% closeness to the 100-day high. The weights are in `config.json`. |
| **Astro** (0–100) | Read from `data/astro_scores.csv` (`Market,Ticker,Astro score,Reason,Valid until`). If a stock has no row, it gets a neutral 50. |
| **Combined** | `Fundamental weight %` × Fundamental + the rest × Astro. The default weight is 70%. If the astro file is empty, Combined equals Fundamental (100%). |

Type is **Blue chip** when market cap is at least $10B (US), C$10B (Canada) or ₹50,000 Cr (India). Otherwise it is **Small cap**.

The 30-Day Test is the honest check on the astro score: if astro picks don't beat both the random control picks and the index over enough cycles, cut the astro weight.

## One-time setup

1. **Twelve Data API key.** Copy your key from the twelvedata.com dashboard. In GitHub go to *Settings → Secrets and variables → Actions → New repository secret*, name it `TWELVE_DATA_API_KEY`, and paste the key.
2. **Share the feed sheet.** Open *AstroAlpha Scanner Feed* in Google Sheets and choose *Share → General access → Anyone with the link → Viewer*. The scanner reads it as CSV without logging in.
3. **Connect the feed to your sheets.** In the feed sheet, click cell **Intl!A2**, **TrackerPicks!A1** and **TrackerSummary!A1**, and press **Allow access** on each. This happens once per source sheet.
4. **Turn on GitHub Pages.** Go to *Settings → Pages → Build and deployment → Deploy from a branch*, choose the default branch and the `/docs` folder, then save. The app will be at `https://aminsarvaiya37-pixel.github.io/Stock-scanner/`.
5. **Run it.** Go to *Actions → Daily scan → Run workflow*. After that it runs by itself every weekday.
6. **Install on your phone.** Open the app URL. On iPhone use *Share → Add to Home Screen*; on Android use *⋮ → Install app*.

Note that the scheduled run only fires from the repository's **default branch**.

## Local use

```bash
pip install -r requirements.txt
python -m pytest -q
TWELVE_DATA_API_KEY=... python -m scanner.run          # one batch
python -m scanner.run --skip-twelvedata                 # re-rank without spending credits
```

This repository is public, so the app, the rankings and the copied tracker are publicly readable. Personal research only, not investment advice.
