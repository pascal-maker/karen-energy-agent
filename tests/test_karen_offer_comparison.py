import pytest

from luminus_harness.comparison_session import is_offer_comparison_question, pdf_comparison_reply


@pytest.mark.parametrize("question", ["Vergelijk energieaanbiedingen", "wie is de goedkoopste stroomleverancier?", "compare electricity offers"])
def test_comparison_questions_are_recognised(question):
    assert is_offer_comparison_question(question)


@pytest.mark.parametrize("question", ["Wat kost stroom vandaag?", "Waarom verschilt mijn voorschot van mijn jaarafrekening?"])
def test_other_questions_are_not_comparisons(question):
    assert not is_offer_comparison_question(question)


def test_pdf_sessions_block_comparisons_locally():
    reply = pdf_comparison_reply()
    assert reply["comparison_status"] == "pdf-blocked" and reply["processing"] == "local-only"
