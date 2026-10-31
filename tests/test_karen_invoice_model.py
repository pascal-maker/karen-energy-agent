"""The language-model invoice reader: masked text out, only printed and consistent values back."""

import asyncio
import json
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from luminus_harness import invoice_model
from luminus_harness.invoice_analysis import analyze_invoice_chunks
from luminus_harness.invoice_model import mask_invoice_text, model_passages, needs_model, verified_model_facts
from luminus_harness.knowledge import KnowledgeChunk, load_pdf_chunks_from_bytes
from luminus_harness.rate_limit import SlidingWindowRateLimiter

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"

# Fictional person, a valid-looking but made-up IBAN and EAN.
BILL = (
    "Dhr. Jan Peeters Kerkstraat 12 bus 3, 2000 Antwerpen Klantnummer: 123456789 "
    "Factuurnummer: 2026-0042 IBAN BE68 5390 0754 7034 EAN 54 1448 8200 4455 6677 "
    "Uw afrekening Kanaal Energie Periode 01/02/2025 - 31/01/2026 "
    "Kosten in totaal € 1.111,10 Reeds gefactureerd -€ 1.000,00 Saldo € 111,10 "
    "Stroom dag 1.400 kWh nacht 600 kWh Betalen voor 20/02/2026 +++123/4567/89012+++"
)


def _chunks(text: str) -> list[KnowledgeChunk]:
    return [KnowledgeChunk(source="bill.pdf", page=1, text=text, urls=())]


def test_masking_hides_the_person_and_keeps_the_numbers() -> None:
    masked = mask_invoice_text(BILL, ("Jan Peeters",))
    for private in ("Peeters", "Kerkstraat", "2000", "Antwerpen", "123456789", "2026-0042", "BE68", "1448", "4567/89012"):
        assert private not in masked
    for kept in ("Kanaal Energie", "01/02/2025 - 31/01/2026", "€ 1.111,10", "-€ 1.000,00", "€ 111,10", "1.400 kWh", "600 kWh", "20/02/2026"):
        assert kept in masked


@pytest.mark.parametrize("text, private", [
    ("Contact: 0478 12 34 56 Totaal te betalen € 89,00", ("0478", "12 34", "56")),
    ("Customer ID: 12345678 Totaal te betalen € 89,00", ("12345678", "Customer")),
    ("Klantnummer: 1234 5678 Totaal te betalen € 89,00", ("1234", "5678")),
    ("ANNA JANSSENS Facture annuelle Bakkerij Verhoeven BV Totaal te betalen € 89,00", ("ANNA", "JANSSENS", "Bakkerij", "Verhoeven")),
    ("Rue de la Loi 16, 1000 Bruxelles Totaal te betalen € 89,00", ("Loi", "16", "1000", "Bruxelles")),
    ("Dhr. Vast Totaal te betalen € 89,00 https://example.be/x?klant=77", ("Vast", "klant=77")),
])
def test_masking_fails_closed_on_names_and_identifiers(text, private) -> None:
    masked = mask_invoice_text(text)
    for value in private:
        assert value not in masked
    assert "te betalen € 89,00" in masked


def test_only_amounts_dates_and_quantities_keep_their_digits() -> None:
    masked = mask_invoice_text("Factuurdatum 13.10.2026 okt 2026 7 novembre 2018 Elektriciteit 2.708 kWh Gas 1.150 m³ btw 21% pagina 1/2 meter 403.335")
    assert "13.10.2026" in masked and "okt 2026" in masked and "7 novembre 2018" in masked
    assert "2.708 kWh" in masked and "1.150 m³" in masked and "21%" in masked
    assert "1/2" not in masked and "403.335" not in masked


def test_only_passages_around_values_are_sent() -> None:
    filler = "Algemene voorwaarden " * 60
    passages = model_passages(mask_invoice_text("Uw jaarafrekening " + filler + "Totaal te betalen € 89,00 " + filler))
    assert "€ 89,00" in passages and len(passages) < 600


MODEL_ANSWER = {
    "supplier": "Kanaal Energie",
    "invoice_type": "jaarafrekening",
    "billing_period": {"from": "01/02/2025", "to": "31/01/2026"},
    "amount_due": {"amount": "111,10", "refund": False},
    "total_energy_cost": "1.111,10",
    "advances_paid": "1.000,00",
    "electricity": {"amount": None, "unit": "kWh", "parts": ["1.400", "600"]},
    "gas": {"amount": "3.000", "unit": "kWh"},
    "new_advance": None,
    "due_date": "20/02/2026",
    "direct_debit": False,
    "advance_lines": None,
}


def test_only_printed_values_survive() -> None:
    facts = verified_model_facts(_chunks(BILL), MODEL_ANSWER)
    assert str(facts["amount_due"]["value"]) == "111.10"
    assert str(facts["total_energy_cost"]["value"]) == "1111.10"
    assert facts["electricity_kwh"]["value"] == 2000
    assert facts["billing_period"]["value"] == "01/02/2025 tot 31/01/2026"
    assert facts["supplier"]["page"] == 1 and "Kanaal" in facts["supplier"]["excerpt"]
    # "3.000" kWh gas is not on the bill: invented, so dropped.
    assert "gas_kwh" not in facts


def test_a_number_inside_a_longer_number_is_not_printed() -> None:
    assert verified_model_facts(_chunks("Totaal te betalen € 1.111,10"), {"amount_due": {"amount": "111,10"}}) == {}


def test_unit_must_be_the_printed_unit() -> None:
    chunks = _chunks("Elektriciteit consommation 50 kWh Gasverbruik: 1.150 m³")
    facts = verified_model_facts(chunks, {"electricity": {"amount": "50", "unit": "MWh"}, "gas": {"amount": "1.150", "unit": "kWh"}})
    assert facts == {}
    facts = verified_model_facts(chunks, {"electricity": {"amount": "50", "unit": "kWh"}, "gas": {"amount": "1.150", "unit": "m³"}})
    assert facts["electricity_kwh"]["value"] == 50 and facts["gas_kwh"]["unit"] == "m³"


def test_consumption_must_belong_to_its_energy() -> None:
    assert verified_model_facts(_chunks("Gasverbruik 3000 kWh"), {"electricity": {"amount": "3000", "unit": "kWh"}}) == {}
    assert verified_model_facts(_chunks("Stroom dag 600 kWh"), {"gas": {"amount": "600", "unit": "kWh"}}) == {}
    # A consumption with no energy named before it is not taken either.
    assert verified_model_facts(_chunks("Uw consommation 50 kWh"), {"electricity": {"amount": "50", "unit": "kWh"}}) == {}


def test_parts_must_be_distinct_rows() -> None:
    chunks = _chunks("Stroom dag 600 kWh")
    assert verified_model_facts(chunks, {"electricity": {"amount": None, "unit": "kWh", "parts": ["600", "600"]}}) == {}
    two_rows = _chunks("Stroom dag 600 kWh nacht 600 kWh")
    assert verified_model_facts(two_rows, {"electricity": {"amount": None, "unit": "kWh", "parts": ["600", "600"]}})["electricity_kwh"]["value"] == 1200


def test_payment_direction_must_be_printed() -> None:
    to_pay = _chunks("Totaal te betalen € 111,10")
    assert verified_model_facts(to_pay, {"amount_due": {"amount": "111,10", "refund": True}}) == {}
    refund = _chunks("Bedrag in uw voordeel € 150,00 wordt teruggestort")
    assert verified_model_facts(refund, {"amount_due": {"amount": "150,00", "refund": False}}) == {}
    assert str(verified_model_facts(refund, {"amount_due": {"amount": "150,00", "refund": True}})["amount_due"]["value"]) == "-150.00"


@pytest.mark.parametrize("printed", ["Saldo -111,10", "Saldo - € 111,10", "Saldo € -111,10", "Saldo 111,10-"])
def test_a_printed_minus_is_never_money_owed(printed) -> None:
    chunks = _chunks(printed)
    for amount in ("-111,10", "111,10"):
        assert verified_model_facts(chunks, {"amount_due": {"amount": amount, "refund": False}}) == {}
    assert str(verified_model_facts(chunks, {"amount_due": {"amount": "111,10", "refund": True}})["amount_due"]["value"]) == "-111.10"


def test_money_is_not_a_consumption() -> None:
    assert verified_model_facts(_chunks("Saldo nacht 600 kWh"), {"amount_due": {"amount": "600", "refund": False}}) == {}


def test_total_cost_must_be_a_total_incl_vat() -> None:
    chunks = _chunks("Kosten excl. btw € 100,00 Btw € 21,00")
    assert verified_model_facts(chunks, {"total_energy_cost": "100,00"}) == {}
    assert verified_model_facts(_chunks("Energiekosten € 100,00 excl. btw"), {"total_energy_cost": "100,00"}) == {}
    assert "total_energy_cost" in verified_model_facts(_chunks("Totale energiekosten € 121,00"), {"total_energy_cost": "121,00"})


def test_invoice_type_frequency_and_energy_come_from_the_invoice() -> None:
    chunks = _chunks("Uw nieuw voorschot Gas per kwartaal € 120,00")
    facts = verified_model_facts(chunks, {
        "invoice_type": "slotfactuur",
        "new_advance": [{"energy": "elektriciteit", "amount": "120,00", "frequency": "maandelijks"}],
    })
    assert "invoice_type" not in facts
    assert facts["new_advance"]["value"][0]["energy"] == "gas" and facts["new_advance"]["value"][0]["frequency"] == "per kwartaal"


def test_partial_model_amounts_are_dropped() -> None:
    chunks = _chunks("Uw afrekening Kanaal Energie Saldo € 111,10")
    model_facts = verified_model_facts(chunks, {"amount_due": {"amount": "111,10", "refund": False}})
    assert "amount_due" in model_facts
    # Without costs and advances to check it against, the model's amount does not reach the answer.
    assert analyze_invoice_chunks(chunks, model_facts)["facts"]["amount_due"] is None


def test_model_amounts_that_do_not_add_up_are_dropped() -> None:
    wrong = dict(MODEL_ANSWER, amount_due={"amount": "1.111,10", "refund": False})
    analysis = analyze_invoice_chunks(_chunks(BILL), verified_model_facts(_chunks(BILL), wrong))
    facts = analysis["facts"]
    assert facts["amount_due"] is None and facts["total_energy_cost"] is None and facts["advances_paid"] is None


def test_model_fills_only_what_the_rules_missed() -> None:
    chunks = load_pdf_chunks_from_bytes((FIXTURES / "01-nl-afrekening-gegroepeerd.pdf").read_bytes(), source="01.pdf")
    answer = dict(MODEL_ANSWER, supplier="Kanaal Energie", amount_due={"amount": "1.000,00", "refund": False})
    facts = analyze_invoice_chunks(chunks, verified_model_facts(chunks, answer))["facts"]
    assert str(facts["amount_due"]["value"]) == "234.56"
    assert "read_by" not in facts["amount_due"]


def test_a_gas_only_bill_does_not_need_electricity() -> None:
    chunks = load_pdf_chunks_from_bytes((FIXTURES / "15-nl-jaarafrekening-gas-m3.pdf").read_bytes(), source="15.pdf")
    assert not needs_model(analyze_invoice_chunks(chunks)["facts"])


def _client(monkeypatch):
    import energy_voice_app

    monkeypatch.setattr(energy_voice_app, "_api_rate_limiter", SlidingWindowRateLimiter(requests=100, window_seconds=60, max_clients=100))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-present")
    monkeypatch.setenv("KAREN_INVOICE_MODEL", "1")
    monkeypatch.setattr(energy_voice_app, "_pdf_chunks", lambda data, name: _chunks(BILL))
    return energy_voice_app, TestClient(energy_voice_app.api_app)


def test_upload_sends_masked_text_only_when_the_rules_fall_short(monkeypatch) -> None:
    energy_voice_app, client = _client(monkeypatch)
    sent: list[str] = []

    async def model(masked: str) -> str:
        sent.append(masked)
        return json.dumps(MODEL_ANSWER)

    monkeypatch.setattr(invoice_model, "_ask_model", model)
    payload = client.post("/api/upload", files={"file": ("bill.pdf", b"%PDF-1.4", "application/pdf")}).json()
    assert payload["processing"] == "masked-model"
    assert len(sent) == 1 and "Peeters" not in sent[0] and "BE68" not in sent[0]
    assert payload["facts"]["amount_due"]["value"] is not None
    assert "111,10" in payload["answer"] and "Kanaal Energie" in payload["answer"]

    full = (FIXTURES / "13-nl-jaarafrekening-nieuw-voorschot-tabel.pdf").read_bytes()
    monkeypatch.setattr(energy_voice_app, "_pdf_chunks", lambda data, name: load_pdf_chunks_from_bytes(data, source=name))
    payload = client.post("/api/upload", files={"file": ("13.pdf", full, "application/pdf")}).json()
    assert len(sent) == 1 and payload["processing"] == "local-only"


def test_unexpected_model_output_falls_back_to_the_rules(monkeypatch) -> None:
    _, client = _client(monkeypatch)
    for answer in ('{"new_advance": 42}', '{"amount_due": "111,10", "billing_period": [1]}', '{"electricity": {"parts": 7}}', "[1, 2]"):
        async def model(masked: str, answer=answer) -> str:
            return answer

        monkeypatch.setattr(invoice_model, "_ask_model", model)
        upload = client.post("/api/upload", files={"file": ("bill.pdf", b"%PDF-1.4", "application/pdf")})
        assert upload.status_code == 200 and upload.json()["answer"]


def test_a_slow_model_is_cancelled(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-present")
    monkeypatch.setenv("KAREN_INVOICE_MODEL", "1")
    monkeypatch.setattr(invoice_model, "MODEL_DEADLINE_SECONDS", 0.05)
    cancelled = []

    async def slow(masked: str) -> str:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return "{}"

    monkeypatch.setattr(invoice_model, "_ask_model", slow)
    assert asyncio.run(invoice_model.read_invoice_with_model(_chunks(BILL))) == ({}, True)
    assert cancelled == [True]


def test_model_is_off_unless_switched_on(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-present")
    monkeypatch.delenv("KAREN_INVOICE_MODEL", raising=False)
    assert asyncio.run(invoice_model.read_invoice_with_model(_chunks(BILL))) == ({}, False)
    monkeypatch.setenv("KAREN_INVOICE_MODEL", "0")
    assert asyncio.run(invoice_model.read_invoice_with_model(_chunks(BILL))) == ({}, False)
