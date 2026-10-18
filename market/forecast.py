"""Simple, honest forecast of monthly Belgian day-ahead prices.

Belgian wholesale prices are non-stationary: on the monthly log price an ADF
test cannot reject a unit root and KPSS rejects stationarity, while monthly
log changes are stationary (I(1)), and their volatility shifts between
regimes (2019-20, the 2021-23 crisis, after). The model follows from that:

- Work in logs (prices move multiplicatively; variance scales with level).
- Local level + seasonality: exponential smoothing of the deseasonalised log
  price. For I(1) data the latest level is the best guess for the future, so
  the forecast does not revert to a long-run mean or an old regime.
- The smoothing weight and how much of the seasonal profile to apply are
  chosen on a recent rolling backtest, and the 10-90%
  band comes from the errors of that recent window only, so its width matches
  today's volatility rather than the crisis years. No bias correction: it
  would encode the drift of whichever regime dominated the backtest.
- The running month (average so far) updates the level in proportion to how
  much of the month has passed.
- The backtest also scores a naive "same as last month" forecast, published
  next to the model's error so readers can see whether it adds anything.
"""

from __future__ import annotations

import numpy as np

from . import MarketDataError

HORIZON = 12
SEASON_YEARS = 3
MIN_TRAIN = 36
RECENT_ORIGINS = 36  # backtest window for alpha selection, band and accuracy
ALPHAS = (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
# How much of the estimated seasonal profile to apply. In non-stationary
# data the profile partly absorbs past regime moves (e.g. the August 2022
# spike), so whether it helps is decided by the backtest, not assumed.
SEASON_WEIGHTS = (0.0, 0.25, 0.5, 0.75, 1.0)
SELECTION_HORIZONS = 3  # alpha is chosen on 1-3 month errors
FLOOR = 1.0  # EUR/MWh; keeps logs defined if a month ever averages <= 0


def _shift_month(month: str, offset: int) -> str:
    year, mon = map(int, month.split("-"))
    index = year * 12 + (mon - 1) + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _seasonal_index(logs: np.ndarray, months: list[int]) -> np.ndarray:
    """Additive month-of-year effects on the log price (12,), mean zero:
    median deviation from the trailing 12-month mean over the last 3 years."""
    window = min(len(logs), SEASON_YEARS * 12)
    recent, recent_months = logs[-window:], months[-window:]
    full = logs[: len(logs) - window]
    deviations: list[list[float]] = [[] for _ in range(12)]
    for i, (value, month) in enumerate(zip(recent, recent_months)):
        history = np.concatenate([full, recent[: i + 1]])[-12:]
        deviations[month].append(value - history.mean())
    index = np.array([np.median(d) if d else 0.0 for d in deviations])
    return index - index.mean()


def _level(logs: np.ndarray, months: list[int], season: np.ndarray, alpha: float) -> float:
    level = logs[0] - season[months[0]]
    for value, month in zip(logs[1:], months[1:]):
        level = alpha * (value - season[month]) + (1 - alpha) * level
    return level


def _predict(logs: np.ndarray, months: list[int], alpha: float, horizon: int, weight: float) -> np.ndarray:
    season = weight * _seasonal_index(logs, months)
    level = _level(logs, months, season, alpha)
    return np.array([level + season[(months[-1] + h) % 12] for h in range(1, horizon + 1)])


def _backtest(logs: np.ndarray, months: list[int], alpha: float, horizon: int, origins: range,
              weight: float) -> list[list[float]]:
    """Log errors (actual - predicted) per horizon over the given origins."""
    errors: list[list[float]] = [[] for _ in range(horizon)]
    for end in origins:
        predicted = _predict(logs[:end], months[:end], alpha, horizon, weight)
        for h in range(min(horizon, len(logs) - end)):
            errors[h].append(float(logs[end + h] - predicted[h]))
    return errors


def _mape(log_errors: list[float]) -> float:
    # |actual - predicted| / actual with actual = predicted * exp(e)
    return round(100 * float(np.mean([abs(1 - np.exp(-e)) for e in log_errors])), 1)


def forecast(history: dict[str, float], horizon: int = HORIZON, *, current: float | None = None,
             current_fraction: float = 0.0) -> dict:
    """history: {"YYYY-MM": EUR/MWh} of complete months, oldest first.
    current: average so far of the running month; current_fraction: share of
    that month already observed (0-1)."""
    labels = sorted(history)
    if len(labels) < MIN_TRAIN + horizon:
        raise MarketDataError(f"Need at least {MIN_TRAIN + horizon} complete months for a backtested forecast")
    for previous, nxt in zip(labels, labels[1:]):
        if _shift_month(previous, 1) != nxt:
            raise MarketDataError(f"History has a gap between {previous} and {nxt}")
    logs = np.log(np.maximum(np.array([history[m] for m in labels], dtype=float), FLOOR))
    months = [int(m[5:7]) - 1 for m in labels]

    # Recent rolling-origin backtest; an origin needs at least one later month.
    first = max(MIN_TRAIN, len(logs) - RECENT_ORIGINS)
    origins = range(first, len(logs))

    def score(params: tuple[float, float]) -> float:
        errors = _backtest(logs, months, params[0], SELECTION_HORIZONS, origins, params[1])
        return float(np.mean([abs(e) for h in errors for e in h]))

    # Ties go to the simpler model: less seasonality, then less smoothing.
    alpha, season_weight = min(((a, w) for a in ALPHAS for w in SEASON_WEIGHTS), key=lambda p: (round(score(p), 6), p[1], -p[0]))
    errors = _backtest(logs, months, alpha, horizon, origins, season_weight)
    naive = [float(logs[end] - logs[end - 1]) for end in origins]

    season = season_weight * _seasonal_index(logs, months)
    level = _level(logs, months, season, alpha)
    first_month = (months[-1] + 1) % 12
    weight = min(max(current_fraction, 0.0), 1.0) * alpha
    if current is not None and weight > 0:
        level = weight * (np.log(max(current, FLOOR)) - season[first_month]) + (1 - weight) * level

    rows = []
    widest_low, widest_high = 0.0, 0.0
    for h in range(horizon):
        point = level + season[(months[-1] + h + 1) % 12]
        # Later horizons have fewer backtest errors; fall back to the
        # nearest horizon that has enough to estimate a 10-90% range.
        sample = next(errors[k] for k in range(h, -1, -1) if len(errors[k]) >= 10)
        low_q, high_q = np.percentile(sample, [10, 90])
        # Uncertainty never shrinks further ahead; fewer long-horizon backtest
        # errors can otherwise make the 12-month band look narrower than 6.
        widest_low, widest_high = min(widest_low, low_q), max(widest_high, high_q)
        rows.append({
            "month": _shift_month(labels[-1], h + 1),
            "point": round(float(np.exp(point)), 2),
            "low": round(float(np.exp(point + widest_low)), 2),
            "high": round(float(np.exp(point + widest_high)), 2),
        })
    return {
        "method": "Lokaal niveau + seizoenspatroon op de log-prijs (exponentiële afvlakking)",
        "band": f"10-90% van de fouten in een backtest over de laatste {len(origins)} maanden",
        "trained_until": labels[-1],
        "current_month_used": current is not None and weight > 0,
        "alpha": alpha,
        "season_weight": season_weight,
        "backtest": {
            "origins": len(origins),
            "window": f"{labels[first]}..{labels[-1]}",
            "mape_1m": _mape(errors[0]),
            "mape_6m": _mape(errors[min(5, horizon - 1)]),
            "mape_12m": _mape(errors[horizon - 1]) if errors[horizon - 1] else None,
            "naive_mape_1m": _mape(naive),
        },
        "months": rows,
    }
