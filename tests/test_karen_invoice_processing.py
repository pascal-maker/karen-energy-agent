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
    assert ask("Wat is het totaal dat ik nog moet betalen?") == "Volgens deze factuur moet je nog € 9,65 betalen (PDF-pagina 1)."
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
