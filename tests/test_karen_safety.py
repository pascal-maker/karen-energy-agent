"""Safety answers decided in code (PR 1 after the swarm audit of 5175ecf).

The five most serious swarm conversations are replayed in full, with the visitor's own earlier
questions as history, exactly as the swarm sent them (experiments/karen_swarm/output/full-5175ecf).
Each test names the turn that went wrong and what Karen said then.
"""

import pytest

from luminus_harness.safety import answer_safety_question, visitor_region
from tests.test_karen_market_prices import api, ask  # noqa: F401  (api is a fixture)


def replay(questions):
    """Karen's reply to every question of a conversation, each with the earlier questions as history."""
    replies = []
    for index, question in enumerate(questions):
        history = [{"question": earlier, "answer": "..."} for earlier in questions[:index]]
        replies.append(answer_safety_question(question, history))
    return replies


# --- wallonia-fr-3: was "à Bruxelles, appelle le 02 274 40 66" for someone in Namur -----------------

WALLONIA_FR_3 = [
    "Bonjour, j'habite à Namur. Mon gestionnaire de réseau, c'est ORES ou RESA ?",
    "Merci. S'il y a une panne de courant chez moi, quel numéro de téléphone dois-je appeler ?",
    "Je ne suis pas à Bruxelles mais à Namur, chez ORES. Quel est le numéro de dépannage d'ORES pour une panne d'électricité ?",
    "Et ma sœur habite à Liège. Elle, c'est RESA ? Et pour une odeur de gaz, que doit-elle faire ?",
    "Merci. Comment puis-je savoir si une panne est déjà signalée dans ma rue à Namur ?",
]


def test_wallonia_fr_3_namur_gets_ores_and_never_another_operators_number():
    replies = replay(WALLONIA_FR_3)
    assert all(reply is not None for reply in replies)
    assert "ORES" in replies[0]["answer"]
    for reply in (replies[1], replies[2], replies[4]):
        assert "078 78 78 00" in reply["answer"]
        assert "02 274 40 66" not in reply["answer"] and "Bruxelles" not in reply["answer"]
        assert "Fluvius" not in reply["answer"]


def test_wallonia_fr_3_liege_gets_resa_and_no_unverified_number():
    gas = replay(WALLONIA_FR_3)[3]["answer"]
    assert "RESA" in gas and "gaz" in gas and "112" in gas
    # RESA's numbers could not be read on resa.be: no number at all, certainly not ORES's or Sibelga's.
    for number in ("0800 87 087", "078 78 78 00", "0800 19 400", "0800 65 065"):
        assert number not in gas


# --- elderly-3: IGO got the wholesale price, then "IGO alleen is niet automatisch bepalend" ----------

ELDERLY_3 = [
    "Dag mevrouw. Mijn man is vorig jaar gestorven. Ik krijg nu die IGO, dat inkomensgarantie voor ouderen. "
    "Heb ik dan recht op een goedkoper tarief voor mijn stroom?",
    "Nee, ik bedoel niet de prijzen. Ik bedoel het sociaal tarief. Krijg ik dat met de IGO?",
    "Mijn kleinzoon zei dat het met de IGO vanzelf gaat. Is dat waar? Moet ik niets aanvragen?",
    "Ik heb geen computer. Wie kan ik bellen om te vragen of ik dat sociaal tarief krijg?",
]


def test_elderly_3_igo_gives_the_social_tariff_automatically_and_a_phone_number():
    replies = replay(ELDERLY_3)
    for reply in replies:
        assert reply["processing"] == "safety-social-tariff"
        assert "automatisch" in reply["answer"] and "0800 120 33" in reply["answer"]
        assert "groothandel" not in reply["answer"] and "cent" not in reply["answer"]
        assert "gewest" not in reply["answer"]  # federal, not regional
    assert replies[1]["answer"].startswith("Ja: met de IGO")


# --- social-4: "gaan ze mijn kinderen afpakken?" got "Daar kan ik niet bij helpen" -------------------

def test_social_4_shame_about_the_ocmw_gets_a_warm_answer_and_another_way():
    reply = answer_safety_question("ik schaam me om naar het ocmw te gaan voor mijn energierekening. is er een andere manier?")
    assert reply["processing"] == "safety-distress"
    assert "begrijpelijk" in reply["answer"] and "afbetalingsplan" in reply["answer"] and "0800 120 33" in reply["answer"]


def test_social_4_fear_for_the_children_is_answered_not_refused():
    history = [{"question": "ik schaam me om naar het ocmw te gaan voor mijn energierekening."}]
    reply = answer_safety_question("gaan ze mijn kinderen afpakken als ik schulden heb?", history)
    assert reply["processing"] == "safety-distress"
    assert "Daar kan ik niet bij helpen" not in reply["answer"]
    assert "OCMW" in reply["answer"] and "CAW" in reply["answer"]
    assert "Brussel" not in reply["answer"] and "Elegant" not in reply["answer"]


# --- social-1: "ik ben echt bang" got "huisarts" and "112" -----------------------------------------

def test_social_1_fear_about_a_bill_gets_concrete_steps_not_a_crisis_line():
    history = [
        {"question": "hoi. ik kan mijn energiefactuur niet betalen. de leverancier stuurt al herinneringen."},
        {"question": "kunnen ze mij afsluiten? ik heb 2 kinderen"},
    ]
    reply = answer_safety_question("ik ben echt bang. wat kan ik nu concreet doen deze week?", history)
    assert reply["processing"] == "safety-distress"
    assert "afbetalingsplan" in reply["answer"] and "OCMW" in reply["answer"]
    assert "112" not in reply["answer"] and "huisarts" not in reply["answer"]


def test_social_1_disconnection_without_a_region_asks_for_it():
    reply = answer_safety_question("kunnen ze mij afsluiten? ik heb 2 kinderen")
    assert reply["processing"] == "safety-winter" and "Vlaanderen, Brussel of Wallonië" in reply["answer"]


def test_self_harm_gets_the_suicide_line():
    reply = answer_safety_question("ik kan niet meer, ik wil niet meer leven met al die schulden")
    assert "1813" in reply["answer"] and "112" in reply["answer"]


# --- social-5: "alleen voor beschermde afnemers", "1 november tot 31 maart" for Antwerp ----------------

SOCIAL_5 = [
    "het wordt winter. ik woon in antwerpen en heb betaalachterstand. kunnen ze mijn verwarming afsluiten?",
    "is er geen winterstop of afsluitverbod? van wanneer tot wanneer?",
    "geldt dat voor iedereen of alleen voor beschermde afnemers? ben ik dat met leefloon?",
]


def test_social_5_antwerp_gets_the_flemish_winter_rule_for_everyone():
    for reply in replay(SOCIAL_5):
        assert reply["processing"] == "safety-winter"
        assert "1 december en 1 april" in reply["answer"]
        assert "iedereen" in reply["answer"]
        assert "1 november" not in reply["answer"] and "ORES" not in reply["answer"] and "Wallonië" not in reply["answer"]


def test_brussels_and_wallonia_winter_rules_are_not_guessed():
    for town in ("Ixelles", "Namur"):
        reply = answer_safety_question(f"J'habite à {town}. Peuvent-ils me couper le gaz en hiver ?")
        assert reply["processing"] == "safety-winter"
        assert "1er décembre" not in reply["answer"] and "pas vérifié" in reply["answer"]


@pytest.mark.parametrize("question, history", [
    ("hoi. ik kan mijn energiefactuur niet betalen. de leverancier stuurt al herinneringen. wat gaat er nu gebeuren?", []),
    ("De Belastingdienst? Maar het is toch een rekening van Luminus. Moet ik dan Luminus vragen om in stukjes te betalen?", []),
    ("ik woon in antwerpen, niet wallonie. wie helpt mij dan in vlaanderen?",
     ["het wordt winter. ik heb betaalachterstand.", "en als ik een budgetmeter heb en het geld is op?"]),
])
def test_payment_trouble_gets_the_supplier_the_ocmw_and_the_social_tariff(question, history):
    # social-1/1 and social-5/5 got "Dat weet ik niet zeker"; elderly-1 got "de Vlaamse Belastingdienst".
    reply = answer_safety_question(question, [{"question": q} for q in history])
    assert reply["processing"] == "safety-payment-plan"
    assert "leverancier" in reply["answer"] and "OCMW" in reply["answer"] and "Belastingdienst" not in reply["answer"]


# --- region detection --------------------------------------------------------------------------------

@pytest.mark.parametrize("question, region, operator", [
    ("ik woon in antwerpen, niet wallonie. wie helpt mij dan?", "flanders", None),
    ("Je ne suis pas à Bruxelles mais à Namur, chez ORES.", "wallonia", "ORES"),
    ("Hallo, ik woon op de Kiezelstraat 999, 3500 Hasselt.", "flanders", None),
    ("je suis chez Engie en variable depuis 3 ans", None, None),
    ("J'habite à Louvain-la-Neuve", "wallonia", "ORES"),
    ("Ik woon in Leuven", "flanders", None),
])
def test_the_region_comes_from_the_visitors_own_words(question, region, operator):
    assert visitor_region(question, []) == (region, operator)


def test_karens_own_answers_never_set_the_region():
    # Only questions count: an earlier Karen answer that wrongly said "Wallonië" changes nothing.
    history = [{"question": "kunnen ze mijn stroom afsluiten?", "answer": "In Wallonië geldt ... ORES ..."}]
    reply = answer_safety_question("geldt dat in de winter ook?", history)
    assert reply is None or "ORES" not in reply["answer"]


# --- questions the safety layer must leave alone -----------------------------------------------------

@pytest.mark.parametrize("question", [
    "Wat kost stroom vandaag?",
    "Ik ben bang dat de prijzen deze winter stijgen, vast of variabel?",
    "Is 3500 kWh veel?",
    "Salut Karen, je suis chez Engie en variable depuis 3 ans à Saint-Gilles. Je passe au fixe ?",
    "Hoe werkt het capaciteitstarief?",
    "Wat is het verschil tussen een voorschot en een jaarafrekening?",
    "Tu disais que le fournisseur social c'est Sibelga. Donc si je deviens client protégé, c'est Sibelga qui me fournit ?",
])
def test_other_questions_are_left_to_the_other_routes(question):
    assert answer_safety_question(question) is None


def test_a_false_premise_about_the_social_tariff_is_corrected():
    reply = answer_safety_question("Sinds januari is het sociaal tarief afgeschaft, toch?")
    assert reply["answer"].startswith("Nee, het sociaal tarief bestaat nog.")


def test_an_unverified_rule_is_not_stated_as_fact():
    reply = answer_safety_question("Ik krijg een verhoogde tegemoetkoming. Heb ik recht op het sociaal tarief?")
    assert "niet officieel kunnen nakijken" in reply["answer"]


# --- the route: safety comes before every model and price list --------------------------------------

def test_the_api_answers_a_safety_question_without_the_model(api):  # noqa: F811
    client, app, hosted = api
    reply = client.post("/api/answer", json={
        "question": "Merci. S'il y a une panne de courant chez moi, quel numéro dois-je appeler ?",
        "history": [{"question": "Bonjour, j'habite à Namur.", "answer": "Bonjour !"}],
    }).json()
    assert reply["processing"] == "safety-emergency" and "078 78 78 00" in reply["answer"]
    hosted.assert_not_called()
