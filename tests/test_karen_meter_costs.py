"""Meter costs: Karen reads Fluvius' tariff list and never calls something free that no source calls free.

Benchmark 2026-11-03: asked what closing a gas meter costs, Karen read only Fluvius' "Meter openen of
sluiten" page ("Je betaalt dan geen huurkosten meer voor de meter") and answered "Het kost niets";
the tariff list says EUR 105,52 incl. btw.
"""

import pytest

from luminus_harness import browserbase_sources, gemma_answer

FLUVIUS_CLOSE = {"title": "Meter openen of sluiten | Fluvius", "url": "https://www.fluvius.be/nl/aansluitingen/meter-openen-sluiten",
                 "content": "We sluiten de meter af. We laten de meter wel gewoon hangen. Je betaalt dan geen huurkosten meer voor de meter."}
TARIFFS = {"title": "Fluvius · aansluittarieven aardgas", "url": browserbase_sources.ENERGY_SOURCES["fluvius_gas_connection_tariffs"][1],
           "content": "In/Uit dienst stellen gas\nUit dienst stellen van analoge gasmeter of langdurige (> 6 mnd) uitdienststelling "
                      "van digitale gasmeter 87,21€ 105,52€ D\nTijdelijk uit dienst stellen (max. 6 mnd) digitale gasmeter -€ -€ D"}


@pytest.mark.parametrize("answer", [
    "Het kost niets: Fluvius rekent geen vaste sluitingskost.",
    "Dat is gratis in Vlaanderen.",
    "Nee, je betaalt niets voor het sluiten.",
    "Closing the meter is free of charge.",
])
def test_a_free_claim_without_a_source_saying_free_is_unsupported(answer):
    assert gemma_answer._unsupported_free_claim(answer, [], [FLUVIUS_CLOSE])


def test_a_free_claim_is_fine_when_a_source_says_so():
    assert not gemma_answer._unsupported_free_claim("Tijdelijk sluiten is gratis.", [], [TARIFFS])
    gratis = {**FLUVIUS_CLOSE, "content": "Het sluiten van je meter is gratis."}
    assert not gemma_answer._unsupported_free_claim("Het kost niets.", [], [gratis])


def test_an_answer_with_a_price_is_not_a_free_claim():
    assert not gemma_answer._unsupported_free_claim("Het kost 105,52 euro inclusief btw in Vlaanderen.", [], [FLUVIUS_CLOSE])


@pytest.mark.parametrize("question", [
    "Wat kost het om mijn gasmeter te laten afsluiten als ik overstap?",
    "Hoeveel betaal ik om mijn aardgasmeter uit dienst te laten stellen?",
    "Is het verwijderen van een gasmeter gratis?",
])
def test_gas_meter_cost_questions_read_the_gas_tariff_list(question):
    assert gemma_answer._meter_tariff_sources(question, "official_energy") == ["fluvius_gas_connection_tariffs"]


def test_other_questions_and_other_regions_read_no_tariff_list():
    assert gemma_answer._meter_tariff_sources("Hoe sluit ik mijn gasmeter af?", "official_energy") == []
    assert gemma_answer._meter_tariff_sources("Wat kost een nieuwe gasmeter in Brussel?", "brussels_public") == []
    assert gemma_answer._meter_tariff_sources("Wat kost het sluiten van mijn elektriciteitsmeter?", "official_energy") == [
        "fluvius_electricity_connection_tariffs"]


@pytest.mark.asyncio
async def test_closing_a_gas_meter_is_answered_from_the_tariff_list_not_as_free(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    fetched = []

    async def fetch(key, focus=None):
        fetched.append(key)
        assert focus
        return TARIFFS if key == "fluvius_gas_connection_tariffs" else None

    async def search(query, scope, max_pages=2):
        return [FLUVIUS_CLOSE] if scope == "grid_operators" else []

    monkeypatch.setattr(gemma_answer, "fetch_energy_page", fetch)
    monkeypatch.setattr(gemma_answer, "search_energy_pages", search)
    prompts = []

    def generate(prompt, *_args):
        prompts.append(prompt)
        return "Het kost 105,52 euro inclusief btw in Vlaanderen." if "105,52" in prompt else "Het kost niets."

    monkeypatch.setattr(gemma_answer, "_generate", generate)
    result = await gemma_answer.answer_energy_question("Wat kost het om mijn gasmeter te laten afsluiten als ik overstap?", [])
    assert fetched == ["fluvius_gas_connection_tariffs"]
    assert result["answer"] == "Het kost 105,52 euro inclusief btw in Vlaanderen."
    assert TARIFFS["url"] in [source["url"] for source in result["sources"]]


@pytest.mark.asyncio
async def test_an_invented_free_answer_makes_karen_search_on(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")

    async def no_page(key, focus=None):
        return None

    async def search(query, scope, max_pages=2):
        return [FLUVIUS_CLOSE] if scope == "grid_operators" else []

    monkeypatch.setattr(gemma_answer, "fetch_energy_page", no_page)
    monkeypatch.setattr(gemma_answer, "search_energy_pages", search)
    monkeypatch.setattr(gemma_answer, "_generate", lambda *_args: "Het kost niets: Fluvius rekent geen sluitingskost.")
    result = await gemma_answer.answer_energy_question("Wat kost het om mijn gasmeter te laten afsluiten als ik overstap?", [])
    assert "kost niets" not in result["answer"]
