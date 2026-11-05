"""Follow-up questions after an invoice upload, answered from the bill itself (fictional bills only).

A live chat on 2026-11-03: "wie is dit vershuldigd" found no passage and went to a web search, and
"wat kunnen we uit deze factuur afleiden" answered with a raw passage holding account and meter numbers.
"""

from datetime import date
from pathlib import Path

import pytest

from luminus_harness.invoice_analysis import analyze_invoice_chunks, answer_uploaded_pdf_question
from luminus_harness.invoice_schema import FOLLOW_UP_SUGGESTIONS, payer_answer
from luminus_harness.knowledge import load_pdf_chunks_from_bytes

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"
ADVANCE = "11-nl-voorschot-domiciliering.pdf"


def _ask(name: str, question: str) -> dict:
    chunks = load_pdf_chunks_from_bytes((FIXTURES / name).read_bytes(), source=name)
    analysis = analyze_invoice_chunks(chunks)
    return answer_uploaded_pdf_question(question, chunks, analysis["facts"], analysis["invoice"])


@pytest.mark.parametrize("question", ["wie is dit vershuldigd", "Wie is dit verschuldigd?", "wie moet dit betalen?", "aan wie betaal ik dit"])
def test_who_owes_what_to_whom_is_answered_from_the_bill(question):
    reply = _ask(ADVANCE, question)
    assert reply.get("found", True), "Stays with the bill, no web search"
    assert "Deze factuur staat op naam van John Doe" in reply["answer"]
    assert "€ 179,00 verschuldigd aan Kanaal Energie" in reply["answer"]
    assert "domiciliëring" in reply["answer"] and "kan ik niet" in reply["answer"], "A debit date is no proof of payment"


def test_payment_status_stays_unknown_before_and_after_the_debit_date():
    chunks = load_pdf_chunks_from_bytes((FIXTURES / ADVANCE).read_bytes(), source=ADVANCE)
    invoice = analyze_invoice_chunks(chunks)["invoice"]
    before = payer_answer(invoice, today=date(2026, 10, 20))["answer"]
    after = payer_answer(invoice, today=date(2026, 11, 5))["answer"]
    assert "niet controleren" in before and "voorbij" not in before
    assert "voorbij" in after and "bankapp" in after
    assert "betaald" not in after.replace("betaling", ""), "Never says the bill was paid"


def test_money_back_and_nothing_due_keep_the_direction():
    assert "Kanaal Energie betaalt je € 150,00 terug" in _ask("03-nl-tegoed.pdf", "wie is dit verschuldigd")["answer"]
    assert "niets meer verschuldigd" in _ask("05-nl-nulafrekening.pdf", "wie moet betalen")["answer"]


@pytest.mark.parametrize("question", ["wat betaal ik op jaarbasis?", "wat kost dit per jaar?"])
def test_a_year_of_advances_is_twelve_times_the_monthly_advance_and_no_settlement(question):
    answer = _ask(ADVANCE, question)["answer"]
    assert "12 × € 179,00 = **€ 2.148,00 aan voorschotten per jaar**" in answer
    assert "geen voorspelling" in answer and "jaarafrekening" in answer
    assert "bijbetaal" not in answer.replace("of je bijbetaalt", ""), "No made-up settlement from one advance"


@pytest.mark.parametrize("question", ["wat kunnen we uit deze factuur afleiden", "wat kunnen we hieruit aflijden", "zie je aandachtspunten?"])
def test_what_the_bill_lets_karen_conclude(question):
    reply = _ask(ADVANCE, question)
    answer = reply["answer"]
    assert answer.startswith("Wat ik uit deze voorschotfactuur afleid:")
    assert "€ 2.148,00 (berekening van Karen)" in answer
    assert "elektriciteit 37%, gas 63%" in answer
    assert "€ 168,87 + 6% btw (€ 10,13) = € 179,00" in answer
    assert "je echte verbruik en of je voorschot genoeg is" in answer
    assert "aandachtspunten vond ik niet" in answer


def test_passages_never_show_account_meter_or_customer_details():
    reply = _ask(ADVANCE, "hoeveel is de btw?")
    assert reply["answer"].startswith("Dit staat er op PDF-pagina 1: ")
    shown = reply["answer"] + " ".join(source["excerpt"] for source in reply["sources"])
    for private in ("000000000000000011", "000000000000000012", "John Doe", "TEST-KLANT-011", "Teststraat"):
        assert private not in shown
    assert "10,13" in reply["answer"], "The amounts stay readable"
    for question in ("wie is dit verschuldigd", "zie je aandachtspunten"):
        excerpts = " ".join(source["excerpt"] for source in _ask(ADVANCE, question)["sources"])
        assert "000000000000000011" not in excerpts and "John Doe" not in excerpts


def test_an_unclear_question_about_the_bill_asks_back_and_a_general_one_leaves_the_pdf():
    unclear = _ask(ADVANCE, "en hiervan?")
    assert unclear.get("found", True) and unclear["suggestions"] == FOLLOW_UP_SUGGESTIONS
    assert _ask(ADVANCE, "wie wint Real of Barcelona?")["found"] is False
