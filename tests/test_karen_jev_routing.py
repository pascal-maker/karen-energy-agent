import asyncio
import io
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from luminus_harness import jev, market_prices
from luminus_harness.rate_limit import SlidingWindowRateLimiter


def jev_answer(choice="price", confidence=0.9):
    return {"model": "jev-1.14.0", "usage": {}, "answers": {"answer": {
        "type": "choice", "choice": choice, "confidence": confidence,
        "probabilities": {name: (confidence if name == choice else 0.01) for name in jev.ROUTES},
    }}}


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("KAREN_JEV_ROUTING", "1")


@pytest.mark.asyncio
@pytest.mark.parametrize("key, switch", [("", "1"), ("test-key", ""), ("test-key", "0")])
async def test_jev_is_off_without_both_key_and_switch(monkeypatch, key, switch):
    monkeypatch.setenv("TYPESAFE_API_KEY", key)
    monkeypatch.setenv("KAREN_JEV_ROUTING", switch)
    monkeypatch.setattr(jev, "_post_system_one", lambda _body: pytest.fail("Jev must not be called"))
    assert await jev.route_question("look up energy prices") is None


@pytest.mark.asyncio
async def test_private_details_never_reach_jev(monkeypatch, enabled):
    monkeypatch.setattr(jev, "_post_system_one", lambda _body: pytest.fail("Jev must not be called"))
    assert await jev.route_question("Mijn klantnummer is 123, wat is de prijs?") is None
    assert await jev.route_question("Waarom is mijn factuur zo hoog?") is None


@pytest.mark.asyncio
async def test_request_shape_and_confident_answer(monkeypatch, enabled):
    sent = {}

    def urlopen(request, timeout):
        sent.update(url=request.full_url, auth=request.get_header("Authorization"), body=json.loads(request.data), timeout=timeout)
        return io.BytesIO(json.dumps(jev_answer("price", 0.93)).encode())

    monkeypatch.setattr(jev, "urlopen", urlopen)
    assert await jev.route_question("look up energy prices") == "price"
    assert sent["url"] == "https://api.typesafe.ai/v1/systemone"
    assert sent["auth"] == "Bearer test-key"
    assert sent["body"]["model"] == "jev-latest"
    assert sent["body"]["state"] == "look up energy prices"
    question = sent["body"]["questions"]["answer"]
    assert question["type"] == "choice" and set(question["criteria"]) == set(jev.ROUTES)
    assert sent["timeout"] == jev.TIMEOUT_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    jev_answer("price", 0.4),
    {"answers": {"answer": {"type": "choice", "choice": "unknown-route", "confidence": 0.9}}},
    {"answers": {}},
    "not json",
])
async def test_unsure_or_unexpected_answers_fall_back(monkeypatch, enabled, response):
    monkeypatch.setattr(jev, "_post_system_one", lambda _body: response)
    assert await jev.route_question("look up energy prices") is None


@pytest.mark.asyncio
async def test_api_errors_fall_back(monkeypatch, enabled):
    def fail(_body):
        raise OSError("timed out")

    monkeypatch.setattr(jev, "_post_system_one", fail)
    assert await jev.route_question("look up energy prices") is None


@pytest.fixture
def api(monkeypatch, enabled):
    import energy_voice_app as app

    monkeypatch.setattr(app, "_api_rate_limiter", SlidingWindowRateLimiter(requests=100, window_seconds=60, max_clients=100))
    hosted = AsyncMock(return_value={"answer": "Bestaand antwoord", "sources": []})
    monkeypatch.setattr(app, "answer_energy_question", hosted)
    with TestClient(app.api_app) as client:
        yield client, app, hosted


def ask(api, question, session=None):
    response = api[0].post("/api/answer", json={"question": question, "comparison_session_id": session})
    assert response.status_code == 200, response.text
    return response.json()


def route_to(monkeypatch, choice):
    monkeypatch.setattr(jev, "_post_system_one", lambda _body: jev_answer(choice))


def test_jev_price_route_answers_wording_the_word_lists_miss(api, monkeypatch):
    from tests.test_karen_market_prices import market

    route_to(monkeypatch, "price")
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=market()))
    question = "how much does power go for these days?"
    assert not market_prices.is_market_price_question(question)
    assert ask(api, question)["processing"] == "market-data"


def test_jev_compare_route_gets_the_supplier_cards(api, monkeypatch):
    from tests.test_karen_market_prices import market

    route_to(monkeypatch, "compare")
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=market()))
    reply = ask(api, "ik wil overstappen naar een goedkopere leverancier")
    assert reply["processing"] == "offer-comparison"


def test_jev_off_topic_route_skips_the_source_search(api, monkeypatch):
    route_to(monkeypatch, "off_topic")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    reply = ask(api, "who won the football match?")
    assert reply["processing"] == "small-talk"
    api[2].assert_not_awaited()


@pytest.mark.parametrize("question", [
    "Why is my advance payment different from my annual bill?",
    "Pourquoi mon acompte est-il différent de ma facture annuelle ?",
])
def test_bill_questions_jev_calls_small_talk_still_get_sources(api, monkeypatch, question):
    route_to(monkeypatch, "small_talk")
    assert ask(api, question)["answer"] == "Bestaand antwoord"


def test_jev_energy_route_keeps_price_words_on_the_source_path(api, monkeypatch):
    route_to(monkeypatch, "energy_other")
    load = AsyncMock()
    monkeypatch.setattr(market_prices, "load_market_data", load)
    assert ask(api, "wat kost stroom op mijn jaarafrekening?")["answer"] == "Bestaand antwoord"
    load.assert_not_awaited()


def test_without_jev_the_word_lists_still_route(api, monkeypatch):
    monkeypatch.setenv("KAREN_JEV_ROUTING", "0")
    from tests.test_karen_market_prices import market

    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=market()))
    assert ask(api, "Vergelijk aanbiedingen")["processing"] == "offer-comparison"


def test_gas_without_gas_cards_stays_on_the_source_path(api, monkeypatch):
    from tests.test_karen_market_prices import market

    route_to(monkeypatch, "price")
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=market()))
    assert ask(api, "Hoeveel kost aardgas per kWh?")["answer"] == "Bestaand antwoord"


@pytest.mark.parametrize("choice", ["price", "energy_other", "small_talk", "off_topic", "contract_advice"])
def test_gas_price_questions_get_the_gas_cards_whatever_jev_picks(api, monkeypatch, choice):
    from tests.test_karen_gas_prices import with_gas

    route_to(monkeypatch, choice)
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    for question in ("Hey kan je mij de prijs van gas geven", "Vertel me eens de gasprijzen"):
        reply = ask(api, question)
        assert reply["processing"] == "market-data" and "Gasprijzen" in reply["answer"]
    api[2].assert_not_awaited()


@pytest.mark.asyncio
async def test_jev_route_and_probabilities_are_reported_as_a_step(monkeypatch, enabled):
    from luminus_harness.steps import answer_steps

    steps = []
    answer_steps.set(steps.append)
    monkeypatch.setattr(jev, "_post_system_one", lambda _body: jev_answer("energy_other", 0.92))
    assert await jev.route_question("Wat is een budgetmeter?") == "energy_other"
    monkeypatch.setattr(jev, "_post_system_one", lambda _body: jev_answer("price", 0.4))
    assert await jev.route_question("hmm") is None
    assert steps[0]["kind"] == "route" and steps[0]["choice"] == "energy_other" and steps[0]["used"] is True
    assert steps[0]["probabilities"]["energy_other"] == 0.92 and steps[0]["probabilities"]["price"] == 0.01
    assert steps[1]["used"] is False, "A guess below the confidence bar is shown but not followed"


@pytest.mark.asyncio
async def test_jev_picks_the_search_topic_in_the_same_call(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("KAREN_JEV_ROUTING", "1")
    bodies = []

    def post(body):
        bodies.append(body)
        return {"answers": {
            "answer": {"type": "choice", "choice": "energy_other", "confidence": 0.9},
            "topic": {"type": "choice", "choice": "fuel", "confidence": 0.8},
        }}

    monkeypatch.setattr(jev, "_post_system_one", post)

    async def ask():
        route = await jev.route_question("Wat is de prijs van olie vandaag?")
        return route, jev.search_topic.get()

    assert await asyncio.create_task(ask()) == ("energy_other", "fuel")
    assert len(bodies) == 1 and set(bodies[0]["questions"]) == {"answer", "topic"}
    assert jev.search_topic.get() is None, "the topic belongs to one question, not to the next"
