from decimal import Decimal

from luminus_harness.invoice_analysis import analyze_invoice_chunks, answer_uploaded_pdf_question
from luminus_harness.knowledge import KnowledgeChunk
from luminus_harness.rate_limit import SlidingWindowRateLimiter


def test_local_invoice_analysis_extracts_page_linked_facts_and_reconciles_clear_totals() -> None:
    chunks = [
        KnowledgeChunk(
            page=2,
            source="sample-bill.pdf",
            urls=(),
            text=(
                "Leverancier: Luminus. Factuurperiode: 01/01/2025 tot 31/12/2025. "
                "Elektriciteitsverbruik: 2.340 kWh. Totale energiekosten: € 1.234,56. "
                "Totaal voorschotten reeds aangerekend: € 900,00."
            ),
        )
    ]

    analysis = analyze_invoice_chunks(chunks)

    assert analysis["facts"]["supplier"]["value"] == "Luminus"
    assert analysis["facts"]["billing_period"]["value"] == "01/01/2025 tot 31/12/2025"
    assert analysis["facts"]["electricity_kwh"]["value"] == Decimal("2340")
    assert analysis["facts"]["total_energy_cost"]["value"] == Decimal("1234.56")
    assert analysis["facts"]["advances_paid"]["value"] == Decimal("900.00")
    assert analysis["facts"]["calculated_difference"]["value"] == Decimal("334.56")
    assert all(fact["page"] == 2 and fact["excerpt"] for fact in analysis["facts"].values() if fact)


def test_local_invoice_analysis_does_not_calculate_without_both_labeled_amounts() -> None:
    chunks = [
        KnowledgeChunk(
            page=1,
            source="sample-bill.pdf",
            urls=(),
            text="Te betalen saldo: € 125,00. Er staan ook meterreferenties 2025 en 2026.",
        )
    ]

    analysis = analyze_invoice_chunks(chunks)

    assert analysis["facts"]["amount_due"]["value"] == Decimal("125.00")
    assert analysis["facts"]["calculated_difference"] is None
    assert "- Rekenverschil: niet betrouwbaar gevonden." in analysis["answer"]


def test_local_invoice_analysis_labels_negative_difference_as_credit() -> None:
    chunks = [
        KnowledgeChunk(
            page=1,
            source="sample-bill.pdf",
            urls=(),
            text=(
                "Totale energiekosten: € 800,00. "
                "Totaal voorschotten reeds aangerekend: € 900,00."
            ),
        )
    ]

    analysis = analyze_invoice_chunks(chunks)

    assert analysis["facts"]["calculated_difference"]["value"] == Decimal("-100.00")
    assert "Rekenverschil (voorschotten hoger dan kosten): € 100,00" in analysis["answer"]
    assert "bevestigt niet op zichzelf het uiteindelijke afrekensaldo" in analysis["answer"]


def test_local_invoice_analysis_does_not_treat_a_due_date_as_an_amount() -> None:
    chunks = [
        KnowledgeChunk(
            page=1,
            source="sample-bill.pdf",
            urls=(),
            text="Te betalen vóór 01/05/2025. Het bedrag staat niet leesbaar op deze pagina.",
        )
    ]

    analysis = analyze_invoice_chunks(chunks)

    assert analysis["facts"]["amount_due"] is None


def test_uploaded_pdf_follow_up_returns_only_retrieved_local_evidence() -> None:
    chunks = [
        KnowledgeChunk(
            page=3,
            source="sample-bill.pdf",
            urls=(),
            text="Totaal voorschotten reeds aangerekend: € 900,00 voor elektriciteit.",
        ),
        KnowledgeChunk(
            page=1,
            source="sample-bill.pdf",
            urls=(),
            text="Dit is de algemene informatie over verhuis en klantendienst.",
        ),
    ]

    result = answer_uploaded_pdf_question("Hoeveel voorschotten zijn aangerekend?", chunks)

    assert result["sources"]
    assert result["sources"][0]["detail"] == "PDF · pagina 3"
    assert "€ 900,00" in result["sources"][0]["excerpt"]
    assert "€ 900,00" in result["answer"]


def test_rate_limiter_evicts_expired_and_caps_unique_clients() -> None:
    limiter = SlidingWindowRateLimiter(requests=2, window_seconds=60, max_clients=2)

    assert limiter.allow("client-a", 0)
    assert limiter.allow("client-b", 0)
    assert limiter.allow("client-c", 1)
    assert limiter.tracked_clients == 2
    assert limiter.allow("client-a", 61)
    assert limiter.tracked_clients == 1


def test_rate_limiter_rejects_excess_requests_inside_window() -> None:
    limiter = SlidingWindowRateLimiter(requests=2, window_seconds=60, max_clients=2)

    assert limiter.allow("client-a", 0)
    assert limiter.allow("client-a", 1)
    assert not limiter.allow("client-a", 2)
    assert limiter.allow("client-a", 61)


def _synthetic_bill_a() -> list[KnowledgeChunk]:
    return [
        KnowledgeChunk(page=1, source="bill-a.pdf", urls=(), text=(
            "Leverancier: Luminus Factuurperiode: 01/01/2025 tot 31/12/2025 Elektriciteitsverbruik: 3.500 kWh"
        )),
        KnowledgeChunk(page=2, source="bill-a.pdf", urls=(), text=(
            "Totale energiekosten: 1.200,00 EUR Totaal voorschotten reeds aangerekend: 900,00 EUR "
            "Totaal te betalen: 300,00 EUR"
        )),
    ]


def test_pdf_follow_ups_answer_directly_from_the_facts_read_at_upload() -> None:
    chunks = _synthetic_bill_a()
    facts = analyze_invoice_chunks(chunks)["facts"]

    cases = {
        "Hoeveel moet ik nog betalen?": "Volgens deze factuur moet je nog € 300,00 betalen (PDF-pagina 2).",
        "Hoeveel voorschot heb ik betaald?": "Volgens deze factuur zijn er € 900,00 aan voorschotten aangerekend (PDF-pagina 2).",
        "Wat zijn de totale kosten?": "De totale energiekosten op deze factuur zijn € 1.200,00 (PDF-pagina 2).",
        "Wat is mijn verbruik?": "Je stroomverbruik op deze factuur is 3500 kWh (PDF-pagina 1).",
        "Over welke periode gaat dit?": "Deze factuur gaat over de periode 01/01/2025 tot 31/12/2025 (PDF-pagina 1).",
        "Wie is mijn leverancier?": "Deze factuur is van Luminus (PDF-pagina 1).",
    }
    for question, answer in cases.items():
        result = answer_uploaded_pdf_question(question, chunks, facts)
        assert result["answer"] == answer, question
        assert result["sources"] and result["sources"][0]["title"] == "bill-a.pdf"


def test_pdf_follow_up_keeps_a_calculation_apart_from_a_stated_balance() -> None:
    chunks = [KnowledgeChunk(page=2, source="bill.pdf", urls=(), text=(
        "Totale energiekosten: 1.200,00 EUR Totaal voorschotten reeds aangerekend: 900,00 EUR"
    ))]
    facts = analyze_invoice_chunks(chunks)["facts"]

    result = answer_uploaded_pdf_question("Hoeveel moet ik nog betalen?", chunks, facts)

    assert result["answer"].startswith("Ik vond op deze factuur geen duidelijk vermeld bedrag te betalen.")
    assert "€ 300,00 meer dan je voorschotten" in result["answer"]
    assert "bevestigt niet" in result["answer"]


def test_pdf_follow_up_says_when_a_fact_is_missing_and_other_questions_keep_passages() -> None:
    chunks = [KnowledgeChunk(page=1, source="bill-b.pdf", urls=(), text=(
        "Leverancier: Eneco Totaal voorschotten reeds aangerekend: 450,00 EUR"
    ))]
    facts = analyze_invoice_chunks(chunks)["facts"]

    missing = answer_uploaded_pdf_question("Wat is mijn verbruik?", chunks, facts)
    assert missing == {"answer": "Ik vond je verbruik niet duidelijk op deze factuur.", "sources": []}
    passage = answer_uploaded_pdf_question("Wat staat er over Eneco?", chunks, facts)
    assert passage["answer"].startswith("Ik vond een relevante passage op PDF-pagina 1")


def test_explain_my_invoice_gives_an_overview_of_the_facts() -> None:
    chunks = _synthetic_bill_a()
    facts = analyze_invoice_chunks(chunks)["facts"]

    result = answer_uploaded_pdf_question("Geef uitleg over de factuur", chunks, facts)

    assert result["answer"].splitlines() == [
        "Dit staat op je factuur:",
        "- **Leverancier:** Luminus (PDF-pagina 1)",
        "- **Periode:** 01/01/2025 tot 31/12/2025 (PDF-pagina 1)",
        "- **Stroomverbruik:** 3500 kWh (PDF-pagina 1)",
        "- **Totale energiekosten:** € 1.200,00 (PDF-pagina 2)",
        "- **Voorschotten aangerekend:** € 900,00 (PDF-pagina 2)",
        "- **Nog te betalen:** € 300,00 (PDF-pagina 2)",
    ]
    assert {source["detail"] for source in result["sources"]} == {"PDF · pagina 1", "PDF · pagina 2"}


def test_the_customer_name_is_read_after_a_title_and_stops_at_the_street() -> None:
    chunks = [KnowledgeChunk(page=1, source="bill.pdf", urls=(), text=(
        "DUPLICAAT Dubbel uitgereikt op aanvraag van de klant. Mevr. Anna De Smet TESTSTRAAT 1 2000 ANTWERPEN"
    ))]
    facts = analyze_invoice_chunks(chunks)["facts"]

    assert facts["customer"]["value"] == "Anna De Smet"
    result = answer_uploaded_pdf_question("Over welke persoon gaat het?", chunks, facts)
    assert result["answer"] == "Deze factuur staat op naam van Anna De Smet (PDF-pagina 1)."


def test_a_quoted_passage_is_the_part_about_the_question() -> None:
    header = "DUPLICAAT Dubbel uitgereikt op aanvraag van de klant ter vervanging van de originele factuur. " * 3
    chunks = [KnowledgeChunk(page=1, source="bill.pdf", urls=(), text=(
        header + "Je capaciteitstarief is berekend op je maandpiek van 3,2 kW. " + "Verhuis je? Geef het tijdig door. " * 3
    ))]

    result = answer_uploaded_pdf_question("Hoe is mijn capaciteitstarief berekend?", chunks)

    assert "capaciteitstarief is berekend op je maandpiek van 3,2 kW" in result["answer"]


# The layout of Luminus' published example invoice (fictional customer), as pypdf flattens its tables.
LUMINUS_EXAMPLE = (
    "G-W1-L1 Dhr. Jan Janssen Marktstraat 1 1111 Hasselt Gelieve vóór 15.04.2021 het bedrag van € 9,65 te betalen "
    "op rekeningnummer BE76 3350 5545 9895 Samenstelling van uw factuurbedrag Facturatieperiode: 26.01.2021 - 26.03.2021 "
    "Totaal Elektriciteit - EAN 541449206008116238 € 532,31 Totaal Gas - EAN 541449206008116239 € 306,75 "
    "Totaal (excl. btw) € 839,06 BTW € 172,59 Totaal (incl. btw) € 1.011,65 Comfort Services € 7,95 "
    "Aangerekende voorschotten - € 1.002,00 Totaal factuurbedrag (incl.btw) € 9,65 Overzicht van uw verbruik "
    "Product Verbruik Eenheidsprijs (excl. btw) Actief+ elektriciteit dag 2.208 kWh 0,0652291 €/kWh "
    "Actief+ elektriciteit nacht 0 kWh 0,0665714 €/kWh Actief+ injectie dag 0 kWh 0,0665714 €/kWh "
    "Actief+ gas 559 kWh 0,0223225 €/kWh Overzicht van uw meterstanden Omschrijving Beginstand Eindstand Status "
    "Afname dag 42.400 kWh 44.608 kWh Opgenomen Consumptie gas 6.266 m 3 6.825 m3 Opgenomen "
    "Uw nieuw voorschot (incl. btw) Omschrijving Frequentie Oud bedrag Nieuw bedrag Eerste aanrekening "
    "Elektriciteit tweemaandelijks € 100 € 108 03.04.2021 Gas tweemaandelijks € 70 € 68 03.04.2021"
)


def test_luminus_layout_reads_balance_totals_and_consumption_not_meter_readings() -> None:
    chunks = [KnowledgeChunk(page=1, source="luminus.pdf", urls=(), text=LUMINUS_EXAMPLE)]
    facts = analyze_invoice_chunks(chunks)["facts"]

    assert facts["amount_due"]["value"] == Decimal("9.65")
    assert facts["total_energy_cost"]["value"] == Decimal("1011.65")
    assert facts["advances_paid"]["value"] == Decimal("1002.00")
    assert facts["billing_period"]["value"] == "26.01.2021 tot 26.03.2021"
    assert facts["electricity_kwh"]["value"] == Decimal("2208")
    assert facts["gas_kwh"]["value"] == Decimal("559")
    assert facts["due_date"]["value"] == "15.04.2021"
    assert facts["customer"]["value"] == "Jan Janssen"

    ask = lambda question: answer_uploaded_pdf_question(question, chunks, facts)["answer"]
    assert ask("Wat is het totaal dat ik nog moet betalen?") == (
        "Volgens deze factuur moet je nog € 9,65 betalen (PDF-pagina 1). Volgens deze factuur betaal je vóór 15.04.2021 (PDF-pagina 1)."
    )
    assert ask("Wat wordt mijn nieuwe voorschot?") == (
        "Je nieuwe voorschot volgens deze factuur: elektriciteit € 108,00 tweemaandelijks (was € 100,00); "
        "gas € 68,00 tweemaandelijks (was € 70,00) (PDF-pagina 1)."
    )
    assert ask("Hoeveel voorschotten zijn aangerekend?").startswith("Volgens deze factuur zijn er € 1.002,00")
    assert ask("Wat is mijn gasverbruik?") == "Je gasverbruik op deze factuur is 559 kWh (PDF-pagina 1)."
    assert ask("Wat is mijn verbruik?") == (
        "Je stroomverbruik op deze factuur is 2208 kWh (PDF-pagina 1). Je gasverbruik op deze factuur is 559 kWh (PDF-pagina 1)."
    )
    assert ask("Wanneer moet ik betalen?") == "Volgens deze factuur betaal je vóór 15.04.2021 (PDF-pagina 1)."


def test_amounts_without_thousands_separator_are_read_whole() -> None:
    chunks = [KnowledgeChunk(page=1, source="bill.pdf", urls=(), text="Totale energiekosten: €1234,56 Totaal te betalen: € 1234,56")]
    facts = analyze_invoice_chunks(chunks)["facts"]

    assert facts["total_energy_cost"]["value"] == Decimal("1234.56")
    assert facts["amount_due"]["value"] == Decimal("1234.56")
    dated = [KnowledgeChunk(page=1, source="bill.pdf", urls=(), text="Te betalen 15.04.2021 via domiciliëring")]
    assert analyze_invoice_chunks(dated)["facts"]["amount_due"] is None


def test_a_negative_balance_is_money_back() -> None:
    chunks = [KnowledgeChunk(page=1, source="bill.pdf", urls=(), text=(
        "Totaal (incl. btw) € 900,00 Aangerekende voorschotten - € 1.000,00 Totaal factuurbedrag (incl.btw) - € 100,00"
    ))]
    facts = analyze_invoice_chunks(chunks)["facts"]

    assert facts["amount_due"]["value"] == Decimal("-100.00")
    assert "- Vermeld bedrag terug te krijgen: € 100,00 (PDF-pagina 1)." in analyze_invoice_chunks(chunks)["answer"]
    assert facts["advances_paid"]["value"] == Decimal("1000.00")
    assert answer_uploaded_pdf_question("Hoeveel moet ik betalen?", chunks, facts)["answer"] == (
        "Volgens deze factuur krijg je € 100,00 terug (PDF-pagina 1)."
    )
    assert "- **Terug te krijgen:** € 100,00 (PDF-pagina 1)" in answer_uploaded_pdf_question("Leg mijn factuur uit", chunks, facts)["answer"]


def test_a_new_advance_question_never_gets_the_advances_already_charged() -> None:
    chunks = _synthetic_bill_a()
    facts = analyze_invoice_chunks(chunks)["facts"]

    result = answer_uploaded_pdf_question("Wat wordt mijn nieuwe voorschot?", chunks, facts)

    assert result == {"answer": "Ik vond je nieuwe voorschot niet duidelijk op deze factuur.", "sources": []}


def test_the_gradio_chat_answers_from_the_same_facts() -> None:
    import asyncio

    import energy_voice_app as app

    chunks = _synthetic_bill_a()
    state = {"knowledge_chunks": [chunk.__dict__ for chunk in chunks], "knowledge_name": "bill-a.pdf"}
    messages, *_ = asyncio.run(app.answer_text_turn("Hoeveel moet ik nog betalen?", state))

    assert messages[-1]["content"] == "Volgens deze factuur moet je nog € 300,00 betalen (PDF-pagina 2)."


def test_a_misspelled_summary_request_gets_the_invoice_summary() -> None:
    chunks = _synthetic_bill_a()
    analysis = analyze_invoice_chunks(chunks)

    result = answer_uploaded_pdf_question("wat is de smaenvatting hiervn", chunks, analysis["facts"], analysis.get("invoice"))

    assert result["answer"].startswith("Samenvatting van je factuur")
    assert "Nog te betalen: € 300,00 (PDF-pagina 2)" in result["answer"]
    assert result["sources"]


# --- Structured invoice (invoice_schema.Invoice) and summaries ---------------------------------------

import json
from pathlib import Path

import pytest

from luminus_harness.invoice_schema import Invoice, build_invoice, is_summary_request, summarize_invoice
from luminus_harness.knowledge import load_pdf_chunks_from_bytes

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def _fixture(name: str) -> tuple[list[KnowledgeChunk], dict]:
    chunks = load_pdf_chunks_from_bytes((FIXTURES / name).read_bytes(), source=name)
    return chunks, analyze_invoice_chunks(chunks)


@pytest.mark.parametrize("question", [
    "wat is de smaenvatting hiervn", "Wat is de samenvatting hiervan?", "Vat deze factuur samen",
    "kan je dit samenvatten", "geef een samenvattng", "Résumé de la facture", "summarize this invoice",
])
def test_summary_requests_are_recognised(question) -> None:
    assert is_summary_request(question)


@pytest.mark.parametrize("question", [
    "Hoeveel moet ik nog betalen?", "Wat is mijn verbruik?", "Wat staat er over Eneco?", "Wie is mijn leverancier?", "samen met gas?",
])
def test_other_questions_are_not_summary_requests(question) -> None:
    assert not is_summary_request(question)


def test_summary_requests_answer_from_the_structured_invoice() -> None:
    chunks, analysis = _fixture("01-nl-afrekening-gegroepeerd.pdf")
    for question in ("Wat is de samenvatting hiervan?", "Vat deze factuur samen"):
        result = answer_uploaded_pdf_question(question, chunks, analysis["facts"], analysis["invoice"])
        assert result["answer"].splitlines() == [
            "Samenvatting van je jaarafrekening van Kanaal Energie:",
            "- Periode: 01/01/2025 tot 31/12/2025 (PDF-pagina 1)",
            "- Verbruik elektriciteit: 2708 kWh (PDF-pagina 1)",
            "- Verbruik gas: 6000 kWh (PDF-pagina 1)",
            "- Totale energiekosten: € 1.234,56 (PDF-pagina 1)",
            "- Al aangerekende voorschotten: € 1.000,00 (PDF-pagina 1)",
            "- Nog te betalen: € 234,56 (PDF-pagina 1)",
            "- Nieuw voorschot: € 105,00 per maand (PDF-pagina 1)",
        ]
        assert result["sources"][0]["title"] == "01-nl-afrekening-gegroepeerd.pdf"


def test_structured_invoice_holds_extracted_and_calculated_values_apart() -> None:
    _, analysis = _fixture("01-nl-afrekening-gegroepeerd.pdf")
    invoice = analysis["invoice"]
    assert invoice.schema_version == "1.0" and invoice.document_type == "annual_settlement"
    assert invoice.source == "01-nl-afrekening-gegroepeerd.pdf"
    assert (invoice.supplier.value, invoice.supplier.page) == ("Kanaal Energie", 1) and "Kanaal Energie" in invoice.supplier.excerpt
    assert (invoice.billing_period.start, invoice.billing_period.end) == ("2025-01-01", "2025-12-31")
    assert invoice.consumption.electricity.value == "2708" and invoice.consumption.electricity.unit == "kWh"
    assert invoice.amounts.total_energy_cost.value_eur == "1234.56"
    assert invoice.amounts.advances_paid.value_eur == "1000.00"
    assert invoice.amounts.amount_due.value_eur == "234.56" and invoice.amounts.credit is None
    assert invoice.calculated.costs_minus_advances_eur == "234.56"
    assert invoice.calculated.inputs == ["total_energy_cost", "advances_paid"]
    assert [(row.energy, row.frequency, row.amount_eur) for row in invoice.new_advances] == [(None, "monthly", "105.00")]
    assert invoice.warnings == []


def test_structured_invoice_serialises_to_json_with_decimal_strings() -> None:
    _, analysis = _fixture("13-nl-jaarafrekening-nieuw-voorschot-tabel.pdf")
    payload = json.loads(analysis["invoice"].model_dump_json())
    assert payload["schema_version"] == "1.0"
    assert payload["amounts"]["amount_due"]["value_eur"] == "412.30"
    assert payload["amounts"]["credit"] is None and payload["customer"]["value"] == "Alex Doe"
    assert payload["due_date"] == {
        "source": "13-nl-jaarafrekening-nieuw-voorschot-tabel.pdf", "page": 1, "excerpt": payload["due_date"]["excerpt"],
        "read_by": "rules", "printed": "28/10/2026", "date": "2026-10-28", "direct_debit": False,
    }
    assert [(row["energy"], row["frequency"], row["amount_eur"], row["previous_amount_eur"]) for row in payload["new_advances"]] == [
        ("electricity", "monthly", "95.00", "80.00"), ("gas", "monthly", "120.00", "110.00"),
    ]
    assert Invoice.model_validate_json(json.dumps(payload)) == analysis["invoice"]


def test_a_credit_is_money_back_not_an_amount_due() -> None:
    chunks, analysis = _fixture("03-nl-tegoed.pdf")
    invoice = analysis["invoice"]
    assert invoice.amounts.amount_due is None and invoice.amounts.credit.value_eur == "150.00"
    assert "- Je krijgt terug: € 150,00 (PDF-pagina 1)" in answer_uploaded_pdf_question("vat samen", chunks, analysis["facts"], invoice)["answer"]


def test_a_zero_balance_has_nothing_to_pay() -> None:
    chunks, analysis = _fixture("05-nl-nulafrekening.pdf")
    assert analysis["invoice"].amounts.amount_due.value_eur == "0.00"
    summary = answer_uploaded_pdf_question("samenvatting", chunks, analysis["facts"], analysis["invoice"])["answer"]
    assert "- Nog te betalen: niets (PDF-pagina 1)" in summary


def test_gas_in_cubic_metres_keeps_its_unit() -> None:
    chunks, analysis = _fixture("15-nl-jaarafrekening-gas-m3.pdf")
    gas = analysis["invoice"].consumption.gas
    assert (gas.value, gas.unit) == ("1150", "m³") and analysis["invoice"].consumption.electricity is None
    assert "- Verbruik gas: 1150 m³ (PDF-pagina 1)" in answer_uploaded_pdf_question("samenvatting", chunks, analysis["facts"])["answer"]


def test_missing_values_are_null_and_said_in_the_summary() -> None:
    chunks, analysis = _fixture("09-nl-ontbrekende-bedragen.pdf")
    invoice = analysis["invoice"]
    assert invoice.amounts.model_dump() == {"total_energy_cost": None, "advances_paid": None, "amount_due": None, "credit": None}
    assert invoice.consumption.electricity is None and invoice.calculated.costs_minus_advances_eur is None
    assert {"missing:amount_due", "missing:total_energy_cost", "missing:advances_paid", "missing:consumption"} <= set(invoice.warnings)
    summary = answer_uploaded_pdf_question("Vat deze factuur samen", chunks, analysis["facts"], invoice)["answer"]
    assert summary.endswith("Niet duidelijk gevonden op de factuur: het bedrag te betalen of terug te krijgen, het verbruik.")


def test_an_advance_bill_summary_lists_its_lines_and_direct_debit() -> None:
    chunks, analysis = _fixture("11-nl-voorschot-domiciliering.pdf")
    assert analysis["invoice"].document_type == "advance_invoice"
    summary = answer_uploaded_pdf_question("samenvatting", chunks, analysis["facts"], analysis["invoice"])["answer"]
    assert summary.splitlines() == [
        "Samenvatting van je voorschotfactuur van Kanaal Energie:",
        "- Voorschot elektriciteit okt 2026: € 63,21 excl. btw (PDF-pagina 1)",
        "- Voorschot gas okt 2026: € 105,66 excl. btw (PDF-pagina 1)",
        "- Nog te betalen: € 179,00 (PDF-pagina 1)",
        "- Wordt via domiciliëring afgehouden op 31.10.2026 (PDF-pagina 1)",
    ]
    assert [line.vat_included for line in analysis["invoice"].advance_lines] == [False, False]


def test_advance_lines_keep_unknown_or_included_vat() -> None:
    lines = {**_fact("lines"), "value": [{"energy": "gas", "period": "okt 2026", "amount": Decimal("100.00")}]}
    assert build_invoice({"advance_lines": lines}, "x.pdf").advance_lines[0].vat_included is None
    invoice = build_invoice({"advance_lines": {**lines, "vat": "incl. btw"}}, "x.pdf")
    assert invoice.advance_lines[0].vat_included is True
    assert "- Voorschot gas okt 2026: € 100,00 incl. btw (PDF-pagina 1)" in summarize_invoice(invoice)["answer"]


def test_a_negative_cost_keeps_its_sign() -> None:
    facts = {
        "total_energy_cost": {**_fact("Totale energiekosten: - 120,00 EUR"), "value": Decimal("-120.00")},
        "advances_paid": {**_fact("Voorschotten: 300,00 EUR"), "value": Decimal("300.00")},
        "amount_due": {**_fact("Terug te krijgen: 420,00 EUR"), "value": Decimal("-420.00")},
        "calculated_difference": {"value": Decimal("-420.00")},
    }
    invoice = build_invoice(facts, "x.pdf")
    assert invoice.amounts.total_energy_cost.value_eur == "-120.00"
    assert invoice.amounts.credit.value_eur == "420.00"
    assert invoice.calculated.costs_minus_advances_eur == "-420.00"
    assert "calculated_difference_differs_from_amount_due" not in invoice.warnings
    assert "- Totale energiekosten: € -120,00 (PDF-pagina 1)" in summarize_invoice(invoice)["answer"]


def test_a_bill_paid_by_hand_says_pay_before() -> None:
    date = {**_fact("Te betalen voor 15/11/2026"), "value": "15/11/2026"}
    summary = summarize_invoice(build_invoice({"due_date": date}, "x.pdf"))["answer"]
    assert "- Betalen vóór 15/11/2026 (PDF-pagina 1)" in summary


def _fact(excerpt: str) -> dict:
    return {"source": "x.pdf", "page": 1, "excerpt": excerpt}


def test_field_questions_and_passages_keep_working_with_the_invoice() -> None:
    chunks = _synthetic_bill_a()
    analysis = analyze_invoice_chunks(chunks)
    paid = answer_uploaded_pdf_question("Hoeveel moet ik nog betalen?", chunks, analysis["facts"], analysis["invoice"])
    assert paid["answer"] == "Volgens deze factuur moet je nog € 300,00 betalen (PDF-pagina 2)."
    passage = answer_uploaded_pdf_question("Wat staat er over Luminus?", chunks, analysis["facts"], analysis["invoice"])
    assert passage["answer"].startswith("Ik vond een relevante passage op PDF-pagina 1")


def test_upload_returns_the_invoice_and_follow_ups_summarise_it_locally(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import energy_voice_app

    monkeypatch.setattr(energy_voice_app, "_api_rate_limiter", SlidingWindowRateLimiter(requests=100, window_seconds=60, max_clients=100))

    async def no_hosted_answer(*args, **kwargs):
        raise AssertionError("an uploaded invoice never reaches a hosted model or search")

    monkeypatch.setattr(energy_voice_app, "answer_energy_question", no_hosted_answer)
    monkeypatch.setattr(energy_voice_app, "standalone_question", no_hosted_answer)
    client = TestClient(energy_voice_app.api_app)
    pdf = (FIXTURES / "01-nl-afrekening-gegroepeerd.pdf").read_bytes()

    upload = client.post("/api/upload", files={"file": ("01.pdf", pdf, "application/pdf")}).json()
    assert {"session_id", "filename", "answer", "facts", "sources", "invoice", "processing"} <= set(upload)
    assert upload["processing"] == "local-only"
    assert upload["invoice"]["schema_version"] == "1.0" and upload["invoice"]["amounts"]["amount_due"]["value_eur"] == "234.56"

    reply = client.post("/api/answer", json={"question": "wat is de smaenvatting hiervn", "session_id": upload["session_id"]}).json()
    assert reply["processing"] == "local-only"
    assert reply["answer"].startswith("Samenvatting van je jaarafrekening van Kanaal Energie:")
    assert "- Nog te betalen: € 234,56 (PDF-pagina 1)" in reply["answer"]
    assert reply["sources"][0]["detail"] == "PDF · pagina 1"


# --- Explaining the invoice, and questions the invoice cannot answer -------------------------------

from luminus_harness.invoice_schema import is_explanation_request


@pytest.mark.parametrize("question", [
    "wqt bekent deze factuur geef me foresights of trends", "oke mqq4 3at beeteknt dit",
    "oke maar wat beteknen die resulataten", "Wat betekent dit?", "wat zegt dit over volgend jaar",
])
def test_explanation_requests_are_recognised(question) -> None:
    assert is_explanation_request(question)


@pytest.mark.parametrize("question", [
    "oke maar real of barcelona", "wat betekent capaciteitstarief", "hoe berekent Luminus het capaciteitstarief",
    "Hoeveel moet ik betalen?", "wat is de samenvatting",
])
def test_other_questions_are_not_explanation_requests(question) -> None:
    assert not is_explanation_request(question)


def test_explaining_a_settlement_says_why_and_what_next_year_may_bring() -> None:
    chunks, analysis = _fixture("01-nl-afrekening-gegroepeerd.pdf")
    answer = answer_uploaded_pdf_question("oke maar wat beteknen die resulataten", chunks, analysis["facts"], analysis["invoice"])
    assert answer["answer"].splitlines() == [
        "Wat deze jaarafrekening betekent:",
        "- Je betaalt € 234,56 bij, omdat je voorschotten (€ 1.000,00) lager waren dan je werkelijke energiekosten "
        "(€ 1.234,56) (PDF-pagina 1)",
        "- In deze periode verbruikte je 2708 kWh elektriciteit en 6000 kWh gas (PDF-pagina 1)",
        "- Je nieuwe voorschot is € 1.260,00 per jaar. Blijven je verbruik en de prijzen gelijk, dan krijg je volgend jaar "
        "ongeveer € 25,44 terug. Dat is een berekening van Karen, geen bedrag van de factuur (PDF-pagina 1)",
    ]
    assert answer["sources"][0]["detail"] == "PDF · pagina 1"


def test_a_trend_question_says_one_invoice_shows_one_period() -> None:
    chunks, analysis = _fixture("01-nl-afrekening-gegroepeerd.pdf")
    answer = answer_uploaded_pdf_question(
        "wqt bekent deze factuur geef me foresights of trends", chunks, analysis["facts"], analysis["invoice"]
    )["answer"]
    assert answer.startswith("Wat deze jaarafrekening betekent:")
    assert answer.splitlines()[-1] == (
        "Trends kan ik uit één factuur niet halen: ze toont maar één periode. Daarvoor heb je de facturen van "
        "vorige jaren nodig, of je verbruiksgeschiedenis bij je netbeheerder (in Vlaanderen: Mijn Fluvius)."
    )


def test_explaining_a_credit_and_an_advance_bill() -> None:
    chunks, analysis = _fixture("03-nl-tegoed.pdf")
    credit = answer_uploaded_pdf_question("wat betekent dit", chunks, analysis["facts"], analysis["invoice"])["answer"]
    assert "- Je krijgt € 150,00 terug, omdat je voorschotten (€ 950,00) hoger waren dan je werkelijke energiekosten (€ 800,00)" in credit
    chunks, analysis = _fixture("11-nl-voorschot-domiciliering.pdf")
    advance = answer_uploaded_pdf_question("wat betekent dit", chunks, analysis["facts"], analysis["invoice"])["answer"]
    assert advance.splitlines()[:2] == [
        "Wat deze voorschotfactuur betekent:",
        "- Dit is een vast bedrag vooraf, geen afrekening van je echte verbruik. Dat gebeurt pas op je jaarafrekening.",
    ]


def test_a_question_the_invoice_cannot_answer_goes_to_karen_without_the_invoice(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import energy_voice_app

    monkeypatch.setattr(energy_voice_app, "_api_rate_limiter", SlidingWindowRateLimiter(requests=100, window_seconds=60, max_clients=100))
    seen = []

    async def public_answer(question, history):
        seen.append((question, history))
        return {"answer": "Daar help ik niet mee.", "sources": [], "processing": "small-talk"}

    monkeypatch.setattr(energy_voice_app, "_public_answer", public_answer)
    client = TestClient(energy_voice_app.api_app)
    pdf = (FIXTURES / "01-nl-afrekening-gegroepeerd.pdf").read_bytes()
    session_id = client.post("/api/upload", files={"file": ("01.pdf", pdf, "application/pdf")}).json()["session_id"]
    history = [{"question": "samenvatting", "answer": "Nog te betalen: € 234,56"}]

    reply = client.post("/api/answer", json={"question": "oke maar real of barcelona", "session_id": session_id, "history": history}).json()
    assert reply["answer"] == "Daar help ik niet mee."
    assert seen == [("oke maar real of barcelona", [])]

    local = client.post("/api/answer", json={"question": "wat betekent dit", "session_id": session_id, "history": history}).json()
    assert local["processing"] == "local-only" and local["answer"].startswith("Wat deze jaarafrekening betekent:")
    assert len(seen) == 1


def test_no_next_year_estimate_for_a_bill_shorter_than_a_year() -> None:
    def fact(value, excerpt="x"):
        return {"source": "x.pdf", "page": 1, "excerpt": excerpt, "value": value}

    facts = {
        "invoice_type": fact("Jaarafrekening"),
        "billing_period": fact("01/01/2025 tot 30/06/2025"),
        "total_energy_cost": fact(Decimal("600.00")), "advances_paid": fact(Decimal("600.00")),
        "amount_due": fact(Decimal("0.00")), "calculated_difference": fact(Decimal("0.00")),
        "new_advance": fact([{"energy": None, "frequency": "maandelijks", "new": Decimal("100.00"), "old": None}]),
    }
    answer = answer_uploaded_pdf_question("wat betekent dit", [], facts)["answer"]
    assert "- Je nieuwe voorschot is € 1.200,00 per jaar. Deze factuur dekt geen volledig jaar, dus een schatting voor volgend jaar maak ik niet (PDF-pagina 1)" in answer
    assert "volgend jaar ongeveer" not in answer
    full = {**facts, "billing_period": fact("01/01/2025 tot 31/12/2025")}
    assert "Blijven je verbruik en de prijzen gelijk" in answer_uploaded_pdf_question("wat betekent dit", [], full)["answer"]
