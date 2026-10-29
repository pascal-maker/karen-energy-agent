"""Karen's invoice reader on 20 fictional bills (tests/fixtures/invoices, expected values in expected.json)."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from luminus_harness.invoice_analysis import analyze_invoice_chunks, answer_uploaded_pdf_question
from luminus_harness.knowledge import load_pdf_chunks_from_bytes

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"
INVOICES = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))["invoices"]


def _read(name: str):
    chunks = load_pdf_chunks_from_bytes((FIXTURES / name).read_bytes(), source=name)
    return chunks, analyze_invoice_chunks(chunks)["facts"]


def _value(key: str, fact):
    if fact is None:
        return None
    value = fact["value"]
    if key == "advance_lines":
        return {row["energy"]: str(row["amount"]) for row in value}
    if key == "new_advance":
        return [str(row["new"]) for row in value]
    if key in ("electricity_kwh", "gas_kwh"):
        return str(int(value))
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal("0.01")))
    return value


@pytest.mark.parametrize("expected", INVOICES, ids=[invoice["file"] for invoice in INVOICES])
def test_reader_finds_exactly_the_facts_on_each_bill(expected):
    _, facts = _read(expected["file"])
    for key, want in expected.items():
        if key in ("file", "gas_unit", "direct_debit"):
            continue
        assert _value(key, facts.get(key)) == want, key
    if expected.get("gas_unit"):
        assert facts["gas_kwh"]["unit"] in ("m3", "m³")
    if expected.get("direct_debit"):
        assert facts["due_date"]["direct_debit"]


def test_an_advance_bill_says_what_it_is_instead_of_not_found():
    chunks, facts = _read("11-nl-voorschot-domiciliering.pdf")
    answer = answer_uploaded_pdf_question("Hoeveel stroom heb ik verbruikt?", chunks, facts)["answer"]
    assert answer.startswith("Dit is een voorschotfactuur")
    assert "elektriciteit € 63,21, gas € 105,66" in answer
    assert "jaarafrekening" in answer
    paid = answer_uploaded_pdf_question("Hoeveel moet ik betalen?", chunks, facts)["answer"]
    assert paid == (
        "Deze factuur bedraagt € 179,00 (PDF-pagina 1). "
        "Je hoeft niets te doen: je bank betaalt dit bedrag op 31.10.2026 via domiciliëring (PDF-pagina 1)."
    )


def test_a_single_new_advance_and_a_refund_are_answered_directly():
    chunks, facts = _read("03-nl-tegoed.pdf")
    assert answer_uploaded_pdf_question("Wat is mijn nieuwe voorschot?", chunks, facts)["answer"] == (
        "Je nieuwe voorschot volgens deze factuur: € 70,00 maandelijks (PDF-pagina 1)."
    )
    assert answer_uploaded_pdf_question("Hoeveel moet ik nog betalen?", chunks, facts)["answer"] == (
        "Volgens deze factuur krijg je € 150,00 terug (PDF-pagina 1)."
    )
