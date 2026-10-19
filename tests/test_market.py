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


def _seasonal_history(noise: float = 0.0) -> dict[str, float]:
    import random
    rng = random.Random(7)
    history = {}
    for year in range(2019, 2026):
        for month in range(1, 13):
            history[f"{year}-{month:02d}"] = round(
                (80 + 25 * math.cos(2 * math.pi * (month - 1) / 12)) * math.exp(rng.gauss(0, noise)), 2
            )
    return history


def test_forecast_uses_seasonality_when_the_backtest_supports_it() -> None:
    result = forecast.forecast(_seasonal_history())
    rows = result["months"]
    assert [r["month"] for r in rows][:2] == ["2026-01", "2026-02"]
    assert len(rows) == 12
    assert result["season_weight"] > 0
    by_month = {r["month"]: r["point"] for r in rows}
    assert by_month["2026-01"] > by_month["2026-07"]  # winter above summer
    assert result["backtest"]["origins"] == forecast.RECENT_ORIGINS
    assert result["backtest"]["mape_1m"] < result["backtest"]["naive_mape_1m"]
    assert forecast.forecast(_seasonal_history()) == result  # deterministic


def test_forecast_falls_back_to_current_level_for_a_random_walk() -> None:
    # Non-stationary data with no seasonality: no model beats "same as now",
    # so the backtest must pick the naive level and a flat midline.
    import random
    rng = random.Random(3)
    level, history = math.log(80), {}
    for year in range(2019, 2026):
        for month in range(1, 13):
            level += rng.gauss(0, 0.2)
            history[f"{year}-{month:02d}"] = round(math.exp(level), 2)
    result = forecast.forecast(history)
    assert result["season_weight"] == 0.0 and result["alpha"] >= 0.8
    points = [r["point"] for r in result["months"]]
    last = history["2025-12"]
    assert max(points) / min(points) < 1.02
    assert abs(points[0] / last - 1) < 0.1


def test_forecast_band_contains_midline_and_never_narrows() -> None:
    rows = forecast.forecast(_seasonal_history(noise=0.15))["months"]
    for previous, row in zip(rows, rows[1:]):
        assert row["low"] <= row["point"] <= row["high"]
        # Ratios are compared at cent precision (values are rounded to 2 decimals).
        assert row["high"] / row["point"] >= previous["high"] / previous["point"] - 1e-3
        assert row["low"] / row["point"] <= previous["low"] / previous["point"] + 1e-3


def test_forecast_starts_from_the_running_month() -> None:
    history = {f"{y}-{m:02d}": 80.0 for y in range(2019, 2026) for m in range(1, 13)}
    flat = forecast.forecast(history)["months"][0]["point"]
    early = forecast.forecast(history, current=160.0, current_fraction=0.1)
    late = forecast.forecast(history, current=160.0, current_fraction=0.9)
    assert early["current_month_used"] and late["current_month_used"]
    assert flat < early["months"][0]["point"] < late["months"][0]["point"] < 160.0

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


def test_get_json_backs_off_on_rate_limit_then_succeeds(monkeypatch) -> None:
    import io
    from urllib.error import HTTPError

    attempts, sleeps = [], []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) < 3:
            headers = {"Retry-After": "7"} if len(attempts) == 2 else {}
            raise HTTPError(request.full_url, 429, "Too Many Requests", headers, None)
        return Response(b'{"ok": true}')

    monkeypatch.setattr(spot, "urlopen", fake_urlopen)
    monkeypatch.setattr(spot, "_last_request", 0.0)
    assert spot._get_json("https://api.example/price", sleep=sleeps.append) == {"ok": True}
    assert len(attempts) == 3
    assert spot.RETRY_DELAYS[0] in sleeps and 7.0 in sleeps


def test_get_json_does_not_retry_other_http_errors(monkeypatch) -> None:
    from urllib.error import HTTPError

    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 500, "Server Error", {}, None)

    monkeypatch.setattr(spot, "urlopen", fake_urlopen)
    with pytest.raises(HTTPError):
        spot._get_json("https://api.example/price", sleep=lambda _s: None)


def test_build_recovers_after_a_run_that_stored_empty_sections() -> None:
    # The first live run hit a rate limit and saved "history": null; the next
    # run must rebuild history from scratch instead of crashing on it.
    previous = {"spot": None, "history": None, "forecast": None, "contracts": None}
    result = build.build(today=date(2026, 10, 18), previous=previous, fetcher=fake_fetcher())
    assert result["errors"] == []
    assert next(iter(result["history"]["months"])) == "2019-01"
    assert result["forecast"]["months"]


def test_build_publishes_contracts_from_the_reviewed_file(tmp_path) -> None:
    import json as _json
    source = tmp_path / "tariffs.json"
    source.write_text(_json.dumps({"as_of": "2026-10-19", "suppliers": [{"name": "Luminus", "price_per_kwh_eur": 0.19}]}))
    result = build.build(today=date(2026, 10, 18), previous={"contracts": {"as_of": "old"}},
                         fetcher=fake_fetcher(), contracts_file=source)
    assert result["contracts"]["as_of"] == "2026-10-19"

    source.write_text("{not json")
    kept = build.build(today=date(2026, 10, 18), previous={"contracts": {"as_of": "old"}},
                       fetcher=fake_fetcher(), contracts_file=source)
    assert kept["contracts"] == {"as_of": "old"}
    assert any(e.startswith("contracts:") for e in kept["errors"])


def test_repository_tariff_file_is_valid() -> None:
    import json as _json
    data = _json.loads(build.CONTRACTS_FILE.read_text(encoding="utf-8"))
    priced = [s for s in data["suppliers"] if s["status"] == "verified" and s["price_per_kwh_eur"]]
    assert priced, "at least one verified supplier price"
    for supplier in priced:
        assert 0.05 < supplier["price_per_kwh_eur"] < 0.6, supplier["name"]
        assert supplier["source_url"].startswith("https://"), supplier["name"]
    for supplier in data["suppliers"]:
        assert supplier.get("contract_type") in ("variable", "fixed"), supplier["name"]
    fixed = [s for s in priced if s["contract_type"] == "fixed"]
    assert all(s["duration_months"] for s in fixed), "fixed cards say how long the price is fixed"
    assert len({(s["name"], s["contract_type"]) for s in priced}) == len(priced), "one card per supplier and type"
