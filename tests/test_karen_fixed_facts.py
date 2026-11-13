"""Fixed facts (units, terms, how something works), the reply language and an objection to an answer.

From the live Langfuse traces of 12 November 2026: "how many kilojoules is in a kwh" searched for 11 s, showed
an unrelated source and answered in Dutch; "dat maakt geen sense" then got "Wat klopt niet precies?"."""

from unittest.mock import AsyncMock

import pytest

from luminus_harness import gemma_answer, market_prices
from tests.test_karen_gas_prices import with_gas
from tests.test_karen_market_prices import api, market  # noqa: F401  (api is a fixture)


def ask(api, question, history=()):  # noqa: F811
    body = {"question": question, "history": [{"question": q, "answer": a} for q, a in history]}
    response = api[0].post("/api/answer", json=body)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def model(monkeypatch):
    """A stand-in for the language model: records every call, answers from a list or a function."""
    calls: list[tuple[str, str]] = []
    replies: list = []

    def generate(prompt, instruction=gemma_answer._SYSTEM_INSTRUCTION, on_delta=None):
        calls.append((prompt, instruction))
        reply = replies.pop(0) if replies else "Ik weet het niet zeker."
        return reply(prompt) if callable(reply) else reply

    monkeypatch.setattr(gemma_answer, "_generate", generate)
    monkeypatch.setattr(gemma_answer, "_model_configured", lambda: True)
    return calls, replies


# --- 1. unit conversions are computed in code ---------------------------------------------------------

@pytest.mark.parametrize("question, lang, expected", [
    ("how many kilojoules is in a kwh", "en", "1 kWh = 3,600 kJ"),
    ("Hoeveel kJ is een kWh?", "nl", "1 kWh = 3.600 kJ"),
    ("Hoeveel kilojoule zit er in 1 kWh?", "nl", "1 kWh = 3.600 kJ"),
    ("combien de kilojoules dans un kWh ?", "fr", "1 kWh = 3 600 kJ"),
    ("Hoeveel kWh is 1 MWh?", "nl", "1 MWh = 1.000 kWh"),
    ("2 kWh in MJ", "nl", "2 kWh = 7,2 MJ"),
    ("Hoeveel kWh is 3600 kJ?", "nl", "3.600 kJ = 1 kWh"),
    ("how many kcal is 1 kWh?", "en", "1 kWh ≈ 860 kcal"),
    ("Hoeveel watt is 2 kW?", "nl", "2 kW = 2.000 W"),
    ("2+2kJ", "nl", "2 + 2 kJ = 4 kJ"),
])
def test_unit_conversions_are_computed_in_code(question, lang, expected):
    from luminus_harness.fixed_facts import convert

    reply = convert(question, lang)
    assert reply is not None and expected in reply["answer"], (question, reply)
    assert reply["processing"] == "unit-conversion" and reply["sources"] == []


@pytest.mark.parametrize("question", [
    "Hoeveel kW is een kWh?",           # power and energy: no conversion, an explanation
    "Hoeveel kWh is 1 m³ gas?",         # depends on the gas: not a fixed number
    "Hoeveel kost een kWh?",
    "Wat is een kWh?",
    "Hoeveel kWh verbruikt een gezin?",
])
def test_what_code_cannot_convert_is_left_alone(question):
    from luminus_harness.fixed_facts import convert

    assert convert(question, "nl") is None, question


def test_api_kilojoules_in_a_kwh_needs_no_search_and_no_model(api, model, monkeypatch):  # noqa: F811
    reply = ask(api, "how many kilojoules is in a kwh")
    assert "1 kWh = 3,600 kJ" in reply["answer"] and reply["processing"] == "unit-conversion"
    assert reply["sources"] == [], "No unrelated source under a computed answer"
    api[2].assert_not_awaited()
    assert model[0] == [], "Computed in code: no model call"


# --- 2. terms and how something works: from general knowledge, inside a hard boundary ---------------------

FACT_QUESTIONS = [
    "Wat is een kWh?", "Wat is het verschil tussen kW en kWh?", "Hoeveel kW is een kWh?",
    "Wat is het capaciteitstarief?", "Hoe werkt een warmtepomp?", "Wat betekent piekvermogen?",
    "Wat is een digitale meter?", "Hoe werken zonnepanelen?", "Hoeveel kWh is 1 m³ gas?",
    "What is a heat pump?", "How does a smart meter work?", "What is the difference between kW and kWh?",
    "Qu'est-ce qu'une pompe à chaleur ?", "Comment fonctionne un compteur intelligent ?", "C'est quoi un kWh ?",
]
BOUNDARY_QUESTIONS = [
    "Wat kost een kWh stroom vandaag?", "Wat is de prijs van gas?", "Hoeveel premie krijg ik voor een warmtepomp?",
    "Wat is het capaciteitstarief in euro?", "Mag mijn leverancier mijn voorschot verhogen?",
    "Wat is het tarief van Luminus?", "Moet ik een digitale meter nemen?", "Wat is mijn verbruik?",
    "Hoe werkt het sociaal tarief in Brussel?", "Wat is de goedkoopste leverancier?",
    "Wat is de hoofdstad van Frankrijk?", "Wat is 2+2?",
]


@pytest.mark.parametrize("question", FACT_QUESTIONS)
def test_fixed_fact_questions_are_recognised(question):
    from luminus_harness.fixed_facts import is_fixed_fact_question

    assert is_fixed_fact_question(question), question


@pytest.mark.parametrize("question", BOUNDARY_QUESTIONS)
def test_prices_rules_suppliers_regions_and_own_situation_stay_on_the_source_path(question):
    from luminus_harness.fixed_facts import is_fixed_fact_question

    assert not is_fixed_fact_question(question), question


def test_api_fixed_fact_is_answered_without_search_and_marked_as_general_knowledge(api, model):  # noqa: F811
    model[1].append("Een warmtepomp haalt warmte uit de buitenlucht, de bodem of het grondwater en geeft die af in huis.")
    reply = ask(api, "Hoe werkt een warmtepomp?")
    assert reply["answer"].startswith("Een warmtepomp haalt warmte")
    assert reply["processing"] == "general-knowledge", "The component shows 'Algemene uitleg' under it"
    assert reply["sources"] == []
    api[2].assert_not_awaited()


def test_fixed_fact_may_hold_a_physical_number(api, model):  # noqa: F811
    model[1].append("Een kWh is de energie van een toestel van 1.000 watt dat 1 uur aanstaat.")
    assert "1.000 watt" in ask(api, "Wat is een kWh?")["answer"]


@pytest.mark.parametrize("invented", [
    "Het capaciteitstarief kost ongeveer € 50 per kW per jaar.",
    "Het capaciteitstarief bedraagt 40 euro per kW.",
    "Je betaalt daarvoor 12 cent per kWh.",
    "Het capaciteitstarief werd ingevoerd in 2023.",
    "Daarmee bespaar je 30% op je factuur.",
    "Ik weet het niet zeker.",
    "",
])
def test_fixed_fact_with_money_a_year_or_a_percentage_falls_back_to_the_source_path(api, model, invented):  # noqa: F811
    model[1].append(invented)
    reply = ask(api, "Wat is het capaciteitstarief?")
    assert reply["answer"] == "Bestaand antwoord", "The existing source path answers instead"


def test_boundary_question_never_reaches_the_fixed_fact_model(api, model, monkeypatch):  # noqa: F811
    from luminus_harness import fixed_facts

    ask(api, "Hoeveel premie krijg ik voor een warmtepomp?")
    assert not any(instruction == fixed_facts.FIXED_FACT_INSTRUCTION for _, instruction in model[0])
    api[2].assert_awaited()


def test_hand_checked_routes_still_win(api, model, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=market()))
    assert ask(api, "Wat is de CREG-scan?")["processing"] == "contract-check"
    # A supplier's fixed fee is a tariff: the source path, as before, never general knowledge.
    assert ask(api, "Wat is de vaste vergoeding?")["answer"] == "Bestaand antwoord"
    from luminus_harness import fixed_facts
    assert not any(instruction == fixed_facts.FIXED_FACT_INSTRUCTION for _, instruction in model[0])


# --- 3. the answer is in the language of the question ------------------------------------------------------

DUTCH_ANSWER = "Een warmtepomp haalt warmte uit de lucht en geeft die af in het huis. Dat is zuiniger dan een ketel."
ENGLISH_ANSWER = "A heat pump takes heat from the outside air and releases it inside your home."


def test_language_mismatch_is_detected_only_when_clear():
    assert market_prices.language_mismatch(DUTCH_ANSWER, "en")
    assert not market_prices.language_mismatch(ENGLISH_ANSWER, "en")
    assert not market_prices.language_mismatch("1 kWh = 3,600 kJ.", "en"), "Numbers alone say nothing"
    assert not market_prices.language_mismatch(DUTCH_ANSWER, "nl")
    assert market_prices.language_mismatch(
        "1 kWh = 3 600 kJ.\n\n- **Uitleg kort:** 1 kilowattuur is energie van 1 kilowatt gedurende 1 uur; dus 3 600 kJ.", "en"
    ), "The answer from the trace"


def test_fixed_fact_in_the_wrong_language_is_asked_again_in_the_right_one(api, model):  # noqa: F811
    model[1].extend([DUTCH_ANSWER, ENGLISH_ANSWER])
    reply = ask(api, "How does a heat pump work?")
    assert reply["answer"] == ENGLISH_ANSWER
    assert len(model[0]) == 2 and "English" in model[0][1][0], "The second call names the language in that language"


def test_fixed_fact_still_in_the_wrong_language_falls_back(api, model):  # noqa: F811
    model[1].extend([DUTCH_ANSWER, DUTCH_ANSWER])
    assert ask(api, "How does a heat pump work?")["answer"] == "Bestaand antwoord"


def test_grounded_answer_in_the_wrong_language_is_asked_again(model):
    import asyncio

    model[1].extend([DUTCH_ANSWER, ENGLISH_ANSWER])
    answer = asyncio.run(gemma_answer._grounded_answer("How does a heat pump work?", [], [], "en"))
    assert answer == ENGLISH_ANSWER and len(model[0]) == 2


def test_grounded_answer_in_the_right_language_is_asked_once(model):
    import asyncio

    model[1].append(ENGLISH_ANSWER)
    assert asyncio.run(gemma_answer._grounded_answer("How does a heat pump work?", [], [], "en")) == ENGLISH_ANSWER
    assert len(model[0]) == 1


# --- 4. an objection to an answer: explain it again, simpler -----------------------------------------------

PREVIOUS = ("Wat is een kWh?", "Een kWh is de energie van een toestel van 1.000 watt dat 1 uur aanstaat.")
SIMPLER = "Een kWh is hoeveel stroom een toestel van 1.000 watt in 1 uur gebruikt. Wat is er precies nog onduidelijk?"


SIMPLER_FR = "Un kWh est l'énergie qu'un appareil de 1.000 watts utilise pendant 1 heure. Qu'est-ce qui n'est pas clair ?"


@pytest.mark.parametrize("objection, simpler", [
    ("dat maakt geen sense", SIMPLER), ("Dat klopt niet.", SIMPLER), ("ik snap het niet", SIMPLER),
    ("Ik begrijp het niet", SIMPLER), ("dat slaat nergens op", SIMPLER), ("that makes no sense", SIMPLER),
    ("I don't understand", SIMPLER), ("je ne comprends pas", SIMPLER_FR), ("ça n'a pas de sens", SIMPLER_FR),
])
def test_an_objection_gets_the_previous_answer_explained_again(api, model, objection, simpler):  # noqa: F811
    model[1].append(simpler)
    reply = ask(api, objection, history=[PREVIOUS])
    assert reply["answer"] == simpler and reply["processing"] == "re-explained", reply
    prompt = model[0][0][0]
    assert PREVIOUS[0] in prompt and PREVIOUS[1] in prompt, "The model sees the question and the answer it explains again"
    api[2].assert_not_awaited()


@pytest.mark.parametrize("invented", [
    "Een kWh kost ongeveer 30 cent en is de energie van 1.000 watt in 1 uur.",   # 30 was not in the answer
    "Een kWh is 3.600 kJ.",                                                         # a new number
    "Ik weet het niet zeker.",
])
def test_a_re_explanation_with_a_new_number_is_not_used(api, model, invented):  # noqa: F811
    model[1].append(invented)
    reply = ask(api, "dat klopt niet", history=[PREVIOUS])
    assert reply.get("processing") != "re-explained" and invented not in reply["answer"], reply


@pytest.mark.parametrize("message", [
    "Dat is geen probleem", "Dat klopt", "Dat klopt niet met mijn factuur van vorige maand, want ik betaalde toen veel minder",
    "Dat vroeg ik niet", "ok", "Wat is een MWh?",
])
def test_other_messages_are_no_objection(api, model, message):  # noqa: F811
    from luminus_harness.fixed_facts import is_objection

    assert not is_objection(message), message


def test_an_objection_without_an_earlier_answer_keeps_the_existing_path(api, model):  # noqa: F811
    reply = ask(api, "dat klopt niet")
    assert reply.get("processing") != "re-explained"


def test_the_period_objection_of_pr97_still_comes_first(api, model, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    turn = ("Hoeveel kost aardgas nu vergeleken met vorig kwartaal",
            "Ik heb alleen de prijzen van vandaag, geen prijzen van vorig kwartaal. Daarom kan ik niet vergelijken; dit zijn de huidige prijzen.")
    reply = ask(api, "Dat klopt niet", history=[turn])
    assert reply["processing"] == "market-data" and model[0] == []


# --- 5. "ja" to Karen's own "Wil je weten …?" ---------------------------------------------------------------

@pytest.mark.parametrize("yes, answer, question", [
    ("Yes", "Daar kan ik niet bij helpen. Wil je weten hoeveel kJ een kWh is?", "hoeveel kJ een kWh is?"),
    ("ja graag", "Dat hangt af van je toestel. Wil je weten hoe een warmtepomp werkt?", "hoe een warmtepomp werkt?"),
    ("oui", "Je ne peux pas t'aider. Veux-tu savoir combien de kJ il y a dans un kWh ?", "combien de kJ il y a dans un kWh ?"),
    ("yes please", "I can't help with that. Do you want to know how many kJ are in a kWh?", "how many kJ are in a kWh?"),
])
def test_yes_to_karens_offer_becomes_the_offered_question(yes, answer, question):
    from luminus_harness.fixed_facts import affirmed_offer

    assert affirmed_offer(yes, [{"question": "2+2kJ", "answer": answer}]) == question


@pytest.mark.parametrize("yes, answer", [
    ("Yes", "Fijn! Waarmee kan ik je helpen rond je energiefactuur?"),            # no offer to know something
    ("Yes", "Wil je dat ik iets over je energieverbruik of factuur nakijk?"),     # an offer to do, not to tell
    ("Wat kost stroom?", "Wil je weten hoeveel kJ een kWh is?"),                   # a new question, no yes
    ("Yes", "Een kWh is 3.600 kJ."),
])
def test_other_turns_are_no_affirmed_offer(yes, answer):
    from luminus_harness.fixed_facts import affirmed_offer

    assert affirmed_offer(yes, [{"question": "x", "answer": answer}]) is None


def test_api_yes_to_the_offer_answers_the_offered_question(api, model):  # noqa: F811
    model[1].append("Yes")  # the follow-up rewriter leaves the bare yes as it is, as in the trace
    reply = ask(api, "Yes", history=[("2+2kJ", "Daar kan ik niet bij helpen. Wil je weten hoeveel kJ een kWh is?")])
    assert "1 kWh = 3.600 kJ" in reply["answer"], reply


def test_the_new_module_is_uploaded_to_the_space():
    from pathlib import Path

    assert "luminus_harness/fixed_facts.py" in Path("scripts/upload_energy_voice_space.py").read_text()


# --- review of 7a0c33b: signs, loose numbers, number notation, and the source boundary ----------------------

@pytest.mark.parametrize("question", [
    "-2 kWh in MJ", "−2 kWh in MJ", "2/3 kWh in kJ", "2+2 kWh in MJ", "2 * 3 kWh in MJ", "2 x 3 kWh in kJ", "2^3 kWh in MJ",
    "3 5 kWh in MJ", "2 kWh in 3 MJ", "kWh in MJ 5", "1.2.3 kWh in MJ", "1,2,3 kWh in MJ", "2 kWh + 3 MJ in kJ",
    "2 kWh in MJ en 3", "10% van 2 kWh in MJ", "2e3 kWh in MJ", "-2+2 kJ", "2+-2 kJ", "2++2 kJ",
    "½ kWh in MJ", "1½ kWh in MJ", "√4 kWh in MJ", "2² kWh in MJ", "#2 kWh in MJ", "~2 kWh in MJ", "twee kWh in MJ",
    "minus 2 kWh in MJ", "een halve kWh in kJ", "2 duizend kWh in MJ", "2k kWh in MJ", ".5 kWh in MJ", ",5 kWh in MJ",
    "2,.5 kWh in MJ", "2. 5 kWh in MJ",
])
def test_signs_operators_and_loose_numbers_are_never_dropped_silently(question):
    from luminus_harness.fixed_facts import convert

    for lang in ("nl", "en", "fr"):
        assert convert(question, lang) is None, f"{question} ({lang}): not only a conversion, the existing route answers"


@pytest.mark.parametrize("question, lang, expected", [
    ("1,000,000 kWh in MJ", "nl", "1.000.000 kWh = 3.600.000 MJ"),   # two groups: thousands in any language
    ("1.000.000 kWh in MJ", "en", "1,000,000 kWh = 3,600,000 MJ"),
    ("1 000 000 kWh en MJ", "fr", "1 000 000 kWh = 3 600 000 MJ"),
    ("1.000,5 kWh in MJ", "nl", "1.000,5 kWh = 3.601,8 MJ"),
    ("1,000.5 kWh in MJ", "en", "1,000.5 kWh = 3,601.8 MJ"),
    ("2,5 kWh in MJ", "nl", "2,5 kWh = 9 MJ"),
    ("2.5 kWh in MJ", "nl", "2,5 kWh = 9 MJ"),
    ("3.500 kWh in MWh", "nl", "3.500 kWh = 3,5 MWh"),
    ("3,500 kWh in MWh", "en", "3,500 kWh = 3.5 MWh"),
])
def test_number_notation_is_read_as_written(question, lang, expected):
    from luminus_harness.fixed_facts import convert

    assert expected in convert(question, lang)["answer"]


@pytest.mark.parametrize("question, lang", [
    ("3,500 kWh in MWh", "nl"),    # three thousand five hundred or three and a half: not guessed
    ("3.500 kWh in MWh", "en"),
    ("1,000 kWh in MJ", "fr"),
    ("12345678901234567890 kWh in MJ", "nl"),   # more digits than a meter ever shows
    ("1.000.00 kWh in MJ", "nl"),
    ("1,,5 kWh in MJ", "nl"),
])
def test_ambiguous_or_invalid_notation_is_not_computed_and_never_crashes(question, lang):
    from luminus_harness.fixed_facts import convert

    assert convert(question, lang) is None


@pytest.mark.parametrize("question", [
    "1,000,000 kWh in MJ", "3,500 kWh in MWh", "1.2.3 kWh in MJ", "-2 kWh in MJ", "2/3 kWh in kJ", "1,,5 kWh in MJ",
    "99999999999999999999999999 GWh in J", "0 kWh in kJ", "0,0000000001 kWh in GJ",
])
def test_api_never_crashes_on_a_number(api, model, question):  # noqa: F811
    assert ask(api, question)["answer"]


SOURCE_BOUND = [
    "Hoe werkt de digitale meter in Gent?", "hoe werkt de digitale meter in gent", "Hoe werkt de meter bij Ecopower?",
    "hoe werkt de meter bij ecopower", "Hoe werkt de meter van Fluvius?", "Comment fonctionne le compteur à Liège ?",
    "Comment fonctionne le compteur chez Cociter ?", "How does the meter work in Antwerp?", "Hoe werkt de meter bij Sibelga?",
    "Hoe werkt een budgetmeter in 9000?", "Hoe werkt de digitale meter in Zottegem?", "Wat is het capaciteitstarief bij Trevion?",
    "Hoe werkt de meter bij Wase Wind?", "Hoe werkt de teller bij ORES?", "Wat is een budgetmeter in België?",
    "Hoe werkt de meter bij Zonnestroomwinkel?",
]


@pytest.mark.parametrize("question", SOURCE_BOUND)
def test_a_place_a_supplier_or_an_operator_keeps_the_source_path(api, model, question):  # noqa: F811
    from luminus_harness import fixed_facts

    assert not fixed_facts.is_fixed_fact_question(question), question
    model[1].extend(["Zo werkt het.", "Zo werkt het."])
    reply = ask(api, question)
    assert reply.get("processing") != "general-knowledge", reply
    assert not any(instruction == fixed_facts.FIXED_FACT_INSTRUCTION for _, instruction in model[0])


@pytest.mark.parametrize("question", [
    "Hoe werkt een digitale meter?", "Wat is het verschil tussen kW en kWh?", "Wat is een MWh?", "What is a kWh?",
    "Hoe werkt een warmtepomp in de winter?", "Wat is een EAN-code?", "Qu'est-ce qu'un kWh ?",
])
def test_generic_questions_still_take_the_fixed_fact_route(question):
    from luminus_harness.fixed_facts import is_fixed_fact_question

    assert is_fixed_fact_question(question), question
