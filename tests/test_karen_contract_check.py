import pytest

from luminus_harness import gemma_answer
from luminus_harness.contract_check import answer_contract_check, contract_check_topic


@pytest.mark.parametrize("question, topic", [
    ("Hoe check ik mijn energiecontract?", "check"),
    ("Hoe kijk ik mijn energiecontract na?", "check"),
    ("Betaal ik te veel voor stroom?", "check"),
    ("Moet ik overstappen van energieleverancier?", "check"),
    ("Wat is de CREG-scan?", "creg_scan"),
    ("Zijn sjoemelkortingen verboden?", "sjoemelkorting"),
    ("Krijg ik mijn welkomstkorting nog?", "sjoemelkorting"),
    ("Wat is de vaste vergoeding bij mijn energiecontract?", "vaste_vergoeding"),
    ("Moet ik mijn oude contract zelf opzeggen als ik overstap?", "switching"),
])
def test_contract_check_questions_are_recognised(question, topic):
    assert contract_check_topic(question) == topic


@pytest.mark.parametrize("question", [
    "Wat kost stroom vandaag?",
    "Vast of variabel contract?",
    "Wie is de goedkoopste stroomleverancier?",
    "Mijn Luminus contract zelf opzeggen",
    "How do I check my energy contract?",
])
def test_other_questions_keep_their_route(question):
    assert contract_check_topic(question) is None


@pytest.mark.parametrize("question", [
    "Hoe check ik mijn energiecontract?", "Wat is de CREG-scan?", "Zijn sjoemelkortingen verboden?",
    "Wat is de vaste vergoeding?", "Moet ik mijn oude contract zelf opzeggen als ik overstap?",
])
def test_every_answer_quotes_its_sources(question):
    reply = answer_contract_check(question)
    assert reply["processing"] == "contract-check"
    assert reply["sources"] and all(source["url"].startswith("https://") and source["excerpt"] for source in reply["sources"])
    assert any(source["title"].startswith("V-test") for source in reply["suggested_sources"])


def test_check_answer_has_the_four_steps():
    answer = answer_contract_check("Hoe check ik mijn energiecontract?")["answer"]
    assert [line[:2] for line in answer.splitlines() if line[:1].isdigit()] == ["1.", "2.", "3.", "4."]
    assert "700.000 gezinnen" in answer


def test_sjoemelkorting_is_an_energy_word():
    assert gemma_answer.mentions_energy("Zijn sjoemelkortingen verboden?")
