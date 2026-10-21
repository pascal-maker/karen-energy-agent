from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from luminus_harness import comparison_session as comparison
from luminus_harness import gemma_answer, market_prices
from luminus_harness.rate_limit import SlidingWindowRateLimiter

TODAY = datetime.now(market_prices.BRUSSELS).date().isoformat()


def market(**spot_overrides):
    return {
        "spot": {"unit": "EUR/MWh", "today": {
            "date": TODAY, "avg": 221.97, "min": 176.11, "max": 297.93, "cheapest_hours": [0, 3, 4], **spot_overrides,
        }},
        "contracts": {"as_of": TODAY, "suppliers": [
            {"name": "Luminus", "status": "verified", "product_name": "BasicFlex Online", "price_per_kwh_eur": 0.1919,
             "source_url": "https://www.luminus.be/tariefkaart.pdf", "retrieved_at": TODAY},
            {"name": "Engie", "status": "unverified", "product_name": "Easy variabel", "price_per_kwh_eur": None},
            {"name": "Frank Energie", "status": "verified", "product_name": "Frank Energie Variabel",
             "price_per_kwh_eur": 0.186786, "source_url": "https://www.frankenergie.be/tariefkaart.pdf"},
            {"name": "Mega", "status": "unverified", "product_name": "Mega Flex", "price_per_kwh_eur": 0.10},
        ]},
        "sources": [{"title": "Energy-Charts (Fraunhofer ISE) · day-ahead BE", "url": "https://www.energy-charts.info/"}],
    }


@pytest.mark.parametrize("question", [
    "look up energy prices",
    "can you look up the cheapest energy prices",
    "what is the electricity price today",
    "wat zijn de stroomprijzen vandaag",
    "zoek de energieprijzen op",
    "zoek de beste energieprijzen",
    "wat is de stroomprijs vandaag",
    "wat is de goedkoopste stroom",
    "Hallo, wat kost stroom vandaag?",
])
def test_plain_price_questions_are_answered_from_market_data(question):
    assert market_prices.is_market_price_question(question)


@pytest.mark.parametrize("question", [
    "Vergelijk aanbiedingen",
    "compare offers",
    "Wat is de elektriciteitsprijs volgens CREG?",
    "Hoeveel kost aardgas per kWh?",
    "Wat is het capaciteitstarief?",
    "Wat is het sociaal tarief voor elektriciteit?",
    "Waarom is mijn energiefactuur zo hoog?",
    "Wat kost het om mijn energiecontract op te zeggen?",
    "Stroomprijs voor 3500 kWh in 9000",
    "Wie wint de Ronde van Vlaanderen?",
])
def test_other_questions_keep_their_own_route(question):
    assert not market_prices.is_market_price_question(question)


def test_dutch_reply_shows_todays_prices_and_only_verified_suppliers_cheapest_first():
    reply = market_prices.market_price_reply(market(), "nl")
    answer = reply["answer"]
    assert "vandaag gemiddeld 22,2 cent per kWh, tussen 17,6 en 29,8 cent" in answer
    assert "rond 0u, 3u en 4u" in answer
    assert answer.index("Frank Energie Variabel: 18,7 cent/kWh") < answer.index("Luminus BasicFlex Online: 19,2 cent/kWh")
    assert "Engie" not in answer and "Mega" not in answer
    assert "postcode" not in answer, "No comparison pitch the user didn't ask for"
    assert reply["processing"] == "market-data"
    assert [source["url"] for source in reply["sources"]] == [
        "https://www.energy-charts.info/",
        "https://www.frankenergie.be/tariefkaart.pdf",
        "https://www.luminus.be/tariefkaart.pdf",
    ]


def test_english_question_gets_an_english_reply():
    reply = market_prices.market_price_reply(market(), market_prices.question_language("look up energy prices"))
    assert reply["answer"].startswith("The Belgian wholesale electricity price (day-ahead) today averages 22.2 cents per kWh")
    assert "postcode" not in reply["answer"]


def test_an_older_spot_day_is_named_by_its_date():
    reply = market_prices.market_price_reply(market(date="2026-10-01"), "nl")
    assert "is op 2026-10-01 gemiddeld" in reply["answer"]


def test_missing_spot_values_give_no_reply():
    assert market_prices.market_price_reply(market(avg=None), "nl") is None
    assert market_prices.market_price_reply({}, "nl") is None


@pytest.fixture
def api(monkeypatch):
    import energy_voice_app as app

    monkeypatch.setattr(app, "_comparison_sessions", comparison.ComparisonSessions())
    monkeypatch.setattr(app, "_api_rate_limiter", SlidingWindowRateLimiter(requests=100, window_seconds=60, max_clients=100))
    lookup = AsyncMock(side_effect=AssertionError("A price question must not start the offer lookup"))
    monkeypatch.setattr(comparison, "compare_energy_offers", lookup)
    hosted = AsyncMock(return_value={"answer": "Bestaand antwoord", "sources": []})
    monkeypatch.setattr(app, "answer_energy_question", hosted)
    with TestClient(app.api_app) as client:
        yield client, app, hosted


def ask(api, question, session=None):
    response = api[0].post("/api/answer", json={"question": question, "comparison_session_id": session})
    assert response.status_code == 200, response.text
    return response.json()


def test_price_question_gets_prices_instead_of_the_comparison_form(api, monkeypatch):
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=market()))
    reply = ask(api, "zoek de beste energieprijzen")
    assert "22,2 cent per kWh" in reply["answer"]
    assert "postcode" not in reply["answer"].split("\n")[0]
    assert reply["comparison_session_id"] is None
    assert not api[1]._comparison_sessions.sessions
    api[2].assert_not_awaited()


def test_unavailable_market_data_falls_back_to_the_existing_route(api):
    reply = ask(api, "wat zijn de stroomprijzen vandaag")
    assert reply["answer"] == "Bestaand antwoord"


def test_an_active_comparison_is_not_interrupted(api, monkeypatch):
    load = AsyncMock(return_value=market())
    monkeypatch.setattr(market_prices, "load_market_data", load)
    token = ask(api, "Vergelijk gas")["comparison_session_id"]
    ask(api, "wat kost stroom", token)
    load.assert_not_awaited()


@pytest.mark.asyncio
async def test_english_small_talk_falls_back_in_english(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = await gemma_answer.answer_energy_question("hi, how are you?", [])
    assert result["answer"] == gemma_answer._SMALL_TALK_FALLBACK_EN["how_are_you"]


@pytest.mark.asyncio
async def test_english_energy_question_without_sources_admits_it_does_not_know(monkeypatch):
    async def no_pages(*_args, **_kwargs):
        return []

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemma_answer, "search_energy_pages", no_pages)
    def generate(prompt, system_instruction=None):
        # Only the Dutch search translation may run; no sources means no answer generation.
        assert system_instruction is gemma_answer._SEARCH_TRANSLATION_INSTRUCTION
        return "Wat is de warmtepomppremie in Wallonië?"

    monkeypatch.setattr(gemma_answer, "_generate", generate)
    result = await gemma_answer.answer_energy_question("What is the heat pump subsidy in Wallonia?", [])
    assert result["answer"] == gemma_answer._NO_SOURCE_REPLY["en"]


@pytest.mark.parametrize(
    ("question", "lang"),
    [
        ("quel est le prix de l'électricité aujourd'hui ?", "fr"),
        ("Pourquoi mon acompte est-il différent de ma facture annuelle ?", "fr"),
        ("bonjour, ça va ?", "fr"),
        ("what's the capital of France?", "en"),
        ("wat zijn de stroomprijzen vandaag", "nl"),
        ("ok", "nl"),
    ],
)
def test_question_language(question, lang):
    assert market_prices.question_language(question) == lang


def test_french_price_question_gets_a_french_reply():
    question = "quel est le prix de l'électricité aujourd'hui ?"
    assert market_prices.is_market_price_question(question)
    reply = market_prices.market_price_reply(market(), market_prices.question_language(question))
    assert reply["answer"].startswith("Le prix de gros de l'électricité en Belgique")
    assert "code postal" not in reply["answer"]


@pytest.mark.asyncio
async def test_small_talk_names_the_reply_language(monkeypatch):
    prompts = []

    def generate(prompt, system_instruction=None):
        prompts.append(prompt)
        return "I only know about energy, sorry!"

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemma_answer, "_generate", generate)
    await gemma_answer.answer_small_talk("what's the capital of France?", "off_topic")
    assert prompts[-1].endswith("ANTWOORDTAAL: Engels")


@pytest.mark.asyncio
async def test_french_energy_question_searches_in_dutch_and_answers_in_french(monkeypatch):
    searched = []

    async def pages(query, *_args, **_kwargs):
        searched.append(query)
        return [{"title": "Energiesparen", "url": "https://example.org", "content": "Voorschotten zijn een raming."}]

    def generate(prompt, system_instruction=None):
        if system_instruction is gemma_answer._SEARCH_TRANSLATION_INSTRUCTION:
            return "Waarom verschilt mijn voorschot van mijn jaarafrekening?"
        assert prompt.endswith("ANTWOORDTAAL: Frans")
        assert "Pourquoi mon acompte" in prompt
        return "Votre acompte est une estimation."

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemma_answer, "search_energy_pages", pages)
    monkeypatch.setattr(gemma_answer, "_generate", generate)
    result = await gemma_answer.answer_energy_question(
        "Pourquoi mon acompte est-il différent de ma facture annuelle ?", []
    )
    assert result["answer"] == "Votre acompte est une estimation."
    assert searched and "voorschot" in searched[0]


def test_price_replies_carry_a_chart_with_hourly_prices_and_cheapest_first_bars():
    points = [[f"{hour:02d}:{minute:02d}", 200.0 + hour] for hour in range(24) for minute in (0, 15, 30, 45)]
    visual = market_prices.market_price_reply(market(points=points), "nl")["visual"]
    assert visual["kind"] == "prices" and visual["lead"].startswith("De groothandelsprijs")
    assert visual["day"]["points"][:2] == [["00:00", 20.0], ["01:00", 20.1]], "Quarter hours become hourly cents per kWh"
    assert len(visual["day"]["points"]) == 24 and visual["day"]["cheapest_hours"] == [0, 3, 4]
    assert visual["groups"] == [{"title": "Variabel", "items": [
        {"label": "Frank Energie Variabel", "value": 18.7}, {"label": "Luminus BasicFlex Online", "value": 19.2},
    ]}]
    assert "day" not in market_prices.market_price_reply(market(), "nl")["visual"], "No curve without points"


def test_after_midnight_the_evening_runs_tomorrow_is_today():
    from datetime import date, timedelta

    yesterday = (date.fromisoformat(TODAY) - timedelta(days=1)).isoformat()
    data = market(date=yesterday, avg=100.0, min=90.0, max=110.0)
    data["spot"]["tomorrow"] = {"date": TODAY, "avg": 221.97, "min": 176.11, "max": 297.93, "cheapest_hours": [1]}
    answer = market_prices.market_price_reply(data, "nl")["answer"]
    assert "vandaag gemiddeld 22,2 cent" in answer and "rond 1u" in answer
    data["spot"]["tomorrow"]["date"] = "2099-01-01"
    assert f"op {yesterday} gemiddeld 10,0 cent" in market_prices.market_price_reply(data, "nl")["answer"]
