"""Read the 'AstroAlpha Scanner Feed' Google Sheet (GOOGLEFINANCE data) as CSV."""
from __future__ import annotations

import csv
from io import StringIO

from .common import USER_AGENT


class FeedUnavailable(Exception):
    pass


def tab_url(sheet_id: str, gid: int) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def fetch_rows(session, sheet_id: str, gid: int) -> list[list[str]]:
    resp = session.get(tab_url(sheet_id, gid), headers={"User-Agent": USER_AGENT}, timeout=60)
    ctype = resp.headers.get("Content-Type", "")
    if resp.status_code != 200 or "text/html" in ctype:
        raise FeedUnavailable(
            f"feed tab gid={gid} not readable (HTTP {resp.status_code}). "
            "Share the feed sheet as 'Anyone with the link: Viewer'."
        )
    return list(csv.reader(StringIO(resp.content.decode("utf-8"))))


def rows_to_dicts(rows: list[list[str]]) -> list[dict[str, str]]:
    if not rows:
        return []
    header = [h.strip() for h in rows[0]]
    out = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        out.append({h: (row[i].strip() if i < len(row) else "") for i, h in enumerate(header) if h})
    return out
