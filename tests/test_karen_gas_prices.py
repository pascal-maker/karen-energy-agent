from unittest.mock import AsyncMock

import pytest

from luminus_harness import market_prices
from tests.test_karen_market_prices import api, ask, market  # noqa: F401  (api is a fixture)


def gas_card(name, product, price, contract_type="variable", months=None, status="verified"):
    card = {"name": name, "status": status, "product_name": product, "price_per_kwh_eur": price,
            "contract_type": contract_type, "source_url": f"https://example.be/{name}.pdf", "retrieved_at": "2026-10-20"}
    return {**card, "duration_months": months} if months else card


def with_gas():
    data = market()
    data["contracts"]["gas_suppliers"] = [
        gas_card("Eneco", "Eneco Aardgas Flex", 0.0912),
        gas_card("TotalEnergies", "TotalEnergies myEssential Variabel", 0.0792),
        gas_card("Mega", "Mega Gas", 0.05, status="unverified"),
        gas_card("Luminus", "BasicFix Online Gas", 0.087, "fixed", 24),
        gas_card("OCTA+", "OCTA+ Gas Fixed", 0.0825, "fixed", 12),
    ]
    return data


@pytest.mark.parametrize("question", [
    "Hey kan je mij de prijs van gas geven",
    "wat is de gasprijs vandaag",
    "Hoeveel kost aardgas per kWh?",
    "what is the gas price",
    "quel est le prix du gaz",
])
def test_gas_price_questions(question):
    assert market_prices.is_gas_price_question(question)
    assert not market_prices.is_market_price_question(question)


@pytest.mark.parametrize("question", [
    "waarom stijgt de gasprijs",
    "wat kost een nieuwe gasketel",
    "hoeveel kost mijn gasfactuur",
    "wat zijn de netkosten voor gas",
    "vergelijk gas",
    "gasprijs voor 15000 kWh in 9000",
    "wat zijn de stroomprijzen vandaag",
])
def test_other_gas_or_electricity_questions_are_not_gas_price_lists(question):
    assert not market_prices.is_gas_price_question(question)


def test_dutch_gas_reply_lists_only_verified_cards_cheapest_first():
    reply = market_prices.gas_price_reply(with_gas())
    lines = reply["answer"].split("\n")
    assert lines[0].startswith("Gasprijzen op de huidige tariefkaarten")
    assert lines[1:] == [
        "Variabel:",
        "• TotalEnergies myEssential Variabel: 7,92 cent/kWh",
        "• Eneco Aardgas Flex: 9,12 cent/kWh",
        "Goedkoopste vaste:",
        "• OCTA+ Gas Fixed: 8,25 cent/kWh (12 maanden vast)",
        "• Luminus BasicFix Online Gas: 8,70 cent/kWh (24 maanden vast)",
    ]
    assert "Mega" not in reply["answer"] and "postcode" not in reply["answer"]
    assert [s["url"] for s in reply["sources"]][0] == "https://example.be/TotalEnergies.pdf"


def test_gas_reply_in_english_and_french():
    assert market_prices.gas_price_reply(with_gas(), "en")["answer"].startswith("Gas prices on the suppliers'")
    assert "(fixed 12 months)" in market_prices.gas_price_reply(with_gas(), "en")["answer"]
    french = market_prices.gas_price_reply(with_gas(), "fr")["answer"]
    assert french.startswith("Prix du gaz") and "7,92 c€/kWh" in french


def test_no_gas_cards_gives_no_reply():
    assert market_prices.gas_price_reply(market()) is None


def test_api_answers_gas_price_from_the_cards(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    reply = ask(api, "Hey kan je mij de prijs van gas geven")
    assert reply["processing"] == "market-data"
    assert "TotalEnergies myEssential Variabel: 7,92 cent/kWh" in reply["answer"]
    api[2].assert_not_awaited()


def test_api_gas_question_without_cards_keeps_the_source_path(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=market()))
    assert ask(api, "wat is de gasprijs vandaag")["answer"] == "Bestaand antwoord"
