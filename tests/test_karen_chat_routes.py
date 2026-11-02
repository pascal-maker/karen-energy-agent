"""Questions the price lists used to swallow, follow-ups Jev is unsure about, and small-talk tone."""

from unittest.mock import AsyncMock

import pytest

from luminus_harness import gemma_answer, market_prices
from tests.test_karen_market_prices import TODAY, api, market  # noqa: F401  (api is a fixture)


def with_named_cards():
    data = market()
    data["contracts"]["suppliers"] += [
        {"name": "Engie", "status": "verified", "product_name": "Engie Direct vast", "contract_type": "fixed",
         "price_per_kwh_eur": 0.205, "standing_charge_amount": 60, "standing_charge_unit": "per_year"},
        {"name": "Luminus", "status": "verified", "product_name": "Luminus BasicFix", "contract_type": "fixed",
         "price_per_kwh_eur": 0.21},
    ]
    return data


@pytest.mark.parametrize("question", [
    "wanneer is stroom het goedkoopst op de dag", "op welk uur is elektriciteit het goedkoopst",
    "hoe laat is stroom goedkoop vandaag", "when is electricity cheapest today", "quand l'électricité est-elle la moins chère",
])
def test_cheapest_time_questions_are_recognised(question):
    assert market_prices.is_cheapest_time_question(question)


@pytest.mark.parametrize("question", [
    "wat is de goedkoopste leverancier", "wanneer krijg ik mijn jaarafrekening", "wat kost stroom vandaag",
])
def test_other_questions_are_not_cheapest_time_questions(question):
    assert not market_prices.is_cheapest_time_question(question)


def test_the_cheapest_hours_come_first_with_what_they_mean_for_a_contract():
    reply = market_prices.cheapest_time_reply(market(), "nl")
    assert reply["answer"].splitlines() == [
        "Vandaag is stroom op de groothandelsmarkt het goedkoopst rond 0u, 3u en 4u (de dag loopt van 17,6 tot 29,8 cent per kWh).",
        "- **Dynamisch contract:** dan volgt je prijs deze uren, dus loont het om je wasmachine of auto dan te laten draaien.",
        "- **Vast of variabel contract:** dan betaal je elk uur van de dag hetzelfde, behalve met een dag- en nachtmeter: "
        "dan is stroom 's nachts en in het weekend goedkoper.",
    ]
    assert reply["processing"] == "market-data"


@pytest.mark.parametrize("question, verdict", [
    ("ik verbruik 3500 kwh per jaar is dat veel", "ongeveer wat een gemiddeld gezin van 3 personen verbruikt"),
    ("is 1800 kWh stroom per jaar normaal", "minder dan een gemiddeld gezin van 2 personen"),
    ("we verbruiken 6.200 kwh elektriciteit, is dat veel?", "meer dan een gemiddeld gezin van 4 personen"),
])
def test_an_electricity_usage_question_gets_a_yardstick(question, verdict):
    reply = market_prices.usage_reply(question)
    assert reply is not None and verdict in reply["answer"]
    assert "- **Gemiddeld volgens de VREG:** 2 personen 2.350 kWh, 3 personen 3.200 kWh, 4 personen 4.000 kWh." in reply["answer"]


@pytest.mark.parametrize("question", [
    "ik verbruik 17000 kwh gas is dat veel", "wat kost 3500 kwh", "hoeveel kwh verbruikt een warmtepomp",
])
def test_other_usage_questions_keep_their_route(question):
    assert market_prices.usage_reply(question) is None


def test_two_named_suppliers_are_compared_on_their_own_cards():
    reply = market_prices.named_comparison_reply("wat is goedkoper luminus of engie", with_named_cards(), "nl")
    lines = reply["answer"].splitlines()
    assert lines[0] == "Zo verhouden Luminus en Engie zich op hun huidige tariefkaarten (energieprijs incl. 6% btw, zonder netkosten):"
    assert "• Luminus BasicFlex Online (variabel): 19,2 cent/kWh" in lines
    assert "• Engie Direct vast (vast): 20,5 cent/kWh, vaste vergoeding € 60 per jaar" in lines
    assert "• Luminus BasicFix (vast): 21,0 cent/kWh" in lines
    assert "Engie heeft geen nagekeken variabele tariefkaart." in reply["answer"]
    assert lines[-1].startswith("Per kWh is Luminus BasicFlex Online het goedkoopst.")
    assert market_prices.named_comparison_reply("wat kost luminus", with_named_cards(), "nl") is None
    assert market_prices.named_comparison_reply("is luminus of engie beter voor klantendienst", with_named_cards(), "nl") is None


def test_the_api_answers_these_before_the_price_lists(api, monkeypatch):  # noqa: F811
    import energy_voice_app as app

    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_named_cards()))
    monkeypatch.setattr(app, "route_question", AsyncMock(return_value="compare"))
    ask = lambda q: api[0].post("/api/answer", json={"question": q}).json()
    assert ask("wanneer is stroom het goedkoopst op de dag")["answer"].startswith("Vandaag is stroom op de groothandelsmarkt")
    assert "gezin van 3 personen" in ask("ik verbruik 3500 kwh per jaar is dat veel")["answer"]
    assert ask("wat is goedkoper luminus of engie")["answer"].startswith("Zo verhouden Luminus en Engie")


def test_an_unsure_route_still_answers_the_rewritten_follow_up(api, monkeypatch):  # noqa: F811
    import energy_voice_app as app

    monkeypatch.setattr(app, "standalone_question", AsyncMock(return_value="Kost van leverancier veranderen iets?"))
    monkeypatch.setattr(app, "route_question", AsyncMock(return_value=None))
    monkeypatch.setattr(app, "answer_contract_check", lambda question: None)
    history = [{"question": "hoe verander ik van leverancier", "answer": "Je sluit een contract af bij je nieuwe leverancier."}]
    api[0].post("/api/answer", json={"question": "kost dat iets", "history": history})
    assert api[2].await_args.args[0] == "Kost van leverancier veranderen iets?"


def test_small_talk_names_the_topics_only_when_it_helps():
    instruction = gemma_answer._SMALL_TALK_INSTRUCTION
    assert "Noem alleen bij een eerste begroeting of een vraag buiten energie" in instruction
    assert "Vraag niet hoe het met de gebruiker gaat als die dat zelf niet vroeg" in instruction
