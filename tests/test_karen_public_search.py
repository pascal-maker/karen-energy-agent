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


@pytest.mark.parametrize("question", [
    "Wat doet de VREG?", "Wat is een budgetmeter?", "Is een thuisbatterij interessant?",
    "Wat kost een laadpaal thuis?", "Hoe lees ik mijn digitale meter af?", "Wat is energiedelen?",
])
def test_grid_and_home_energy_words_count_as_energy(question):
    assert gemma_answer.mentions_energy(question)


@pytest.mark.parametrize("question", ["Hoeveel kilometers is Gent?", "Wie wint de Ronde van Vlaanderen?"])
def test_other_questions_stay_off_topic(question):
    assert not gemma_answer.mentions_energy(question)


def recording_search(calls, hit_scope):
    async def search(query, scope, max_pages=2):
        calls.append((query, scope))
        return [{"title": scope, "url": f"https://www.{scope}.test/x", "content": "tekst"}] if scope == hit_scope else []
    return search


@pytest.mark.asyncio
async def test_meter_questions_ask_the_grid_operators_first(monkeypatch):
    calls = []
    monkeypatch.setattr(gemma_answer, "search_energy_pages", recording_search(calls, "grid_operators"))
    pages = await gemma_answer._search_public_pages("Hoe lees ik mijn digitale meter af?", "official_energy")
    assert pages and calls == [("lees digitale meter", "grid_operators")]


@pytest.mark.asyncio
async def test_why_prices_rise_asks_the_news_first(monkeypatch):
    calls = []
    monkeypatch.setattr(gemma_answer, "search_energy_pages", recording_search(calls, "news"))
    pages = await gemma_answer._search_public_pages("Waarom stijgt de gasprijs?", "official_energy")
    assert pages and calls == [("stijgt gasprijs", "news")]


@pytest.mark.asyncio
async def test_national_consumer_sites_are_the_last_try(monkeypatch):
    calls = []
    monkeypatch.setattr(gemma_answer, "search_energy_pages", recording_search(calls, "consumer"))
    assert await gemma_answer._search_public_pages("Wat kost een laadpaal thuis?", "official_energy")
    assert [scope for _, scope in calls][-2:] == ["flanders_public", "consumer"]


@pytest.mark.asyncio
async def test_a_knowledge_base_miss_searches_the_public_sites(monkeypatch):
    from luminus_harness.knowledge import KnowledgeChunk

    monkeypatch.setenv("GEMINI_API_KEY", "test")
    chunk = KnowledgeChunk(page=2, text="Netkosten netkosten worden aangerekend door de netbeheerder via de leverancier.", urls=())
    monkeypatch.setattr(gemma_answer, "retrieve", lambda *_args, **_kwargs: [chunk])
    monkeypatch.setattr(gemma_answer, "_strong_local_match", lambda *_: True)
    calls = []
    monkeypatch.setattr(gemma_answer, "search_energy_pages", recording_search(calls, "flanders_public"))
    prompts = []

    def generate(prompt, *_args):
        prompts.append(prompt)
        return "Ik weet het niet zeker." if len(prompts) == 1 else "Netkosten zijn de kosten van het net."

    monkeypatch.setattr(gemma_answer, "_generate", generate)
    result = await gemma_answer.answer_energy_question("Wat zijn netkosten?", [chunk])
    assert result["answer"] == "Netkosten zijn de kosten van het net."
    assert [source["url"] for source in result["sources"]] == ["https://www.flanders_public.test/x"]
    assert "Geen passend PDF-fragment gevonden." in prompts[1]


@pytest.mark.asyncio
async def test_a_searched_answer_that_still_is_not_sure_gets_the_honest_reply(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "search_energy_pages", recording_search([], "official_energy"))
    monkeypatch.setattr(gemma_answer, "_generate", lambda *_args: "Ik weet het niet zeker, de pagina gaat over iets anders.")
    result = await gemma_answer.answer_energy_question("Hoe vraag ik een nieuwe aansluiting aan?", [])
    assert result["answer"] == gemma_answer._NO_SOURCE_REPLY["nl"]
    assert result["sources"] == []


@pytest.mark.asyncio
async def test_searches_and_pages_read_are_reported_as_steps(monkeypatch):
    from luminus_harness.steps import answer_steps

    steps = []
    answer_steps.set(steps.append)
    monkeypatch.setattr(gemma_answer, "search_energy_pages", recording_search([], "grid_operators"))
    await gemma_answer._search_public_pages("Hoe lees ik mijn digitale meter af?", "official_energy")
    assert steps == [
        {"kind": "search", "sites": ["fluvius.be", "sibelga.be", "ores.be", "resa.be"]},
        {"kind": "read", "pages": [{"title": "grid_operators", "url": "https://www.grid_operators.test/x"}]},
    ]


@pytest.mark.asyncio
async def test_pages_that_do_not_answer_lead_to_the_next_sites(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    calls = []

    async def search(query, scope, max_pages=2):
        calls.append(scope)
        return [{"title": scope, "url": f"https://www.{scope}.test/x", "content": "tekst"}] if scope in ("grid_operators", "official_energy") else []

    monkeypatch.setattr(gemma_answer, "search_energy_pages", search)
    answers = iter(["Ik weet het niet zeker.", "Je verlaagt je piek door toestellen te spreiden."])
    monkeypatch.setattr(gemma_answer, "_generate", lambda *_args: next(answers))
    result = await gemma_answer.answer_energy_question("Hoe kan ik mijn capaciteitstarief verlagen?", [])
    assert calls == ["grid_operators", "official_energy"]
    assert result["answer"] == "Je verlaagt je piek door toestellen te spreiden."
    assert [source["title"] for source in result["sources"]] == ["official_energy"]


@pytest.mark.asyncio
async def test_a_repeated_search_reuses_the_pages_but_not_an_empty_result(monkeypatch):
    from luminus_harness import gemma_answer

    calls = []
    results = [[], [{"title": "Energiedelen", "url": "https://www.fluvius.be/energiedelen", "content": "x"}]]

    async def search(query, scope, max_pages=2):
        calls.append(query)
        return results[min(len(calls) - 1, 1)]

    monkeypatch.setattr(gemma_answer, "search_energy_pages", search)
    assert await gemma_answer._search_scope("Wat is energiedelen?", "official_energy") == []
    first = await gemma_answer._search_scope("Wat is energiedelen?", "official_energy")
    again = await gemma_answer._search_scope("wat is  ENERGIEDELEN?", "official_energy")
    assert first == again and len(calls) == 2, "The empty result was retried, the found pages were reused"


@pytest.mark.asyncio
async def test_failing_fetch_is_reported_as_a_search_error(monkeypatch):
    from luminus_harness import browserbase_sources
    from luminus_harness.steps import answer_steps

    steps = []
    answer_steps.set(steps.append)
    monkeypatch.setattr(browserbase_sources, "_search", lambda *_: [{"title": "CREG", "url": "https://www.creg.be/x"}])
    monkeypatch.setattr(browserbase_sources, "_fetch", lambda _url: "Browserbase Fetch kon niet uitvoeren: het account heeft geen beschikbare quota.")
    assert await browserbase_sources.search_energy_pages("capaciteitstarief", "official_energy") == []
    assert steps == [{"kind": "search_error", "message": "Browserbase Fetch kon niet uitvoeren: het account heeft geen beschikbare quota."}]


@pytest.mark.asyncio
async def test_jev_topic_picks_the_sites_and_all_public_sites_come_last(monkeypatch):
    from luminus_harness.jev import search_topic

    calls = []

    async def search(query, scope, max_pages=2):
        calls.append(scope)
        return []

    monkeypatch.setattr(gemma_answer, "search_energy_pages", search)
    token = search_topic.set("fuel")
    try:
        assert await gemma_answer._search_public_pages("Wat kost een liter stookolie?", "official_energy") == []
    finally:
        search_topic.reset(token)
    assert calls[:2] == ["fuel", "news"]
    assert calls[-1] == "all_public"
    assert "petrolfed.be" in gemma_answer.ENERGY_SOURCE_SCOPES["all_public"]
    assert "luminus.be" not in gemma_answer.ENERGY_SOURCE_SCOPES["all_public"]
