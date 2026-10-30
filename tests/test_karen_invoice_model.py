"""The language-model invoice reader: masked text out, only printed and consistent values back."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from luminus_harness import invoice_model
from luminus_harness.invoice_analysis import analyze_invoice_chunks
from luminus_harness.invoice_model import mask_invoice_text, verified_model_facts
from luminus_harness.knowledge import KnowledgeChunk, load_pdf_chunks_from_bytes

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"

# Fictional person, a valid-looking but made-up IBAN and EAN.
BILL = (
    "Dhr. Jan Peeters Kerkstraat 12 bus 3, 2000 Antwerpen Klantnummer: 123456789 "
    "Factuurnummer: 2026-0042 IBAN BE68 5390 0754 7034 EAN 54 1448 8200 4455 6677 "
    "Uw afrekening Zonnig Stroom NV Periode 01/02/2025 - 31/01/2026 "
    "Kosten in totaal € 1.111,10 Reeds gefactureerd -€ 1.000,00 Saldo € 111,10 "
    "Stroom dag 1.400 kWh nacht 600 kWh Betalen voor 20/02/2026 +++123/4567/89012+++"
)


def _chunks(text: str) -> list[KnowledgeChunk]:
    return [KnowledgeChunk(source="bill.pdf", page=1, text=text, urls=())]


def test_masking_hides_the_person_and_keeps_the_numbers() -> None:
    masked = mask_invoice_text(BILL, ("Jan Peeters",))
    for private in ("Peeters", "Kerkstraat", "2000 Antwerpen", "123456789", "2026-0042", "BE68", "1448 8200", "4567/89012"):
        assert private not in masked
    for kept in ("Zonnig Stroom NV", "01/02/2025 - 31/01/2026", "€ 1.111,10", "-€ 1.000,00", "€ 111,10", "1.400 kWh", "600 kWh", "20/02/2026"):
        assert kept in masked


def test_masking_keeps_dates_that_look_like_a_postcode() -> None:
    text = "Adresse de livraison: Rue de la Loi 16, 1000 Bruxelles Électricité 03/10/2017 - 30/06/2018 EAN 541448820044556677"
    masked = mask_invoice_text(text)
    assert "Loi" not in masked and "1000 Bruxelles" not in masked
    assert "03/10/2017 - 30/06/2018" in masked


MODEL_ANSWER = {
    "supplier": "Zonnig Stroom NV",
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
    assert facts["supplier"]["page"] == 1 and "Zonnig" in facts["supplier"]["excerpt"]
    # "3.000" kWh gas is not on the bill: invented, so dropped.
    assert "gas_kwh" not in facts


def test_a_number_inside_a_longer_number_is_not_printed() -> None:
    facts = verified_model_facts(_chunks("Totaal te betalen € 1.111,10"), {"amount_due": {"amount": "111,10"}})
    assert facts == {}


def test_model_amounts_that_do_not_add_up_are_dropped() -> None:
    wrong = dict(MODEL_ANSWER, amount_due={"amount": "600", "refund": False})
    analysis = analyze_invoice_chunks(_chunks(BILL), verified_model_facts(_chunks(BILL), wrong))
    facts = analysis["facts"]
    assert facts["amount_due"] is None and facts["total_energy_cost"] is None and facts["advances_paid"] is None


def test_model_fills_only_what_the_rules_missed() -> None:
    chunks = load_pdf_chunks_from_bytes((FIXTURES / "01-nl-afrekening-gegroepeerd.pdf").read_bytes(), source="01.pdf")
    answer = dict(MODEL_ANSWER, supplier="Kanaal Energie", amount_due={"amount": "1.000,00", "refund": False})
    facts = analyze_invoice_chunks(chunks, verified_model_facts(chunks, answer))["facts"]
    assert str(facts["amount_due"]["value"]) == "234.56"
    assert "read_by" not in facts["amount_due"]


def test_upload_sends_masked_text_only_when_the_rules_fall_short(monkeypatch) -> None:
    import energy_voice_app

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-present")
    sent: list[str] = []

    def model(masked: str) -> str:
        sent.append(masked)
        return json.dumps(MODEL_ANSWER)

    monkeypatch.setattr(invoice_model, "_ask_model", model)
    monkeypatch.setattr(energy_voice_app, "_pdf_chunks", lambda data, name: _chunks(BILL))
    client = TestClient(energy_voice_app.api_app)

    payload = client.post("/api/upload", files={"file": ("bill.pdf", b"%PDF-1.4", "application/pdf")}).json()
    assert len(sent) == 1 and "Peeters" not in sent[0] and "BE68" not in sent[0]
    assert payload["facts"]["amount_due"]["value"] is not None
    assert "111,10" in payload["answer"] and "Zonnig Stroom NV" in payload["answer"]

    full = (FIXTURES / "13-nl-jaarafrekening-nieuw-voorschot-tabel.pdf").read_bytes()
    monkeypatch.setattr(energy_voice_app, "_pdf_chunks", lambda data, name: load_pdf_chunks_from_bytes(data, source=name))
    client.post("/api/upload", files={"file": ("13.pdf", full, "application/pdf")})
    assert len(sent) == 1


def test_switch_keeps_invoices_on_the_rules(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-present")
    monkeypatch.setenv("KAREN_INVOICE_MODEL", "0")
    assert invoice_model.read_invoice_with_model(_chunks(BILL)) == {}
