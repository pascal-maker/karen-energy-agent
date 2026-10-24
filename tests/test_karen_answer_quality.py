"""What the VRT expert benchmark taught Karen: answer first, news for current events, no peeking at the expert."""

import asyncio

import pytest

from luminus_harness import gemma_answer


@pytest.mark.parametrize("question", [
    "Worden propaangas en stookolie ook duurder?",
    "Zijn de hoge prijzen het gevolg van de accijnzen en taksen op energie?",
    "De prijzen zijn al zo hoog, maar de taksen gaan nog stijgen?",
    "Waar komt onze olie vandaan?",
    "Waar komt ons gas vandaan?",
])
def test_current_energy_events_search_the_news_first(question):
    assert gemma_answer._search_attempts(question, "official_energy")[0][1] == "news"


@pytest.mark.parametrize("question", ["Wat is het capaciteitstarief?", "Kan mijn leverancier een hoger voorschot verplichten?"])
def test_how_the_system_works_still_starts_at_the_regulators(question):
    assert gemma_answer._search_attempts(question, "official_energy")[0][1] != "news"


def test_prompts_ask_for_a_direct_answer_and_je():
    for instruction in (gemma_answer._SYSTEM_INSTRUCTION, gemma_answer._GENERAL_INSTRUCTION):
        assert "rechtstreeks beantwoordt" in instruction and 'nooit met "u"' in instruction
    assert "niet over wat precies gevraagd wordt" in gemma_answer._SYSTEM_INSTRUCTION


@pytest.mark.parametrize("question", [
    "Kan mijn energieleverancier een hoger voorschotbedrag verplichten?",
    "Worden propaangas en stookolie ook duurder?",
    "Zijn de hoge prijzen het gevolg van de accijnzen en taksen op energie?",
    "Can my supplier raise my advance payment?",
])
def test_yes_no_questions_ask_for_ja_or_nee(question):
    assert '"Ja" of "Nee"' in gemma_answer._form_hint(question)


@pytest.mark.parametrize("question", [
    "Waarom wordt de gasprijs aan de elektriciteitsprijs gekoppeld?",
    "Hoe komt het dat ook het sociaal tarief stijgt?",
    "Waar komt onze olie vandaan?",
    "Wat is de makkelijkste manier om prijzen te vergelijken?",
])
def test_open_questions_get_no_forced_ja(question):
    assert gemma_answer._form_hint(question) == ""


def test_blocked_pages_are_never_read(monkeypatch):
    async def pages(query, scope, max_pages=2):
        return [{"title": "VRT", "url": "https://www.vrt.be/vrtnws/nl/2026/10/23/vragen/", "content": "x"},
                {"title": "CREG", "url": "https://www.creg.be/nl", "content": "y"}]

    monkeypatch.setattr(gemma_answer, "search_energy_pages", pages)
    monkeypatch.setattr(gemma_answer, "_SEARCH_CACHE", {})

    async def search():
        reset = gemma_answer.blocked_urls.set(("https://www.vrt.be/vrtnws/nl/2026/10/23/vragen",))
        try:
            return await gemma_answer._search_scope("taksen", "news")
        finally:
            gemma_answer.blocked_urls.reset(reset)

    assert [page["title"] for page in asyncio.run(search())] == ["CREG"]
    assert len(asyncio.run(gemma_answer._search_scope("taksen", "news"))) == 2, "Visitors still read every page"
