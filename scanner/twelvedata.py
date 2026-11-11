"""Twelve Data client that respects the free plan (8 credits/minute, 800/day)."""
from __future__ import annotations

import logging
import time
from typing import Callable

import requests

log = logging.getLogger(__name__)

API = "https://api.twelvedata.com/time_series"


class CreditCapReached(Exception):
    pass


class TwelveData:
    def __init__(self, session, api_key: str, cfg: dict, sleep: Callable[[float], None] = time.sleep):
        self.session = session
        self.api_key = api_key
        self.per_request = cfg["symbols_per_request"]
        self.pause = cfg["seconds_between_requests"]
        self.cap = cfg["daily_credit_cap"]
        self.outputsize = cfg["history_sessions"]
        self.sleep = sleep
        self.credits_used = 0

    def _request(self, symbols: list[str]) -> dict:
        params = {
            "symbol": ",".join(symbols),
            "interval": "1day",
            "outputsize": self.outputsize,
            "order": "ASC",
            "apikey": self.api_key,
        }
        for attempt in range(4):
            try:
                resp = self.session.get(API, params=params, timeout=60)
                payload = resp.json()
            except (requests.RequestException, ValueError) as exc:  # dropped connection or bad body
                log.info("Request failed (%s), retrying in 15s (attempt %d)", type(exc).__name__, attempt + 1)
                self.sleep(15)
                continue
            if isinstance(payload, dict) and payload.get("code") == 429:
                log.info("Rate limited, waiting 65s (attempt %d)", attempt + 1)
                self.sleep(65)
                continue
            if isinstance(payload, dict) and payload.get("status") == "error":
                raise RuntimeError(payload.get("message", "Twelve Data error"))
            return payload
        raise RuntimeError("Twelve Data kept rate limiting")

    @staticmethod
    def _split(symbols: list[str], payload: dict) -> dict[str, dict]:
        # One symbol -> the payload itself; several -> keyed by symbol.
        if len(symbols) == 1:
            return {symbols[0]: payload}
        return {s: payload.get(s, {"status": "error", "message": "missing"}) for s in symbols}

    def histories(self, symbols: list[str]) -> tuple[dict[str, list[tuple[str, float]]], dict[str, str]]:
        """Fetch daily closes. Returns ({symbol: [(date, close), ...]}, {symbol: error})."""
        out: dict[str, list[tuple[str, float]]] = {}
        errors: dict[str, str] = {}
        chunks = [symbols[i : i + self.per_request] for i in range(0, len(symbols), self.per_request)]
        for n, chunk in enumerate(chunks):
            if self.credits_used + len(chunk) > self.cap:
                for s in symbols[symbols.index(chunk[0]):]:
                    errors[s] = "daily credit cap reached"
                log.warning("Credit cap reached after %d credits", self.credits_used)
                break
            if n:
                self.sleep(self.pause)
            try:
                payload = self._request(chunk)
            except Exception as exc:
                for s in chunk:
                    errors[s] = str(exc)
                continue
            finally:
                self.credits_used += len(chunk)
            for sym, item in self._split(chunk, payload).items():
                if item.get("status") != "ok" or not item.get("values"):
                    errors[sym] = item.get("message", "no data")
                    continue
                rows = []
                for v in item["values"]:
                    try:
                        rows.append((v["datetime"][:10], float(v["close"])))
                    except (KeyError, TypeError, ValueError):
                        continue
                rows.sort()
                out[sym] = rows
            log.info("Batch %d/%d done (%d credits used)", n + 1, len(chunks), self.credits_used)
        return out, errors
