from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from luminus_harness import comparison_session as comparison
from luminus_harness import contract_advice, jev, market_prices
from luminus_harness.rate_limit import SlidingWindowRateLimiter
from tests.test_karen_market_prices import market as spot_market

FLAT = [{"month": f"2026-{m:02d}", "point": 160.0, "low": 120.0, "high": 220.0} for m in range(1, 13)]


def market(months=FLAT):
    data = spot_market()
    data["contracts"]["suppliers"].append(
        {"name": "Eneco", "status": "verified", "product_name": "Flex", "price_per_kwh_eur": 0.20}
    )
    data["forecast"] = {"unit": "EUR/MWh", "band": "10-90%", "stale": False, "months": months}
    return data


@pytest.mark.parametrize("question", [
    "Is een vast contract aan 21 cent per kWh beter dan variabel?",
    "vast of variabel?",
    "fixed at 0,25 €/kWh or variable?",
    "Should I take a fixed contract at 17 cents?",
])
def test_fixed_or_variable_questions_are_recognised(question):
    assert contract_advice.is_contract_advice_question(question)


@pytest.mark.parametrize("question", [
    "wat zijn de stroomprijzen vandaag",
    "Ik wil een vaste prijs",
    "what is the fixed fee at Luminus?",
    "Vergelijk aanbiedingen",
])
def test_other_questions_are_not_contract_advice(question):
    assert not contract_advice.is_contract_advice_question(question)


@pytest.mark.parametrize("text, cents", [
    ("vast aan 21 cent per kWh", 21.0),
    ("fixed at 19,5 ct/kWh", 19.5),
    ("0,25 €/kWh vast", 25.0),
    ("€0.18 per kWh", 18.0),
    ("vast of variabel?", None),
    ("vast aan 300 cent", None),
])
def test_offered_fixed_price_is_read_in_cents(text, cents):
    assert contract_advice.offered_fixed_cents(text) == cents


def test_usage_is_read_from_the_message():
    assert contract_advice.yearly_usage_kwh("ik verbruik 4.500 kWh per jaar") == 4500
    assert contract_advice.yearly_usage_kwh("21 cent per kWh") is None


def test_probability_falls_as_the_fixed_price_drops():
    data = market()
    reference, _ = contract_advice._variable_reference_cents(data)
    chances = [contract_advice.chance_variable_is_cheaper(FLAT, reference, fixed) for fixed in (30, 22, reference, 17, 12)]
    assert chances == sorted(chances, reverse=True)
    # With a flat forecast the median path keeps today's price, so a fixed price equal to it is a coin flip.
    assert chances[2] == pytest.approx(0.5, abs=0.01)
    assert chances[0] > 0.95 and chances[-1] < 0.05


@pytest.mark.parametrize("fixed, decision", [(30, "variable"), (19.4, "toss-up"), (12, "fixed")])
def test_decision_follows_the_probability(fixed, decision):
    reply = contract_advice.contract_advice_reply(f"Vast aan {fixed} cent per kWh of variabel?", market())
    assert reply["decision"] == decision
    assert reply["processing"] == "contract-advice"
    assert "geen financieel advies" in reply["answer"]


def test_dutch_reply_names_probability_and_yearly_difference():
    reply = contract_advice.contract_advice_reply("Vast aan 25 cent per kWh of variabel? Ik verbruik 4.500 kWh.", market())
    assert reply["answer"].startswith("Variabel lijkt de betere keuze.")
    assert f"ongeveer {round(reply['probability_variable_cheaper'] * 100)}%" in reply["answer"]
    assert "per jaar minder dan vast bij 4.500 kWh" in reply["answer"]


def test_english_question_gets_english_reply():
    reply = contract_advice.contract_advice_reply("Should I take a fixed contract at 12 cents?", market())
    assert reply["answer"].startswith("The fixed contract looks like the better choice.")


def test_french_question_gets_french_reply():
    question = "Un contrat fixe à 12 centimes, c'est mieux que variable ?"
    assert contract_advice.is_contract_advice_question(question)
    reply = contract_advice.contract_advice_reply(question, market())
    assert reply["answer"].startswith("Le contrat fixe semble être le meilleur choix.")
    assert "ce n'est pas un conseil financier" in reply["answer"]


def test_french_question_without_price_asks_for_it_in_french():
    reply = contract_advice.contract_advice_reply("Contrat fixe ou variable ?", market())
    assert reply["answer"].startswith("Je peux le calculer")


def test_missing_fixed_price_asks_for_it():
    reply = contract_advice.contract_advice_reply("vast of variabel?", market())
    assert "vaste prijs nodig" in reply["answer"]
    assert "decision" not in reply


@pytest.mark.parametrize("data", [
    market(months=FLAT[:3]),
    {**market(), "forecast": {**market()["forecast"], "stale": True}},
    {**market(), "contracts": {"suppliers": []}},
])
def test_no_reply_without_a_usable_forecast_or_tariff_cards(data):
    assert contract_advice.contract_advice_reply("vast aan 21 cent of variabel?", data) is None


@pytest.fixture
def api(monkeypatch):
    import energy_voice_app as app

    monkeypatch.setattr(app, "_comparison_sessions", comparison.ComparisonSessions())
    monkeypatch.setattr(app, "_api_rate_limiter", SlidingWindowRateLimiter(requests=100, window_seconds=60, max_clients=100))
    monkeypatch.setattr(comparison, "compare_energy_offers", AsyncMock(side_effect=AssertionError("no offer lookup")))
    hosted = AsyncMock(return_value={"answer": "Bestaand antwoord", "sources": []})
    monkeypatch.setattr(app, "answer_energy_question", hosted)
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=market()))
    with TestClient(app.api_app) as client:
        yield client, hosted


def ask(api, question):
    response = api[0].post("/api/answer", json={"question": question})
    assert response.status_code == 200, response.text
    return response.json()


def test_api_answers_contract_questions_with_a_decision(api):
    reply = ask(api, "Is een vast contract aan 25 cent per kWh beter dan variabel?")
    assert reply["decision"] == "variable"
    assert reply["comparison_session_id"] is None
    api[1].assert_not_awaited()


def test_gas_contracts_keep_the_source_path(api):
    assert ask(api, "Vast gascontract aan 8 cent of variabel?")["answer"] == "Bestaand antwoord"


def test_jev_contract_route_answers_wording_the_word_lists_miss(api, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("KAREN_JEV_ROUTING", "1")
    monkeypatch.setattr(jev, "_post_system_one", lambda _body: {"answers": {"answer": {
        "type": "choice", "choice": "contract_advice", "confidence": 0.95, "probabilities": {}}}})
    question = "My supplier offers me 0,22 €/kWh locked in for a year, good deal?"
    assert not contract_advice.is_contract_advice_question(question)
    assert ask(api, question)["processing"] == "contract-advice"
