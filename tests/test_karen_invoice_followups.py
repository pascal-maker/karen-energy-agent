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
    assert "niets meer verschuldigd" in _ask("05-nl-nulafrekening.pdf", "wie moet er betalen?")["answer"]


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
    assert "Nagekeken: de optelling (€ 168,87 + 6% btw = € 179,00)" in answer, "Says what was checked, not 'nothing else found'"
    assert "aandachtspunten vond ik niet" not in answer


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


@pytest.mark.parametrize("question", [
    "Wie krijgt een sociaal tarief?", "Wie moet mijn gasmeter afsluiten?", "Wie betaalt de netkosten?",
    "Wie betaalt mijn netkosten?", "Wie moet betalen voor het afsluiten van mijn gasmeter?",
])
def test_other_who_questions_are_not_about_who_owes_the_bill(question):
    from luminus_harness.invoice_schema import is_payer_request

    assert not is_payer_request(question)


def test_a_yearly_question_about_one_energy_uses_that_energys_advance():
    gas = _ask(ADVANCE, "Wat kost gas per jaar?")["answer"]
    assert "12 × € 105,66 = **€ 1.267,92 excl. btw per jaar**" in gas and "2.148" not in gas
    power = _ask(ADVANCE, "wat betaal ik per jaar aan stroom?")["answer"]
    assert "12 × € 63,21 = **€ 758,52 excl. btw per jaar**" in power
    assert "apart bedrag voor gas" in _ask("08-fr-b2b-annuelle.pdf", "wat kost gas per jaar?")["answer"]


def test_one_energys_new_advance_is_not_a_yearly_total_for_both():
    from luminus_harness.invoice_schema import NewAdvance, yearly_answer

    chunks = load_pdf_chunks_from_bytes((FIXTURES / "01-nl-afrekening-gegroepeerd.pdf").read_bytes(), source="01")
    invoice = analyze_invoice_chunks(chunks)["invoice"]
    only_power = NewAdvance(source="01", page=1, excerpt="Nieuw voorschot elektriciteit € 50,00 per maand",
                            energy="electricity", frequency="monthly", amount_eur="50.00", previous_amount_eur=None)
    answer = yearly_answer(invoice.model_copy(update={"new_advances": [only_power]}), "wat betaal ik per jaar?")["answer"]
    assert "alleen het nieuwe voorschot voor elektriciteit" in answer and "gas vond ik niet" in answer
    assert "600" not in answer
    assert "€ 600,00 per jaar" in yearly_answer(invoice.model_copy(update={"new_advances": [only_power]}), "wat kost stroom per jaar?")["answer"]


@pytest.mark.parametrize("question", [
    "wie moet mijn factuur betalen", "aan wie moet ik dit bedrag betalen?", "wie krijgt het geld?",
    "Wie moet voor deze factuur betalen?", "Wie moet deze factuur van Luminus betalen?", "Wie moet deze rekening betalen aub?",
])
def test_payer_questions_about_the_bill_itself_are_still_recognised(question):
    from luminus_harness.invoice_schema import is_payer_request

    assert is_payer_request(question)


def test_a_payment_question_about_something_else_leaves_the_payer_answer_alone():
    for question in ("Wie betaalt mijn netkosten?", "Wie moet betalen voor het afsluiten van mijn gasmeter?"):
        assert _ask(ADVANCE, question)["found"] is False, "A general question, answered without the bill"


@pytest.mark.parametrize("question", ["Wie moet voor deze factuur betalen?", "Wie moet deze rekening betalen aub?"])
def test_a_payer_question_naming_the_bill_stays_with_the_bill(question):
    reply = _ask(ADVANCE, question)
    assert reply.get("found", True) and "€ 179,00 verschuldigd aan Kanaal Energie" in reply["answer"]
