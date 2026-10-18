"""Market pipeline tests. No network: Energy-Charts is replaced by a fake that
serves deterministic synthetic prices for the requested date range."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from market import MarketDataError, build, forecast, spot


def _synthetic_price(moment: datetime) -> float:
    # Seasonal (winter high) + daily (evening peak) + slow upward trend.
    season = 20 * math.cos(2 * math.pi * (moment.month - 1) / 12)
    daily = 15 * math.sin(2 * math.pi * (moment.hour - 12) / 24)
    trend = (moment.year - 2019) * 3
    # A 2022-style price crisis, so the backtest sees a regime shock.
    years = moment.year + (moment.month - 1) / 12
    crisis = 220 * math.exp(-(((years - 2022.6) / 0.45) ** 2))
    return round(70 + season + daily + trend + crisis, 2)


def fake_fetcher(minutes: int = 60, missing_days: set[date] = frozenset()):
    calls: list[str] = []

    def fetch(url: str) -> dict:
        calls.append(url)
        query = parse_qs(urlparse(url).query)
        start = date.fromisoformat(query["start"][0])
        end = date.fromisoformat(query["end"][0])
        seconds, prices = [], []
        moment = datetime.combine(start, datetime.min.time(), tzinfo=spot.BRUSSELS)
        stop = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=spot.BRUSSELS)
        while moment < stop:
            if moment.date() not in missing_days:
                seconds.append(int(moment.timestamp()))
                prices.append(_synthetic_price(moment))
            moment += timedelta(minutes=minutes)
        return {"unix_seconds": seconds, "price": prices, "unit": "EUR / MWh"}

    fetch.calls = calls
    return fetch


def test_fetch_rejects_unexpected_shape_and_unit() -> None:
    with pytest.raises(MarketDataError, match="shape"):
        spot.fetch_prices(date(2026, 1, 1), date(2026, 1, 1), fetcher=lambda _url: {"price": [1]})
    with pytest.raises(MarketDataError, match="unit"):
        spot.fetch_prices(
            date(2026, 1, 1), date(2026, 1, 1),
            fetcher=lambda _url: {"unix_seconds": [1], "price": [1.0], "unit": "EUR / kWh"},
        )


def test_fetch_skips_null_intervals_instead_of_inventing_them() -> None:
    points = spot.fetch_prices(
        date(2026, 1, 1), date(2026, 1, 1),
        fetcher=lambda _url: {"unix_seconds": [0, 3600, 7200], "price": [10.0, None, 30.0], "unit": "EUR / MWh"},
    )
    assert points == [(0, 10.0), (7200, 30.0)]


def test_day_view_reports_local_day_stats_and_cheapest_hours() -> None:
    day = date(2026, 10, 18)
    points = spot.fetch_prices(day, day, fetcher=fake_fetcher(minutes=15))
    view = spot.day_view(points, day)
    assert view["date"] == "2026-10-18"
    assert view["resolution_minutes"] == 15
    assert len(view["points"]) == 96 and view["points"][0][0] == "00:00"
    values = [p for _, p in view["points"]]
    assert view["min"] == min(values) and view["max"] == max(values)
    # The synthetic daily curve bottoms out around 06:00.
    assert view["cheapest_hours"] == [5, 6, 7]
    assert spot.day_view(points, day + timedelta(days=1)) is None


def test_monthly_average_is_time_weighted_across_resolution_change() -> None:
    # One hourly point at 100 and four quarter-hours at 0 cover equal time.
    base = int(datetime(2025, 10, 1, tzinfo=spot.BRUSSELS).timestamp())
    points = [(base, 100.0)] + [(base + 3600 + i * 900, 0.0) for i in range(4)]
    assert spot.monthly_averages(points) == {"2025-10": 50.0}


def test_forecast_requires_enough_contiguous_history() -> None:
    with pytest.raises(MarketDataError, match="at least"):
        forecast.forecast({f"2025-{m:02d}": 80.0 for m in range(1, 13)})
    history = {f"{y}-{m:02d}": 80.0 for y in range(2019, 2025) for m in range(1, 13)}
    del history["2021-05"]
    with pytest.raises(MarketDataError, match="gap"):
        forecast.forecast(history)


def test_forecast_follows_seasonality_with_a_backtested_band() -> None:
    history = {}
    for year in range(2019, 2026):
        for month in range(1, 13):
            history[f"{year}-{month:02d}"] = round(
                80 + 25 * math.cos(2 * math.pi * (month - 1) / 12) + (year - 2019) * 2, 2
            )
    result = forecast.forecast(history)
    rows = result["months"]
    assert [r["month"] for r in rows][:2] == ["2026-01", "2026-02"]
    assert len(rows) == 12
    assert all(r["low"] <= r["point"] <= r["high"] for r in rows)
    by_month = {r["month"]: r["point"] for r in rows}
    assert by_month["2026-01"] > by_month["2026-07"]  # winter above summer
    assert result["backtest"]["origins"] == len(history) - forecast.MIN_TRAIN
    assert result["backtest"]["mape_1m"] < 10  # a clean seasonal series is easy
    assert forecast.forecast(history) == result  # deterministic


def test_build_assembles_all_sections_and_reuses_complete_months() -> None:
    today = date(2026, 10, 18)
    fetch = fake_fetcher()
    first = build.build(today=today, previous={}, fetcher=fetch, now="2026-10-18T12:20:00+00:00")
    assert first["errors"] == []
    assert first["spot"]["today"]["date"] == "2026-10-18"
    assert first["spot"]["tomorrow"]["date"] == "2026-10-19"
    months = first["history"]["months"]
    assert next(iter(months)) == "2019-01" and "2026-09" in months and "2026-10" not in months
    assert first["history"]["current_month"]["month"] == "2026-10"
    assert first["forecast"]["months"][0]["month"] == "2026-10"
    assert first["sources"][0]["licence"] == "CC BY 4.0"

    refetch = fake_fetcher()
    second = build.build(today=today, previous=first, fetcher=refetch)
    history_calls = [u for u in refetch.calls if "start=2026-09-01" in u]
    assert history_calls and not any("start=2019" in u for u in refetch.calls)
    assert second["history"]["months"] == months


def test_build_keeps_previous_section_marked_stale_when_source_fails() -> None:
    today = date(2026, 10, 18)
    good = build.build(today=today, previous={}, fetcher=fake_fetcher())

    def broken(_url: str) -> dict:
        raise OSError("connection reset")

    result = build.build(today=today + timedelta(days=1), previous=good, fetcher=broken)
    assert result["spot"]["stale"] is True
    assert result["spot"]["today"]["date"] == "2026-10-18"  # old data, clearly dated
    assert result["history"]["stale"] is True
    assert any("Energy-Charts request failed" in e for e in result["errors"])


def test_build_without_previous_data_leaves_failed_sections_empty() -> None:
    result = build.build(today=date(2026, 10, 18), previous={}, fetcher=fake_fetcher(missing_days={date(2026, 10, 18)}))
    assert result["spot"] is None
    assert any("No day-ahead prices published" in e for e in result["errors"])
