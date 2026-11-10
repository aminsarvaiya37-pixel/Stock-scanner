"""Astro scores, supplied in data/astro_scores.csv.

Columns: Market, Ticker, Astro score (0-100), Reason, Valid until (YYYY-MM-DD, optional).
The rules that produce these scores are yours; this module only reads them so the
scanner can blend them with the fundamental score and the 30-day test can judge them.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .common import DATA, to_float

ASTRO_PATH = DATA / "astro_scores.csv"


def load_astro(today: date, path: Path = ASTRO_PATH) -> dict[tuple[str, str], tuple[float, str]]:
    if not path.exists():
        return {}
    scores: dict[tuple[str, str], tuple[float, str]] = {}
    with path.open() as fh:
        for row in csv.DictReader(fh):
            market = (row.get("Market") or "").strip()
            ticker = (row.get("Ticker") or "").strip().upper()
            score = to_float(row.get("Astro score"))
            if not market or not ticker or score is None:
                continue
            until = (row.get("Valid until") or "").strip()
            if until:
                try:
                    if date.fromisoformat(until) < today:
                        continue
                except ValueError:
                    pass
            scores[(market, ticker)] = (max(0.0, min(100.0, score)), (row.get("Reason") or "").strip())
    return scores
