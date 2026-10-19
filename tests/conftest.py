import pytest


@pytest.fixture(autouse=True)
def no_live_market_data(monkeypatch):
    """Tests never download market.json; a test that needs it patches load_market_data itself."""
    from luminus_harness import market_prices

    async def unavailable():
        return None

    monkeypatch.setattr(market_prices, "load_market_data", unavailable)
