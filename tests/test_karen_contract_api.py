"""/api/contract-options and /api/contract-check: the contract check from three answers or an uploaded bill.
market.json is never downloaded: the tariff cards are a fixed test set."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from luminus_harness.contract_savings import compare_answers, contract_options

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"
CARDS = {
    "as_of": "2026-10-19",
    "suppliers": [
        {"name": "Alfa", "status": "verified", "product_name": "Alfa Flex", "price_per_kwh_eur": 0.19,
         "standing_charge_amount": 25.0, "standing_charge_unit": "per_year"},
        {"name": "Beta", "status": "verified", "product_name": "Beta Vast", "contract_type": "fixed", "duration_months": 36,
         "price_per_kwh_eur": 0.14, "standing_charge_amount": 75.0, "standing_charge_unit": "per_year"},
    ],
    "gas_suppliers": [
        {"name": "Alfa", "status": "verified", "product_name": "Alfa Gas Flex", "price_per_kwh_eur": 0.085,
         "standing_charge_amount": 20.0, "standing_charge_unit": "per_year"},
    ],
}


@pytest.fixture()
def client(monkeypatch):
    import energy_voice_app

    async def market_data():
        return {"contracts": CARDS}

    monkeypatch.setattr(energy_voice_app, "load_market_data", market_data)
    monkeypatch.setattr(energy_voice_app, "_api_rate_limiter",
                        energy_voice_app.SlidingWindowRateLimiter(requests=30, window_seconds=60, max_clients=10))
    return TestClient(energy_voice_app.api_app)


def test_options_list_the_verified_products_per_energy(client):
    body = client.get("/api/contract-options").json()
    assert body["tariffs_as_of"] == "2026-10-19"
    assert [option["label"] for option in body["options"]["electricity"]] == ["Alfa Flex", "Beta Vast"]
    assert body["options"]["gas"][0]["supplier"] == "Alfa"


def test_three_answers_give_the_saving(client):
    response = client.post("/api/contract-check", json={
        "postcode": "9000", "meter": "single",
        "electricity": {"usage_kwh": 3200, "supplier": "Alfa", "product": "Alfa Flex"},
    })
    assert response.status_code == 200
    body = response.json()
    electricity = body["energies"]["electricity"]
    assert body["based_on"] == "answers" and body["region"] == "flanders"
    assert electricity["current"]["yearly_eur"] == "633.00"  # 3.200 × 0,19 + 25
    assert electricity["best"]["label"] == "Beta Vast"  # 3.200 × 0,14 + 75 = 523,00
    assert electricity["saving_eur_per_year"] == "110.00"
    assert [warning["code"] for warning in electricity["warnings"]] == ["price_from_chosen_product"]
    assert "regelt alles" in body["switching"]["text"] and body["switching"]["sources"]


def test_unknown_product_shows_offers_without_a_saving(client):
    body = client.post("/api/contract-check", json={"postcode": "9000", "electricity": {"usage_kwh": 3200}}).json()
    electricity = body["energies"]["electricity"]
    assert electricity["status"] == "offers_only" and electricity["saving_eur_per_year"] is None
    assert body["total"] is None


def test_answers_for_a_day_night_meter_or_brussels_show_no_saving(client):
    day_night = client.post("/api/contract-check", json={"postcode": "9000", "meter": "day_night", "electricity": {"usage_kwh": 3200}})
    assert day_night.json()["energies"]["electricity"]["reason"] == "day_night_meter"
    brussels = client.post("/api/contract-check", json={"postcode": "1050", "electricity": {"usage_kwh": 3200}})
    assert brussels.json()["energies"]["electricity"]["reason"] == "region_not_covered"


def test_uploaded_bill_is_checked_with_its_evidence_and_a_typed_usage_is_used(client):
    pdf = (FIXTURES / "27-nl-contract-enkelvoudige-meter.pdf").read_bytes()
    session = client.post("/api/upload", files={"file": ("27.pdf", pdf, "application/pdf")}).json()["session_id"]
    body = client.post("/api/contract-check", json={"session_id": session}).json()
    electricity = body["energies"]["electricity"]
    assert body["based_on"] == "invoice"
    assert electricity["current"]["yearly_eur"] == "821.50" and electricity["current"]["price_source"] == "invoice"
    assert "Jane Doe" not in str(electricity["current"]["evidence"])  # the customer's name never comes back
    assert electricity["saving_eur_per_year"] == "256.50"  # 821,50 − Beta 3.500 × 0,14 + 75 = 565,00
    assert body["v_check"] is None  # bill 27 prints no V-check code
    typed = client.post("/api/contract-check", json={"session_id": session, "electricity": {"usage_kwh": 2000}}).json()
    assert typed["energies"]["electricity"]["usage_kwh"] == "2000"


@pytest.mark.parametrize("body, status", [
    ({}, 422),  # neither a bill nor answers
    ({"postcode": "90000", "electricity": {"usage_kwh": 3200}}, 422),
    ({"electricity": {"usage_kwh": 0}}, 422),
    ({"meter": "zonnepanelen", "electricity": {"usage_kwh": 3200}}, 422),
    ({"session_id": "x" * 32}, 404),  # expired or unknown bill session
    ({"electricity": {"usage_kwh": 3200}, "email": "a@b.c"}, 422),  # nothing else is accepted
])
def test_invalid_requests(client, body, status):
    assert client.post("/api/contract-check", json=body).status_code == status


def test_options_and_answers_without_the_api():
    assert contract_options(CARDS)["electricity"][1]["contract_type"] == "fixed"
    result = compare_answers(CARDS, postcode=None, meter=None, energies={"gas": {"usage_kwh": 10000, "supplier": "Alfa", "product": "Alfa Gas Flex"}})
    gas = result["energies"]["gas"]
    assert gas["status"] == "already_cheapest"  # the only gas card is the one picked
    assert "region_unknown" in [warning["code"] for warning in gas["warnings"]]


def test_uploaded_bill_with_a_v_check_code(client):
    pdf = (FIXTURES / "30-nl-contract-tabel-productwissel.pdf").read_bytes()
    session = client.post("/api/upload", files={"file": ("30.pdf", pdf, "application/pdf")}).json()["session_id"]
    body = client.post("/api/contract-check", json={"session_id": session}).json()
    assert body["v_check"]["codes"][0]["code"] == "KNGP250400030125"
    assert body["v_check"]["codes"][0]["usage_kwh"] == "3400"


def test_answers_have_no_v_check(client):
    body = client.post("/api/contract-check", json={"postcode": "9000", "electricity": {"usage_kwh": 3200}}).json()
    assert body["v_check"] is None


def test_answers_without_a_meter_warn_that_the_saving_is_for_a_single_meter(client):
    body = client.post("/api/contract-check", json={
        "postcode": "9000", "electricity": {"usage_kwh": 3200, "supplier": "Alfa", "product": "Alfa Flex"},
    }).json()
    electricity = body["energies"]["electricity"]
    assert electricity["saving_eur_per_year"] == "110.00"
    assert "meter_unknown" in [warning["code"] for warning in electricity["warnings"]]


def test_the_screen_states_the_real_retention_and_size_limit():
    import energy_voice_app

    screen = (Path(__file__).parent.parent / "energyagent-karen-prototype" / "ContractCheck.tsx").read_text(encoding="utf-8")
    assert f"maximaal {energy_voice_app.UPLOAD_SESSION_TTL_SECONDS // 60} minuten" in screen
    assert f"max. {energy_voice_app.MAX_PDF_BYTES // (1024 * 1024)} MB" in screen
    assert "niet bewaard" not in screen
