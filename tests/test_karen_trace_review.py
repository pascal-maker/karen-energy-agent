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
            "Ik heb alleen de prijzen van vandaag, geen prijzen van vorig kwartaal. Daarom kan ik niet vergelijken; "
            "dit zijn de huidige prijzen.\nGasprijzen op de huidige tariefkaarten van leveranciers in Vlaanderen: ...")


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


# --- review of a9fabbb -------------------------------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "Hoeveel kost aardgas bij Eneco vergeleken met vorig kwartaal?",
    "Wat kost gas bij Eneco nu tegenover vorig jaar?",
])
def test_review_a9fabbb_supplier_price_against_an_earlier_period_says_so_too(api, monkeypatch, question):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    reply = ask(api, question)
    assert reply["processing"] == "supplier-tariff-card", reply
    lead = reply["answer"].split("\n")[0]
    assert "alleen de prijzen van vandaag" in lead and "niet vergelijken" in lead, reply["answer"]
    assert "tariefkaart van Eneco" in reply["answer"] and "Eneco Aardgas Flex (variabel): 9,1 cent/kWh" in reply["answer"], "Today's Eneco card still follows"


def test_review_a9fabbb_plain_supplier_price_has_no_lead(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    reply = ask(api, "Hoeveel kost aardgas bij Eneco?")
    assert reply["processing"] == "supplier-tariff-card" and "niet vergelijken" not in reply["answer"]


@pytest.mark.parametrize("question", [
    "Hoeveel kost gas per kWh, is dat goedkoper dan elektriciteit?",
    "Is gas duurder dan stroom?",
    "Is Eneco goedkoper dan Luminus?",
    "le gaz est-il plus cher que l'électricité ?",
    "is gas more expensive than electricity?",
])
def test_review_a9fabbb_comparing_two_things_today_is_no_earlier_period(question):
    assert not market_prices.earlier_period_asked(question), question


@pytest.mark.parametrize("question", [
    "Is gas duurder dan vorig jaar?", "Is stroom goedkoper dan vroeger?", "Is gas duurder geworden?",
    "Is gas duurder dan een jaar geleden?", "le gaz est-il plus cher que l'an dernier ?", "is gas more expensive than last year?",
    "is gas more expensive than before?",
])
def test_review_a9fabbb_a_comparison_with_a_time_is_an_earlier_period(question):
    assert market_prices.earlier_period_asked(question), question


def test_review_a9fabbb_gas_against_electricity_gets_no_period_lead(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    answer = ask(api, "Hoeveel kost gas per kWh, is dat goedkoper dan elektriciteit?")["answer"]
    assert "eerdere periode" not in answer and "niet vergelijken" not in answer, answer


@pytest.mark.parametrize("previous", [
    ("Hoe check ik mijn energiecontract van vorig jaar?", "Begin bij je eigen contract en beslis niet overhaast. Doe de CREG-scan en de V-test."),
    ("Is mijn voorschot gestegen tegenover vorig jaar?", "Bestaand antwoord"),
    ("Hoeveel kost aardgas nu vergeleken met vorig kwartaal", "Gasprijzen op de huidige tariefkaarten van leveranciers in Vlaanderen: ..."),
])
@pytest.mark.parametrize("objection", ["Dat klopt niet", "Dat is niet per kwartaal"])
def test_review_a9fabbb_an_objection_counts_only_after_the_no_earlier_prices_answer(api, monkeypatch, previous, objection):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    reply = ask(api, objection, history=[previous])
    assert "prijzen van vandaag" not in reply["answer"] and "sociaal tarief" not in reply["answer"], reply["answer"]


def test_review_a9fabbb_objection_after_the_supplier_period_answer(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    first = ask(api, "Hoeveel kost aardgas bij Eneco vergeleken met vorig kwartaal?")
    reply = ask(api, "Dat is niet per kwartaal", history=[("Hoeveel kost aardgas bij Eneco vergeleken met vorig kwartaal?", first["answer"])])
    assert "vorig kwartaal" in reply["answer"] and "niet vergelijken" in reply["answer"], reply["answer"]
    api[2].assert_not_awaited()


def test_review_a9fabbb_outside_flanders_keeps_the_regional_answer(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    reply = ask(api, "Hoeveel kost aardgas bij Eneco in Brussel vergeleken met vorig kwartaal?")
    assert "prijzen van vandaag" not in reply["answer"], "Karen has no Brussels cards, so she does not claim today's prices"
