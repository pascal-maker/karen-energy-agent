import asyncio
from datetime import date

import pytest

from luminus_harness import fuel_prices

PAGE = """
<section class="samenstelling-chart"><header class="samenstelling-chart__header">
  <h3 class="samenstelling-chart__title">Diesel - B7</h3>
  <div class="samenstelling-chart__price price-change--down">€/l 2.4440</div>
  <div class="samenstelling-chart__date">
    vanaf 2026-10-17
  </div></header></section>
<section class="samenstelling-chart"><header class="samenstelling-chart__header">
  <h3 class="samenstelling-chart__title">Gasolie verwarming (H0/H7) (<2000 L)</h3>
  <div class="samenstelling-chart__price price-change--up">€/l 1.6291</div>
  <div class="samenstelling-chart__date">
    vanaf 2026-10-27
  </div></header></section>
<section class="samenstelling-chart"><header class="samenstelling-chart__header">
  <h3 class="samenstelling-chart__title">Gasolie verwarming (H0/H7) (&ge; 2000 L)</h3>
  <div class="samenstelling-chart__price price-change--up">€/l 1.5860</div>
  <div class="samenstelling-chart__date">
    vanaf 2026-10-27
  </div></header></section>
"""


def test_parses_every_product_with_price_date_and_direction():
    rows = fuel_prices.parse_prices(PAGE)
    assert rows["Gasolie verwarming (H0/H7) (<2000 L)"] == {"price": 1.6291, "from": "2026-10-27", "change": "up"}
    assert rows["Gasolie verwarming (H0/H7) (≥ 2000 L)"]["price"] == 1.586
    assert rows["Diesel - B7"]["change"] == "down"


@pytest.mark.parametrize("question, product", [
    ("Wat is de prijs van olie vandaag", "heating_oil"),
    ("Wat kost olie", "heating_oil"),
    ("Wat kost mazout per liter?", "heating_oil"),
    ("Hoeveel kost diesel vandaag", "diesel"),
    ("Wat kost een liter benzine", "petrol"),
    ("prijs propaan", "propane"),
    ("Waarom stijgt de stookolieprijs?", None),
    ("Hoe lang blijft olie zo duur?", None),
    ("Wat kost stroom vandaag?", None),
    ("Waar komt onze olie vandaan?", None),
])
def test_recognises_todays_fuel_price_questions_only(question, product):
    assert fuel_prices.fuel_product(question) == product


def test_heating_oil_answer_says_when_the_new_price_starts():
    rows = fuel_prices.parse_prices(PAGE)
    reply = fuel_prices.fuel_reply("heating_oil", rows, "Wat is de prijs van olie vandaag", today=date(2026, 10, 26))
    assert reply["answer"].startswith("Vanaf morgen is de officiële maximumprijs voor stookolie (mazout) **€ 1,629 per liter**")
    assert "€ 1,586" in reply["answer"] and "Ruwe olie (Brent)" in reply["answer"]
    assert reply["sources"][0]["url"] == fuel_prices.SOURCE_URL
    assert "1.6291" in reply["sources"][0]["excerpt"]
    same_day = fuel_prices.fuel_reply("heating_oil", rows, "Wat kost mazout?", today=date(2026, 10, 27))
    assert same_day["answer"].startswith("Vandaag is") and "Brent" not in same_day["answer"]


def test_no_answer_when_the_page_cannot_be_read(monkeypatch):
    monkeypatch.setattr(fuel_prices, "_cache", {"at": 0.0, "rows": None})
    monkeypatch.setattr(fuel_prices, "_download", lambda: (_ for _ in ()).throw(OSError("offline")))
    assert asyncio.run(fuel_prices.answer_fuel_price_question("Wat kost mazout vandaag?")) is None


def test_api_answers_oil_price_from_the_official_page(monkeypatch):
    from fastapi.testclient import TestClient
    import energy_voice_app

    monkeypatch.setattr(fuel_prices, "_cache", {"at": 0.0, "rows": None})
    monkeypatch.setattr(fuel_prices, "_download", lambda: PAGE)

    async def no_route(question):
        return "small_talk"

    monkeypatch.setattr(energy_voice_app, "route_question", no_route)
    response = TestClient(energy_voice_app.app).post("/api/answer", json={"question": "Wat kost olie"})
    assert response.status_code == 200
    body = response.json()
    assert body["processing"] == "fuel-prices" and "€ 1,629" in body["answer"]
