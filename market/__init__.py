"""Belgian energy market data for Karen's Prijzen tab.

Run by GitHub Actions (this package needs internet access to Energy-Charts);
the output is a single market.json published on the `market-data` branch.
"""


class MarketDataError(RuntimeError):
    """A source returned unusable data. Callers keep the previous section and
    mark it stale; they never fill in values."""
