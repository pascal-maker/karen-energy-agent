"""Belgian day-ahead electricity prices from Energy-Charts (Fraunhofer ISE).

API: https://api.energy-charts.info/price?bzn=BE&start=YYYY-MM-DD&end=YYYY-MM-DD
Response: {"unix_seconds": [...], "price": [...], "unit": "EUR / MWh", ...}
Licence: CC BY 4.0 -- the dashboard must attribute Energy-Charts.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from . import MarketDataError

BRUSSELS = ZoneInfo("Europe/Brussels")
API_URL = "https://api.energy-charts.info/price"
SOURCE = {
    "title": "Energy-Charts (Fraunhofer ISE) · day-ahead BE",
    "url": "https://www.energy-charts.info/charts/price_spot_market/chart.htm?l=nl&c=BE",
    "licence": "CC BY 4.0",
}

Point = tuple[int, float]
Fetcher = Callable[[str], dict]

# Energy-Charts rate-limits bursts (HTTP 429). Space requests out and back
# off on 429, honouring Retry-After when it is given.
REQUEST_GAP_SECONDS = 5.0
RETRY_DELAYS = (15.0, 30.0, 60.0, 120.0)
_last_request = 0.0


def _get_json(url: str, *, sleep: Callable[[float], None] = time.sleep) -> dict:
    global _last_request
    for attempt in range(len(RETRY_DELAYS) + 1):
        wait = _last_request + REQUEST_GAP_SECONDS - time.monotonic()
        if wait > 0:
            sleep(wait)
        _last_request = time.monotonic()
        request = Request(url, headers={"User-Agent": "karen-energy-agent market pipeline", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            if error.code != 429 or attempt == len(RETRY_DELAYS):
                raise
            retry_after = error.headers.get("Retry-After") if error.headers else None
            delay = float(retry_after) if retry_after and retry_after.isdigit() else RETRY_DELAYS[attempt]
            sleep(min(delay, 300.0))
    raise AssertionError("unreachable")


def fetch_prices(start: date, end: date, *, fetcher: Fetcher = _get_json) -> list[Point]:
    """Day-ahead prices (EUR/MWh) for [start, end], as (unix_seconds, price)."""
    url = f"{API_URL}?bzn=BE&start={start.isoformat()}&end={end.isoformat()}"
    try:
        payload = fetcher(url)
    except Exception as error:
        raise MarketDataError(f"Energy-Charts request failed: {error}") from error
    seconds = payload.get("unix_seconds") if isinstance(payload, dict) else None
    prices = payload.get("price") if isinstance(payload, dict) else None
    if not isinstance(seconds, list) or not isinstance(prices, list) or len(seconds) != len(prices):
        raise MarketDataError("Energy-Charts returned an unexpected response shape")
    unit = str(payload.get("unit", "EUR / MWh")).replace(" ", "").casefold()
    if unit != "eur/mwh":
        raise MarketDataError(f"Energy-Charts returned prices in an unexpected unit: {payload.get('unit')}")
    points: list[Point] = []
    for second, price in zip(seconds, prices):
        # Missing intervals come back as null; skip them rather than invent a value.
        if isinstance(second, (int, float)) and isinstance(price, (int, float)) and math.isfinite(price):
            points.append((int(second), float(price)))
    return points


def _local(second: int) -> datetime:
    return datetime.fromtimestamp(second, tz=BRUSSELS)


def _resolution_minutes(points: list[Point]) -> int:
    gaps = sorted(b[0] - a[0] for a, b in zip(points, points[1:]) if b[0] > a[0])
    return gaps[len(gaps) // 2] // 60 if gaps else 60


def day_view(points: list[Point], day: date) -> dict | None:
    """Prices for one local (Brussels) day, with summary stats and the three
    cheapest whole hours. None when the day has not been published."""
    selected = [(s, p) for s, p in points if _local(s).date() == day]
    if not selected:
        return None
    values = [p for _, p in selected]
    hourly: dict[int, list[float]] = defaultdict(list)
    for second, price in selected:
        hourly[_local(second).hour].append(price)
    hourly_avg = {hour: sum(v) / len(v) for hour, v in hourly.items()}
    cheapest = sorted(hourly_avg, key=lambda hour: (hourly_avg[hour], hour))[:3]
    return {
        "date": day.isoformat(),
        "resolution_minutes": _resolution_minutes(selected),
        "points": [[_local(s).strftime("%H:%M"), round(p, 2)] for s, p in selected],
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "avg": round(sum(values) / len(values), 2),
        "cheapest_hours": sorted(cheapest),
    }


def monthly_averages(points: list[Point]) -> dict[str, float]:
    """Time-weighted mean price per local month ("YYYY-MM": EUR/MWh).

    Since October 2025 the day-ahead market runs on 15-minute intervals, so a
    plain mean of mixed hourly/15-minute points would overweight recent days;
    each point is weighted by the interval it covers."""
    totals: dict[str, float] = defaultdict(float)
    minutes: dict[str, float] = defaultdict(float)
    for (second, price), nxt in zip(points, points[1:] + [None]):
        duration = (nxt[0] - second) / 60 if nxt and 0 < nxt[0] - second <= 3600 else _resolution_minutes(points)
        month = _local(second).strftime("%Y-%m")
        totals[month] += price * duration
        minutes[month] += duration
    return {month: round(totals[month] / minutes[month], 2) for month in sorted(totals)}


def fetch_history(first_month: date, today: date, *, fetcher: Fetcher = _get_json) -> dict[str, float]:
    """Monthly averages from first_month up to and including today's month,
    fetched one calendar year at a time to keep responses small."""
    averages: dict[str, float] = {}
    cursor = first_month.replace(day=1)
    while cursor <= today:
        chunk_end = min(date(cursor.year, 12, 31), today)
        averages.update(monthly_averages(fetch_prices(cursor, chunk_end, fetcher=fetcher)))
        cursor = date(cursor.year + 1, 1, 1)
    return averages


def complete_months(averages: dict[str, float], today: date) -> dict[str, float]:
    """Drop the running month: its average changes until the month ends."""
    current = today.strftime("%Y-%m")
    return {month: value for month, value in averages.items() if month < current}


def previous_month_start(today: date) -> date:
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)
