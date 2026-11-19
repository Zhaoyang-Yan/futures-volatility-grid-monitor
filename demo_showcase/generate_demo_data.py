"""Generate synthetic futures data for the public portfolio showcase.

This script is intentionally independent from the production application. It
creates fictional market observations only; no credentials, endpoints,
accounts, positions, or local database files are read.
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data.json"
JS_OUTPUT = ROOT / "data.js"
SEED = 20261116


def build_series(start: float, drift: float, volatility: float, points: int = 120) -> list[dict[str, float | str]]:
    rng = random.Random(SEED + int(start))
    price = start
    rows = []
    anchor = datetime(2026, 11, 16, 8, 50, tzinfo=timezone.utc)
    for index in range(points):
        shock = rng.gauss(drift, volatility)
        price = max(price + shock, start * 0.72)
        rows.append({
            "time": (anchor + timedelta(minutes=index * 5)).strftime("%H:%M"),
            "price": round(price, 2),
        })
    return rows


def build_volatility_cone(spot: float, base_vol: float) -> list[dict[str, float | str]]:
    horizons = ["1W", "2W", "1M", "2M", "3M", "6M"]
    values = []
    for index, horizon in enumerate(horizons):
        centre = base_vol + math.sin(index * 0.75) * 1.8
        values.append({
            "horizon": horizon,
            "p10": round(max(centre - 5.2, 1.0), 2),
            "p25": round(max(centre - 2.4, 1.0), 2),
            "median": round(centre, 2),
            "p75": round(centre + 2.8, 2),
            "p90": round(centre + 5.6, 2),
            "current": round(centre + (index % 2 - 0.5) * 1.4, 2),
        })
    return values


def main() -> None:
    instruments = [
        {
            "symbol": "GC-SIM",
            "display_name": "Gold Futures",
            "exchange": "Synthetic COMEX",
            "last": 2678.40,
            "change": 0.74,
            "grid_step": 8.0,
            "grid_state": "Inside upper band",
            "series": build_series(2640.0, 0.45, 4.2),
            "volatility": build_volatility_cone(2678.4, 19.4),
        },
        {
            "symbol": "CL-SIM",
            "display_name": "Crude Oil Futures",
            "exchange": "Synthetic NYMEX",
            "last": 68.72,
            "change": -1.16,
            "grid_step": 0.65,
            "grid_state": "Lower band watch",
            "series": build_series(71.2, -0.018, 0.17),
            "volatility": build_volatility_cone(68.72, 31.6),
        },
        {
            "symbol": "HG-SIM",
            "display_name": "Copper Futures",
            "exchange": "Synthetic COMEX",
            "last": 4.91,
            "change": 0.38,
            "grid_step": 0.045,
            "grid_state": "Grid aligned",
            "series": build_series(4.72, 0.002, 0.018),
            "volatility": build_volatility_cone(4.91, 24.8),
        },
    ]
    payload = {
        "generated_at": "2026-11-16T09:30:00Z",
        "environment": "Synthetic portfolio showcase",
        "instruments": instruments,
        "alerts": [
            {"time": "09:24:15", "symbol": "GC-SIM", "direction": "UP", "move": "+1 grid", "status": "Simulated"},
            {"time": "09:17:40", "symbol": "CL-SIM", "direction": "DOWN", "move": "-2 grids", "status": "Simulated"},
            {"time": "09:05:20", "symbol": "HG-SIM", "direction": "UP", "move": "+1 grid", "status": "Simulated"},
        ],
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    JS_OUTPUT.write_text("window.SHOWCASE_DATA = " + json.dumps(payload, indent=2) + ";\n", encoding="utf-8")
    print(f"Generated synthetic showcase data: {JS_OUTPUT}")


if __name__ == "__main__":
    main()
