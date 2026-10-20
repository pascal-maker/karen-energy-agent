import pytest

from luminus_harness import gemma_answer, market_prices


def test_search_keywords_drop_question_words():
    assert gemma_answer._search_keywords("Wie heeft recht op het sociaal tarief?") == "recht sociaal tarief"
    assert gemma_answer._search_keywords("Hoe vaak moet mijn gasketel onderhouden worden?") == "vaak gasketel onderhouden"


@pytest.mark.asyncio
async def test_public_search_retries_with_keywords_then_flanders(monkeypatch):
    calls = []

    async def search(query, scope, max_pages=2):
        calls.append((query, scope))
        return [{"title": "Vlaanderen", "url": "https://www.vlaanderen.be/x", "content": "tekst"}] if scope == "flanders_public" else []

    monkeypatch.setattr(gemma_answer, "search_energy_pages", search)
    pages = await gemma_answer._search_public_pages("Wie heeft recht op het sociaal tarief?", "official_energy")
    assert pages and calls == [
        ("Wie heeft recht op het sociaal tarief?", "official_energy"),
        ("recht sociaal tarief", "official_energy"),
        ("recht sociaal tarief", "flanders_public"),
    ]


@pytest.mark.asyncio
async def test_public_search_stops_at_the_first_hit_and_keeps_a_named_region(monkeypatch):
    calls = []

    async def search(query, scope, max_pages=2):
        calls.append((query, scope))
        return []

    monkeypatch.setattr(gemma_answer, "search_energy_pages", search)
    assert await gemma_answer._search_public_pages("sociaal tarief Brussel", "brussels_public") == []
    assert [scope for _, scope in calls] == ["brussels_public"], "No Flanders fallback when the user named a region"


@pytest.mark.parametrize("question", ["Wat is het prosumententarief?", "Wat is het injectietarief?", "Wie is mijn energieleverancier?"])
def test_tariff_words_count_as_energy(question):
    assert gemma_answer.mentions_energy(question)


def test_cheapest_fixed_gas_contract_is_a_gas_price_question():
    assert market_prices.is_gas_price_question("Wat is het goedkoopste vaste gascontract?")
