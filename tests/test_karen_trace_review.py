"""Conversations from the live Langfuse traces of 12 November 2026, as regression tests."""

from unittest.mock import AsyncMock

import pytest

from luminus_harness import market_prices
from luminus_harness.contract_check import answer_contract_check, contract_check_topic
from tests.test_karen_gas_prices import with_gas
from tests.test_karen_market_prices import api, market  # noqa: F401  (api is a fixture)


def ask(api, question, history=()):  # noqa: F811
    body = {"question": question, "history": [{"question": q, "answer": a} for q, a in history]}
    response = api[0].post("/api/answer", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --- 1. the fixed contract-check text ignored what the visitor added -------------------------------------

@pytest.mark.parametrize("question", [
    "Hoe check ik mijn energiecontract NIET?",
    "Hoe controleer ik niet mijn energiecontract?",  # the rewritten follow-up, as in the trace
    "Hoe check ik mijn energiecontract zonder internet?",
    "Hoe controleer ik mijn energiecontract zonder computer?",
    "Hoe kijk ik mijn energiecontract na als ik geen internet heb?",
    "Kan ik mijn energiecontract telefonisch laten nakijken?",
    "Hoe check ik mijn energiecontract op papier?",
])
def test_a_condition_the_fixed_steps_cannot_meet_takes_the_source_path(question):
    assert contract_check_topic(question) is None, question


@pytest.mark.parametrize("question", [
    "Hoe check ik mijn energiecontract?",
    "Betaal ik niet te veel voor stroom?",
    "Hoe kijk ik mijn energiecontract na zonder me te laten overhaasten?",
])
def test_ordinary_contract_checks_keep_the_fixed_steps(question):
    assert contract_check_topic(question) == "check", question
    assert answer_contract_check(question)["processing"] == "contract-check"


def test_api_no_internet_question_is_not_sent_to_online_tools(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=market()))
    reply = ask(api, "Hoe check ik mijn energiecontract zonder internet?")
    assert reply["answer"] == "Bestaand antwoord", "The source path, not the four online steps"


# --- 2. a price compared with an earlier period: Karen has today's cards only --------------------------------

@pytest.mark.parametrize("question", [
    "Hoeveel kost aardgas nu vergeleken met vorig kwartaal",
    "Wat is de gasprijs nu, vergeleken met vorige maand?",
    "Hoeveel kost gas tegenover vorig jaar?",
    "Is de gasprijs gestegen?",
])
def test_gas_price_against_an_earlier_period_says_there_is_no_earlier_price(api, monkeypatch, question):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    answer = ask(api, question)["answer"]
    lead = answer.split("\n")[0]
    assert "alleen de prijzen van vandaag" in lead and "niet vergelijken" in lead, answer
    assert "TotalEnergies myEssential Variabel: 7,92 cent/kWh" in answer, "Today's cards still follow"


def test_a_plain_gas_price_question_has_no_comparison_lead(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    assert "niet vergelijken" not in ask(api, "wat is de gasprijs vandaag")["answer"]


def test_earlier_period_lead_in_french_and_english():
    assert market_prices.earlier_period_asked("le gaz est-il plus cher que le trimestre dernier ?")
    assert market_prices.earlier_period_asked("is gas more expensive than last quarter?")
    assert not market_prices.earlier_period_asked("quel est le prix du gaz")
    assert "today" in market_prices.NO_EARLIER_PRICES["en"] and "aujourd" in market_prices.NO_EARLIER_PRICES["fr"]


# --- 3. "Dat is niet per kwartaal" after that answer got nothing -------------------------------------------

GAS_TURN = ("Hoeveel kost aardgas nu vergeleken met vorig kwartaal",
            "Gasprijzen op de huidige tariefkaarten van leveranciers in Vlaanderen: ...")


@pytest.mark.parametrize("objection", ["Dat is niet per kwartaal", "dat klopt niet", "Nee, dat vroeg ik niet", "Dat zijn geen kwartaalprijzen"])
def test_an_objection_to_a_period_answer_gets_the_honest_reason(api, monkeypatch, objection):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    reply = ask(api, objection, history=[GAS_TURN])
    assert "vorig kwartaal" in reply["answer"] and "niet vergelijken" in reply["answer"], reply["answer"]
    api[2].assert_not_awaited()


def test_an_objection_without_a_period_question_keeps_the_source_path(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    reply = ask(api, "Dat klopt niet", history=[("Hoe zeg ik mijn contract op?", "Via je leverancier ...")])
    assert reply["answer"] == "Bestaand antwoord"
