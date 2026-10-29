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
    assert facts["advance_lines"]["vat"] == "excl. btw"
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


def _facts_from_text(text: str):
    from luminus_harness.photo_invoice import chunks_from_text
    chunks = chunks_from_text(text, source="test.pdf")
    return chunks, analyze_invoice_chunks(chunks)["facts"]


def _table(*rows_at: tuple[int, str]) -> str:
    """A bill text with each table row placed at a character position (chunks are 1000 long, 160 overlap)."""
    text = "Uw jaarafrekening Omschrijving Beginstand Eindstand Verbruik "
    for position, row in rows_at:
        text += "x" * (position - len(text) - 1) + " " + row + " "
    return text + "y" * max(0, 1100 - len(text)) + " einde"


DAY = "Elektriciteit dag 12.000 kWh 13.600 kWh 1.600 kWh"
NIGHT = "Elektriciteit nacht 5.000 kWh 5.900 kWh 900 kWh"


def test_a_meter_table_split_over_two_chunks_adds_both_rows():
    chunks, facts = _facts_from_text(_table((700, DAY), (1010, NIGHT)))
    assert len(chunks) == 2
    assert not any(DAY in chunk.text and NIGHT in chunk.text for chunk in chunks)
    assert facts["electricity_kwh"]["value"] == 2500


def test_a_row_repeated_by_the_chunk_overlap_is_counted_once():
    chunks, facts = _facts_from_text(_table((700, DAY), (880, NIGHT)))
    assert NIGHT in chunks[0].text and NIGHT in chunks[1].text and DAY not in chunks[1].text
    assert facts["electricity_kwh"]["value"] == 2500


def test_two_connections_with_the_same_amount_are_both_kept():
    chunks, facts = _facts_from_text(
        "Uw maandelijks voorschot Elektriciteit (EAN 000000000000000001) okt 2026 60,00 € "
        "Elektriciteit (EAN 000000000000000002) okt 2026 60,00 € Gas (EAN 000000000000000003) okt 2026 90,00 € "
        "Totaal (excl. BTW) 210,00 € Totaal factuurbedrag (incl. BTW) 222,60 €"
    )
    assert [row["amount"] for row in facts["advance_lines"]["value"]] == [60, 60, 90]
    assert facts["advance_lines"]["vat"] == "excl. btw"


def test_two_meters_with_identical_readings_are_both_counted():
    _, facts = _facts_from_text(
        "Uw jaarafrekening Meter 1 Elektriciteit dag 1.000 kWh 2.000 kWh 1.000 kWh "
        "Meter 2 Elektriciteit dag 1.000 kWh 2.000 kWh 1.000 kWh"
    )
    assert facts["electricity_kwh"]["value"] == 2000


def test_direct_debit_wins_over_a_printed_due_date():
    chunks, facts = _facts_from_text(
        "Uw maandelijks voorschot Elektriciteit okt 2026 63,21 € Totaal factuurbedrag (incl. BTW) 67,00 € "
        "Vervaldatum: 31/10/2026 Uw bank zal op 31.10.2026 het bedrag van € 67,00 overschrijven."
    )
    assert facts["due_date"]["direct_debit"]
    assert "je bank betaalt" in answer_uploaded_pdf_question("Hoeveel moet ik betalen?", chunks, facts)["answer"]


@pytest.mark.parametrize("text, vat", [
    ("Elektriciteit okt 2026 63,21 € Gas okt 2026 105,66 € Totaal (excl. BTW) 168,87 € BTW 10,13 € "
     "Totaal factuurbedrag (incl. BTW) 179,00 €", "excl. btw"),
    ("Elektriciteit okt 2026 70,00 € Gas okt 2026 109,00 € Totaal factuurbedrag inclusief btw 179,00 € "
     "Te betalen bedrag: € 179,00", "incl. btw"),
    ("Elektriciteit okt 2026 70,00 € Gas okt 2026 100,00 € Te betalen bedrag: € 179,00", None),
])
def test_advance_lines_keep_the_bills_own_vat_treatment(text, vat):
    chunks, facts = _facts_from_text("Uw maandelijks voorschot " + text)
    assert facts["advance_lines"]["vat"] == vat
    answer = answer_uploaded_pdf_question("Hoeveel stroom heb ik verbruikt?", chunks, facts)["answer"]
    assert ("btw" in answer) == (vat is not None)
    if vat:
        assert vat in answer
