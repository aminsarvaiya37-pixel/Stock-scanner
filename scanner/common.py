"""Shared paths, config loading and small helpers."""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS_DATA = ROOT / "docs" / "data"
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = DATA / "state.json"

USER_AGENT = "Stock-scanner/1.0 (+https://github.com/aminsarvaiya37-pixel/Stock-scanner)"


def load_config(path: Path = CONFIG_PATH) -> dict:
    return json.loads(path.read_text())


def load_state(path: Path = STATE_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def to_float(value) -> float | None:
    """Parse a number from a CSV cell ("1,234.5", "12%", "" -> None)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if (isinstance(value, float) and math.isnan(value)) else float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text.startswith("#"):
        return None
    try:
        out = float(text)
    except ValueError:
        return None
    return None if math.isnan(out) else out


def clean(value):
    """Make a value JSON-safe (NaN -> None, round floats)."""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 4)
    return value
