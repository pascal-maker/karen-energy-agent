"""Simple, honest forecast of monthly Belgian day-ahead prices.

Model: seasonal profile (median month-of-year ratio over the last three
years) x exponentially smoothed deseasonalised level. Deliberately simple:
it shows the direction and the uncertainty, not a trading signal.

The uncertainty band is not assumed; it comes from a rolling backtest of the
same method on past data (10th/90th percentile of log errors per horizon),
and the backtest accuracy is published next to the forecast.
"""

from __future__ import annotations

import numpy as np

from . import MarketDataError

HORIZON = 12
ALPHA = 0.3
SEASON_YEARS = 3
MIN_TRAIN = 36
FLOOR = 1.0  # EUR/MWh; keeps ratios defined if a month ever averages <= 0


def _shift_month(month: str, offset: int) -> str:
    year, mon = map(int, month.split("-"))
    index = year * 12 + (mon - 1) + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _seasonal_index(values: np.ndarray, months: list[int]) -> np.ndarray:
    """Month-of-year multipliers (12,), normalised to mean 1."""
    window = min(len(values), SEASON_YEARS * 12)
    recent, recent_months = values[-window:], months[-window:]
    # Ratio to a centred-ish yearly mean: trailing 12-month mean of the window.
    trend = np.array([recent[max(0, i - 11): i + 1].mean() for i in range(len(recent))])
    ratios: list[list[float]] = [[] for _ in range(12)]
    for value, base, month in zip(recent, trend, recent_months):
        ratios[month].append(value / base)
    index = np.array([np.median(r) if r else 1.0 for r in ratios])
    return index / index.mean()


def _fit_predict(values: np.ndarray, months: list[int], horizon: int) -> np.ndarray:
    season = _seasonal_index(values, months)
    level = values[0] / season[months[0]]
    for value, month in zip(values[1:], months[1:]):
        level = ALPHA * (value / season[month]) + (1 - ALPHA) * level
    return np.array([level * season[(months[-1] + h) % 12] for h in range(1, horizon + 1)])


def forecast(history: dict[str, float], horizon: int = HORIZON) -> dict:
    """history: {"YYYY-MM": EUR/MWh} of complete months, oldest first."""
    labels = sorted(history)
    if len(labels) < MIN_TRAIN + horizon:
        raise MarketDataError(f"Need at least {MIN_TRAIN + horizon} complete months for a backtested forecast")
    for previous, current in zip(labels, labels[1:]):
        if _shift_month(previous, 1) != current:
            raise MarketDataError(f"History has a gap between {previous} and {current}")
    values = np.maximum(np.array([history[m] for m in labels], dtype=float), FLOOR)
    months = [int(m[5:7]) - 1 for m in labels]

    # Rolling-origin backtest: train on everything up to t, compare h months later.
    log_errors: list[list[float]] = [[] for _ in range(horizon)]
    abs_pct: list[list[float]] = [[] for _ in range(horizon)]
    for end in range(MIN_TRAIN, len(values)):
        predicted = _fit_predict(values[:end], months[:end], horizon)
        for h in range(min(horizon, len(values) - end)):
            actual = values[end + h]
            log_errors[h].append(float(np.log(actual / predicted[h])))
            abs_pct[h].append(float(abs(actual - predicted[h]) / actual))

    point = _fit_predict(values, months, horizon)
    rows = []
    for h in range(horizon):
        # Centre on the median backtest error so a systematic bias (e.g. the
        # smoothed level lagging a trend) is corrected, not hidden in the band.
        low_q, mid_q, high_q = np.percentile(log_errors[h], [10, 50, 90])
        rows.append({
            "month": _shift_month(labels[-1], h + 1),
            "point": round(float(point[h] * np.exp(mid_q)), 2),
            "low": round(float(point[h] * np.exp(low_q)), 2),
            "high": round(float(point[h] * np.exp(high_q)), 2),
        })
    return {
        "method": "Seizoensprofiel (laatste 3 jaar) x exponentieel afgevlakt niveau",
        "band": "10-90% van de fouten in een backtest op eerdere jaren; middenlijn gecorrigeerd voor de mediane fout",
        "trained_until": labels[-1],
        "backtest": {
            "origins": len(log_errors[0]),
            "mape_1m": round(100 * float(np.mean(abs_pct[0])), 1),
            "mape_6m": round(100 * float(np.mean(abs_pct[min(5, horizon - 1)])), 1),
            "mape_12m": round(100 * float(np.mean(abs_pct[horizon - 1])), 1),
        },
        "months": rows,
    }
