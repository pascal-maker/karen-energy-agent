import pytest


@pytest.fixture(autouse=True)
def no_live_market_data(monkeypatch):
    """Tests never download market.json; a test that needs it patches load_market_data itself."""
    from luminus_harness import market_prices

    async def unavailable():
        return None

    monkeypatch.setattr(market_prices, "load_market_data", unavailable)


@pytest.fixture(autouse=True)
def no_live_jev(monkeypatch):
    """Jev routing stays off unless a test turns it on."""
    monkeypatch.delenv("KAREN_JEV_ROUTING", raising=False)


@pytest.fixture(autouse=True)
def empty_search_cache():
    """Each test starts without pages a previous test's mocked search left behind."""
    from luminus_harness import gemma_answer

    gemma_answer._SEARCH_CACHE.clear()
