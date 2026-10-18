"""Assemble market.json for Karen's Prijzen tab.

    python -m market.build --out data/market.json [--previous data/market.json]

Each section is built independently. When a source fails, the previous
section is kept, marked stale with its original date, and the error is
recorded -- values are never filled in.
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import MarketDataError
from . import forecast as forecast_module
from . import spot

HISTORY_START = date(2019, 1, 1)
SCHEMA_VERSION = 1


def _load(path: Path | None) -> dict:
    if not path or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _section(name: str, build: Callable[[], dict], previous: dict, errors: list[str], now: str) -> dict | None:
    try:
        section = build()
    except MarketDataError as error:
        errors.append(f"{name}: {error}")
        old = previous.get(name)
        if not old:
            return None
        return {**old, "stale": True}
    return {**section, "stale": False, "updated_at": now}


def build(*, today: date, previous: dict, fetcher: spot.Fetcher = spot._get_json, now: str | None = None) -> dict:
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    errors: list[str] = []

    def build_spot() -> dict:
        points = spot.fetch_prices(today, today + timedelta(days=1), fetcher=fetcher)
        today_view = spot.day_view(points, today)
        if today_view is None:
            raise MarketDataError(f"No day-ahead prices published for {today.isoformat()}")
        return {"unit": "EUR/MWh", "today": today_view, "tomorrow": spot.day_view(points, today + timedelta(days=1))}

    def build_history() -> dict:
        # Complete months never change, so only refetch from last month onwards.
        # A failed earlier run stores the section as null, not as a missing key.
        known = dict((previous.get("history") or {}).get("months") or {})
        start = spot.previous_month_start(today) if known else HISTORY_START
        known = {m: v for m, v in known.items() if m < start.strftime("%Y-%m")}
        fresh = spot.fetch_history(start, today, fetcher=fetcher)
        months = spot.complete_months({**known, **fresh}, today)
        if not months:
            raise MarketDataError("No complete months of history")
        running = fresh.get(today.strftime("%Y-%m"))
        return {"unit": "EUR/MWh", "months": months, "current_month": {"month": today.strftime("%Y-%m"), "avg_so_far": running}}

    sections: dict[str, dict | None] = {}
    sections["spot"] = _section("spot", build_spot, previous, errors, now)
    sections["history"] = _section("history", build_history, previous, errors, now)
    history_section = sections["history"] or {}
    history_months = history_section.get("months", {})
    running = history_section.get("current_month") or {}
    # Only use the running month when it really is this month (not a stale copy).
    current = running.get("avg_so_far") if running.get("month") == today.strftime("%Y-%m") else None
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    sections["forecast"] = _section(
        "forecast",
        lambda: {"unit": "EUR/MWh", **forecast_module.forecast(
            history_months, current=current, current_fraction=today.day / days_in_month)},
        previous, errors, now,
    )
    # Phase 2 (monthly reviewed tariff cards) fills this; keep whatever was merged.
    sections["contracts"] = previous.get("contracts")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now,
        "market": "BE",
        **sections,
        "sources": [spot.SOURCE],
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args(argv)
    today = datetime.now(spot.BRUSSELS).date()
    result = build(today=today, previous=_load(args.previous or args.out))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for error in result["errors"]:
        print(f"warning: {error}", file=sys.stderr)
    # Fail the run only when nothing usable exists at all.
    return 1 if not any(result.get(k) for k in ("spot", "history")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
