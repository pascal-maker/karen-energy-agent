import io
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from luminus_harness import comparison_session as comparison
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

    monkeypatch.setattr(app, "_comparison_sessions", comparison.ComparisonSessions())
    monkeypatch.setattr(app, "_api_rate_limiter", SlidingWindowRateLimiter(requests=100, window_seconds=60, max_clients=100))
    monkeypatch.setattr(comparison, "compare_energy_offers", AsyncMock(side_effect=AssertionError("no offer lookup")))
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


def test_jev_compare_route_starts_the_comparison(api, monkeypatch):
    route_to(monkeypatch, "compare")
    reply = ask(api, "ik wil overstappen naar een goedkopere leverancier")
    assert reply["answer"] == comparison.PROMPTS["region"]
    assert reply["comparison_session_id"] in api[1]._comparison_sessions.sessions


def test_jev_off_topic_route_skips_the_source_search(api, monkeypatch):
    route_to(monkeypatch, "off_topic")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    reply = ask(api, "who won the football match?")
    assert reply["processing"] == "small-talk"
    api[2].assert_not_awaited()


def test_jev_energy_route_keeps_price_words_on_the_source_path(api, monkeypatch):
    route_to(monkeypatch, "energy_other")
    load = AsyncMock()
    monkeypatch.setattr(market_prices, "load_market_data", load)
    assert ask(api, "wat kost stroom op mijn jaarafrekening?")["answer"] == "Bestaand antwoord"
    load.assert_not_awaited()


def test_without_jev_the_word_lists_still_route(api, monkeypatch):
    monkeypatch.setenv("KAREN_JEV_ROUTING", "0")
    reply = ask(api, "Vergelijk aanbiedingen")
    assert reply["answer"] == comparison.PROMPTS["region"]


def test_jev_price_route_still_leaves_gas_to_the_source_path(api, monkeypatch):
    from tests.test_karen_market_prices import market

    route_to(monkeypatch, "price")
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=market()))
    assert ask(api, "Hoeveel kost aardgas per kWh?")["answer"] == "Bestaand antwoord"
