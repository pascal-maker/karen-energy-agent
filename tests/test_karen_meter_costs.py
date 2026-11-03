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


def test_a_free_claim_is_fine_when_a_source_says_so_for_the_same_act():
    assert not gemma_answer._unsupported_free_claim(
        "Een digitale gasmeter tijdelijk uit dienst stellen is gratis.", [], [TARIFFS])
    gratis = {**FLUVIUS_CLOSE, "content": "Het sluiten van je meter is gratis."}
    # A short answer is about the act the question names.
    assert not gemma_answer._unsupported_free_claim("Het kost niets.", [], [gratis], "Wat kost het sluiten van mijn meter?")


def test_free_for_another_act_or_condition_does_not_count():
    source = {**FLUVIUS_CLOSE, "content": "Definitief afsluiten kost €105,52; tijdelijk afsluiten is gratis."}
    assert gemma_answer._unsupported_free_claim("Definitief afsluiten is gratis.", [], [source])
    # Dropping the condition is just as wrong: only temporary closing is free.
    assert gemma_answer._unsupported_free_claim("Afsluiten is gratis.", [], [source])
    assert not gemma_answer._unsupported_free_claim("Tijdelijk afsluiten is gratis.", [], [source])
    # The tariff list: only a digital meter is free to close temporarily.
    assert gemma_answer._unsupported_free_claim("Tijdelijk sluiten is gratis.", [], [TARIFFS])
    assert gemma_answer._unsupported_free_claim("Een analoge gasmeter tijdelijk uit dienst stellen is gratis.", [], [TARIFFS])
    # A free row in a tariff table does not cover the row after it.
    table = {**TARIFFS, "content": "Uit dienst stellen (digitale elektriciteitsmeter) -€ -€ L Afsluiten elektriciteit "
                                   "bovengronds indien geen toegang tot elektriciteitsmeter 204,60€ 247,57€"}
    assert gemma_answer._unsupported_free_claim("Het kost niets.", [], [table], "Wat kost het om mijn meter te laten afsluiten?")
    gratis_opening = {**FLUVIUS_CLOSE, "content": "Een nieuwe meter openen is gratis."}
    assert gemma_answer._unsupported_free_claim("Je meter verwijderen is gratis.", [], [gratis_opening])


@pytest.mark.parametrize("answer", ["Closing the meter is free.", "It's free.", "Ça ne coûte rien."])
def test_plain_free_in_other_languages_counts_as_a_free_claim(answer):
    assert gemma_answer._unsupported_free_claim(answer, [], [FLUVIUS_CLOSE])
    assert gemma_answer._FREE_CLAIM.search(answer)


@pytest.mark.asyncio
async def test_a_general_answer_never_says_free(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "_generate", lambda *_args: "Closing the meter is free.")
    result = await gemma_answer._general_or_no_source("Is closing my meter free?", "Is closing my meter free?", "en", "checked")
    assert result["answer"] == gemma_answer._NO_SOURCE_REPLY["en"]


def test_a_tariff_table_far_down_a_long_page_is_still_read(monkeypatch):
    import asyncio

    long_page = "Nieuwe aansluiting 1.491,28€ 1.804,45€ A " * 400 + TARIFFS["content"]
    assert len(long_page) > 15000
    monkeypatch.setattr(browserbase_sources, "_fetch_full", lambda url: long_page)
    page = asyncio.run(browserbase_sources.fetch_energy_page("fluvius_gas_connection_tariffs", focus=r"In/Uit dienst stellen"))
    assert "105,52" in page["content"] and len(page["content"]) <= 10000


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
