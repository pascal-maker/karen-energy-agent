"""A question about an uploaded invoice as typed tasks, carried out in code (reported 2026-11-07).

"heb je advies over deze factuur zoek het adres op in deze factuur" got "Ik weet niet zeker wat je wilt weten";
advice repeated the summary; the upload said the bank pays "automatisch" while later answers said Karen cannot
see whether it did. The phrasings below are the visitor's own, typos included. All invoices are fictional.
"""

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from luminus_harness import invoice_intent
from luminus_harness.invoice_intent import Plan, Task, masked_question, parse, parse_locally
from luminus_harness.invoice_schema import advice_answer, payer_answer, payment_status, summarize_invoice
from luminus_harness.invoice_session import open_invoice_session, open_text_session

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"
ADVANCE = "11-nl-voorschot-domiciliering.pdf"  # monthly advance € 179,00, direct debit on 31.10.2026, customer John Doe
DELIVERY_ONLY = "23-nl-leveringsadres.pdf"
NO_ADDRESS = "25-nl-geen-adres.pdf"
ADDRESS = "Teststraat 0, 0000 Teststad"


def _session(name: str):
    return open_invoice_session((FIXTURES / name).read_bytes(), name)


# --- several tasks in one message ----------------------------------------------------------------

@pytest.mark.parametrize("question", [
    "heb je advies over deze factuur zoek het adres op in deze factuur",
    "heb je advies over deze factuur en zoek het adres op",
    "geef advies, en het adress graag",
])
def test_advice_and_address_in_one_question_get_both_answers(question):
    reply = _session(ADVANCE).ask(question)
    answer = reply["answer"]
    assert "Ik weet niet zeker" not in answer
    assert "Mijn advies" in answer and "bankapp" in answer
    assert ADDRESS in answer
    assert answer.index("Mijn advies") < answer.index(ADDRESS), "Answered in the order asked"
    assert reply.get("found", True)


def test_summary_and_address_in_one_question():
    answer = _session(ADVANCE).ask("vat samen en zoek het adres")["answer"]
    assert answer.startswith("Samenvatting van je voorschotfactuur")
    assert ADDRESS in answer


@pytest.mark.parametrize("question", ["zoek het adress op", "wat is het adrs op deze factuur", "zoek het leveringsadrs"])
def test_typos_in_an_address_question(question):
    answer = _session(DELIVERY_ONLY).ask(question)["answer"]
    assert "Ik weet niet zeker" not in answer
    assert "PDF-pagina" in answer


def test_the_clear_part_is_answered_and_the_unclear_part_asked_about_specifically():
    answer = _session(ADVANCE).ask("heb je iets voor me over deze factuur, zoek het adres op")["answer"]
    assert ADDRESS in answer
    assert "Wat wil je weten over „heb je iets voor me over deze factuur“?" in answer
    assert "Stel je vraag" not in answer and "concreter" not in answer


def test_courtesy_is_not_an_unclear_part():
    answer = _session(ADVANCE).ask("zoek het adres op, dank je wel")["answer"]
    assert ADDRESS in answer and "Wat wil je weten" not in answer


def test_one_question_stays_one_task():
    assert [task.type for task in parse_locally("hoeveel btw betaal ik op gas").tasks] == ["question"]
    assert [task.type for task in parse_locally("wie moet betalen?").tasks] == ["payer"]
    for topic in ("wat moet ik doen als ik verhuis?", "tips om te besparen"):
        assert [task.type for task in parse_locally(topic).tasks] == ["question"], "Advice about a topic is a question of its own"
    assert [task.type for task in parse_locally("wacht, eerst: heb je tips?").tasks] == ["advice"]
    plan = parse_locally("geef het leveringsadres en het facturatieadres")
    assert [(task.type, task.address_kind) for task in plan.tasks] == [("address", "delivery"), ("address", "billing")]


# --- advice is actions, not the summary again ---------------------------------------------------

@pytest.mark.parametrize("question", ["heb je advies?", "wat moet ik hiermee doen", "tips?", "zie je aandachtspunten?"])
def test_advice_says_what_to_do_and_what_was_checked(question):
    reply = _session(ADVANCE).ask(question)
    answer = reply["answer"]
    assert reply.get("found", True), "Advice about the bill stays on the bill"
    assert answer.startswith("**Mijn advies:**")
    assert "bankapp" in answer and "afhouding van 31 oktober 2026" in answer
    assert "Pas je voorschot niet aan op basis van alleen deze voorschotfactuur." in answer
    assert "Upload je jaarafrekening: pas dan kan ik beoordelen of € 179,00 per maand voldoende is." in answer
    assert "Nagekeken: de optelling (€ 168,87 + 6% btw = € 179,00)" in answer
    assert "Niet te zien op deze factuur: of de afhouding gelukt is" in answer
    assert "aandachtspunten vond ik niet" not in answer
    summary = summarize_invoice(_session(ADVANCE).invoice)["answer"].splitlines()[1:]
    assert not any(line in answer for line in summary), "Advice never repeats the summary"


def test_advice_on_a_settlement_names_the_payment_and_what_is_missing():
    invoice = _session(NO_ADDRESS).invoice  # settlement, € 172,00 to pay before 10/03/2026, no consumption printed
    answer = advice_answer(invoice)["answer"]
    assert answer.startswith("**Mijn advies:** betaal € 172,00 vóór 10 maart 2026.")
    assert "Niet te zien op deze factuur: je verbruik." in answer
    with_use = advice_answer(_session("01-nl-afrekening-gegroepeerd.pdf").invoice)["answer"]
    assert "meterstanden" in with_use


# --- one payment status for one bill ------------------------------------------------------------

def test_one_bill_has_one_payment_status_everywhere():
    session = _session(ADVANCE)
    upload = session.analysis["answer"]
    answers = [upload, payer_answer(session.invoice)["answer"], advice_answer(session.invoice)["answer"],
               summarize_invoice(session.invoice)["answer"]]
    for answer in answers:
        assert "betaalt je bank" not in answer and "automatisch" not in answer
        assert "31.10.2026" not in answer, "One way of writing the date"
    assert all("31 oktober 2026" in answer for answer in answers)
    assert "domiciliëring aan op 31 oktober 2026" in upload


def test_the_payment_status_follows_the_date():
    invoice = _session(ADVANCE).invoice
    before = payment_status(invoice, today=date(2026, 10, 20))
    after = payment_status(invoice, today=date(2026, 11, 7))
    assert before.state == "debit_announced" and "genoeg op je rekening" in before.action
    assert after.state == "debit_date_passed" and after.action == "Kijk in je bankapp of de afhouding van 31 oktober 2026 gelukt is."
    assert "kan ik niet zien" in after.sentence


# --- the upload answer leads with the figure ----------------------------------------------------

def test_the_upload_answer_of_an_advance_bill_leads_with_the_advance_and_a_year_of_it():
    lead = _session(ADVANCE).analysis["answer"].splitlines()[0]
    assert lead == ("**€ 179,00 per maand aan voorschotten.** Bij hetzelfde voorschot is dat € 2.148,00 per jaar. "
                    "Of dat voldoende is, kan ik pas beoordelen met je jaarafrekening.")


# --- addresses: unknown is not absent -----------------------------------------------------------

def test_an_unlabelled_address_is_unreadable_not_absent():
    text = ("Kanaal Energie — FICTIEF\nJoy Doe\nKerkstraat 12\n9000 Gent\nUw jaarafrekening\n"
            "Klant: Joy Doe\nTotaal te betalen: € 172,00\nVervaldatum: 10/03/2026\n")
    answer = open_text_session(text, "venster.txt").ask("wat is het adres?")["answer"]
    assert answer.startswith("Ik kon het adres niet betrouwbaar lezen")
    assert "Kerkstraat 12, 9000 Gent" in answer


def test_a_bill_without_an_address_says_so():
    answer = _session(NO_ADDRESS).ask("zoek het adres op")["answer"]
    assert "geen adres" in answer and "niet betrouwbaar" not in answer


# --- the optional intent model: masked question in, strict plan out, rules as fallback -----------

@pytest.fixture
def intent_model(monkeypatch):
    monkeypatch.setenv("KAREN_INTENT_MODEL", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    seen: dict = {}

    def install(reply):
        async def fake(masked, context):
            seen["masked"], seen["context"] = masked, context
            if isinstance(reply, Exception):
                raise reply
            return reply
        monkeypatch.setattr(invoice_intent, "_ask_intent_model", fake)
        return seen

    return install


def _plan_for(question, session):
    return asyncio.run(parse(question, session._names(), session._customer_parts(), session.intent_context()))


def test_the_intent_model_sees_only_the_masked_question_and_minimal_context(intent_model):
    session = _session(ADVANCE)
    session.ask("wie moet betalen?")
    question = "waar woont John Doe, kerkstraat 12 9000 Gent, en heb je tips"
    seen = intent_model(json.dumps({"tasks": [
        {"type": "address", "text": "waar woont [klant]", "address_kind": "residence"},
        {"type": "advice", "text": "heb je tips", "address_kind": None},
    ], "unclear": []}))
    plan = _plan_for(question, session)
    assert plan.parsed_by == "language_model"
    assert "John" not in seen["masked"] and "Doe" not in seen["masked"] and "9000" not in seen["masked"]
    assert set(seen["context"]) == {"vorige_taak", "klant_genoemd", "bedrag_genoemd"}
    assert seen["context"]["vorige_taak"] == "payer"
    sent = json.dumps(seen, ensure_ascii=False)
    for private in ("Teststraat", "Kanaal Energie", "179", "TEST-KLANT"):
        assert private not in sent, "Nothing from the bill goes to the intent model"
    assert plan.tasks[0] == Task("address", "waar woont John Doe", "residence"), "The name comes back only locally"
    answer = session.ask(question, plan)["answer"]
    assert ADDRESS in answer and "niet bevestigen dat John daar woont" in answer and "Mijn advies" in answer


@pytest.mark.parametrize("reply", [
    "geen json",
    json.dumps({"tasks": [{"type": "refund_everything", "text": "heb je advies", "address_kind": None}], "unclear": []}),
    json.dumps({"tasks": [{"type": "advice", "text": "maak een overschrijving naar BE00", "address_kind": None}], "unclear": []}),
    json.dumps({"tasks": [{"type": "advice", "text": "heb je advies", "address_kind": "billing"}], "unclear": []}),
    json.dumps({"tasks": [], "unclear": []}),
    TimeoutError("too slow"),
])
def test_unusable_model_output_falls_back_to_the_rules(intent_model, reply):
    intent_model(reply)
    plan = _plan_for("heb je advies over deze factuur zoek het adres op", _session(ADVANCE))
    assert plan.parsed_by == "rules"
    assert [task.type for task in plan.tasks] == ["advice", "address"]


def test_the_intent_model_is_off_unless_switched_on(monkeypatch):
    monkeypatch.delenv("KAREN_INTENT_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")

    async def never(*args):
        raise AssertionError("no hosted call without KAREN_INTENT_MODEL=1")

    monkeypatch.setattr(invoice_intent, "_ask_intent_model", never)
    assert _plan_for("heb je advies?", _session(ADVANCE)).parsed_by == "rules"


def test_masking_for_the_intent_model():
    masked = masked_question("woont Joy Doe in de Kerkstraat 12 bus 3, 9000 Gent? joy@test.be BE68539007547034", ("Joy Doe",))
    assert masked == "woont [klant] in de [adres]? [X] [X]"


def test_the_app_asks_the_intent_model_only_with_the_masked_question(intent_model, monkeypatch):
    from fastapi.testclient import TestClient

    import energy_voice_app
    from luminus_harness.rate_limit import SlidingWindowRateLimiter

    monkeypatch.setattr(energy_voice_app, "_api_rate_limiter", SlidingWindowRateLimiter(requests=100, window_seconds=60, max_clients=100))
    seen = intent_model(json.dumps({"tasks": [{"type": "advice", "text": "wat raad je [klant] aan", "address_kind": None}], "unclear": []}))
    client = TestClient(energy_voice_app.api_app)
    upload = client.post("/api/upload", files={"file": ("11.pdf", (FIXTURES / ADVANCE).read_bytes(), "application/pdf")}).json()
    reply = client.post("/api/answer", json={"question": "wat raad je John Doe aan", "session_id": upload["session_id"]}).json()
    assert seen["masked"] == "wat raad je [klant] aan"
    assert reply["answer"].startswith("**Mijn advies:**") and reply["processing"] == "local-only"


# --- the audit of main 70e2ce1 (2026-11-07): each conversation as found, on synthetic invoices --------

SETTLEMENT = "23-nl-leveringsadres.pdf"  # € 172,00 to pay before 10/03/2026, Opzegtermijn: 3 weken, gas row € 700,00 excl. btw
WITH_USE = "01-nl-afrekening-gegroepeerd.pdf"


def test_audit_1_the_vat_question_gets_the_vat_amount_not_the_total():
    answer = _session(ADVANCE).ask("hoeveel btw betaal ik?")["answer"]
    assert "10,13" in answer and "179,00" not in answer


def test_audit_2_a_gas_question_in_kwh_stays_about_gas():
    for question in ("Wat betaal ik per jaar aan gas (kWh)?", "Wat betaal ik per jaar aan gas?"):
        answer = _session(ADVANCE).ask(question)["answer"]
        assert "€ 1.267,92 excl. btw per jaar" in answer and "2.148" not in answer


def test_audit_3_must_i_still_pay_gives_the_one_payment_status():
    answer = _session(ADVANCE).ask("moet ik nog betalen?")["answer"]
    assert "Je hoeft niets te doen" not in answer and "je bank betaalt" not in answer
    assert "domiciliëring aan op 31 oktober 2026" in answer


def test_audit_4_a_notice_period_is_no_contract_term():
    session = _session(SETTLEMENT)
    contract = session.ask("Hoe lang loopt mijn contract?")["answer"]
    assert contract.startswith("Op deze factuur vond ik geen contractduur.")
    assert "Dat is niet hetzelfde." in contract, "The notice period is shown as what it is"
    assert "3 weken" in session.ask("wat is mijn opzegtermijn?")["answer"]
    assert "10/02/2026" in session.ask("wanneer is de factuur opgemaakt?")["answer"], "Invoice date, not the billing period"
    assert "10/02/2026" in session.ask("wat is de factuurdatum")["answer"]
    due = session.ask("tegen wanneer moet het geld op hun rekening staan?")["answer"]
    assert "10 maart 2026" in due and "01/01/2025" not in due


def test_audit_5_electricity_use_is_a_quantity():
    answer = _session(WITH_USE).ask("Wat is mijn elektriciteitsverbruik?")["answer"]
    assert "kWh" in answer and "EAN" not in answer and "€" not in answer


def test_audit_6_without_vat_means_excluding_vat_also_for_this_amount():
    session = _session(SETTLEMENT)
    assert "**Bedrag excl. btw: € 700,00**" in session.ask("Wat kost gas zonder btw?")["answer"]
    follow_up = session.ask("Is dit bedrag inclusief btw?")["answer"]
    assert follow_up.startswith("Nee: € 700,00") and "42,00" not in follow_up


def test_audit_7_yes_to_a_question_back_resumes_the_task():
    session = _session(SETTLEMENT)
    assert "Bedoel je die persoon?" in session.ask("waar woont ze?")["answer"]
    reply = session.ask("ja")
    assert reply.get("found", True) and "Zonnestraat 12" in reply["answer"]
    other = _session(SETTLEMENT)
    other.ask("waar woont ze?")
    assert other.ask("nee")["answer"].startswith("Over wie gaat het dan?")
    assert _session(SETTLEMENT).ask("ja").get("found", True) is False, "A bare 'ja' with nothing asked is not about the bill"


def test_audit_8_due_date_and_payer_are_both_answered_once():
    reply = _session(SETTLEMENT).ask("wat is de vervaldatum en wie moet betalen?")
    answer = reply["answer"]
    assert reply.get("found", True) and "Joy Doe" in answer
    assert answer.count("10 maart 2026") == 1, "The due date once, not repeated by both tasks"


def test_audit_9_an_address_typo_stays_on_the_bill():
    reply = _session(ADVANCE).ask("wat is het ares")
    assert reply.get("found", True) and ADDRESS in reply["answer"]


# --- review of edc048b ---------------------------------------------------------------------------

@pytest.mark.parametrize("pick, amount", [
    ("de 42 euro", "€ 42,00"), ("42", "€ 42,00"), ("€ 30,00", "€ 30,00"), ("die van gas", "€ 42,00"),
    ("elektriciteit", "€ 30,00"), ("de eerste", "€ 30,00"), ("de laatste", "€ 42,00"),
])
def test_review_choosing_an_amount_resumes_the_pending_question(pick, amount):
    session = _session(SETTLEMENT)
    session.ask("hoeveel is de btw?")
    assert session.ask("is dit bedrag inclusief btw?")["answer"].startswith("Welk bedrag bedoel je?")
    reply = session.ask(pick)
    assert reply.get("found", True), "A pick stays on the bill"
    assert reply["answer"].startswith(f"{amount} is het btw-bedrag zelf")


def test_review_a_new_question_after_which_amount_is_not_a_pick():
    session = _session(SETTLEMENT)
    session.ask("hoeveel is de btw?")
    session.ask("is dit bedrag inclusief btw?")
    answer = session.ask("hoeveel kost gas?")["answer"]
    assert "btw-bedrag zelf" not in answer and "Gas" in answer


def test_review_two_tasks_of_one_type_about_two_energies_stay_two():
    plan = parse_locally("Wat betaal ik per jaar aan gas en wat betaal ik per jaar aan stroom?")
    assert [task.type for task in plan.tasks] == ["yearly", "yearly"]
    answer = _session(ADVANCE).ask("Wat betaal ik per jaar aan gas en wat betaal ik per jaar aan stroom?")["answer"]
    assert "€ 1.267,92 excl. btw per jaar" in answer and "€ 758,52 excl. btw per jaar" in answer
    assert "2.148" not in answer
    assert answer.count("geen voorspelling") == 1, "The caveat once"
    assert len(parse_locally("vat samen, vat samen").tasks) == 1


@pytest.mark.parametrize("question", [
    "zoek het adres Rue des Tests 12, 1000 Testville",
    "woont ze Avenue Louise 54 bte 3?",
    "Chaussée de Wavre 100, 1050 Ixelles",
    "Place Flagey 7",
    "Grote Markt 1, 2000 Antwerpen",
    "is het Oude Baan 5?",
])
def test_review_street_names_never_reach_the_intent_model(question):
    masked = masked_question(question, ())
    for word in ("Tests", "Testville", "Louise", "Wavre", "Ixelles", "Flagey", "Markt", "Antwerpen", "Baan", "54", "100", "12"):
        assert word not in masked
    assert "[adres]" in masked


# --- review round 3: a pick that does not fit asks again; task parameters; masking or no model call ----

def _asked_which(session):
    session.ask("hoeveel is de btw?")
    assert session.ask("is dit bedrag inclusief btw?")["answer"].startswith("Welk bedrag bedoel je?")


@pytest.mark.parametrize("pick", ["de 99 euro", "die van water"])
def test_round3_a_pick_that_matches_no_choice_asks_again(pick):
    session = _session(SETTLEMENT)
    _asked_which(session)
    reply = session.ask(pick)
    assert reply.get("found", True), "Stays on the bill"
    assert reply["answer"].startswith("Dat bedrag stond niet bij mijn keuzes.") and "€ 30,00" in reply["answer"] and "€ 42,00" in reply["answer"]
    assert session.ask("de 42 euro")["answer"].startswith("€ 42,00 is het btw-bedrag zelf"), "The question stays open"


def test_round3_number_and_row_together_pick_and_equal_amounts_ask_which_field():
    from luminus_harness.invoice_session import AmountRef, _choose

    gas = AmountRef("€ 700,00", "Gas — Bedrag excl. btw: € 700,00", "Bedrag excl. btw", 1)
    due = AmountRef("€ 700,00", "Totaal te betalen: € 700,00", "Totaal te betalen", 1)
    vat = AmountRef("€ 42,00", "Gas — Btw 6%: € 42,00", "Btw 6%", 1)
    assert _choose("700", (gas, due, vat)) == [gas, due], "Equal amounts: both fields, so Karen asks which"
    assert _choose("de 700 van gas", (gas, due, vat)) == [gas], "The row narrows the number"
    assert _choose("de 42", (gas, due, vat)) == [vat]
    assert _choose("de 5 euro", (gas, due, vat)) == []


def test_round3_equal_amounts_in_a_pick_ask_which_field():
    from luminus_harness.invoice_session import AmountRef

    session = _session(SETTLEMENT)
    session.pending = Task("this_amount", "is dit bedrag inclusief btw?")
    session.choices = (
        AmountRef("€ 700,00", "Gas — Bedrag excl. btw: € 700,00", "Bedrag excl. btw", 1),
        AmountRef("€ 700,00", "Totaal te betalen: € 700,00", "Totaal te betalen", 1),
    )
    answer = session.ask("700")["answer"]
    assert answer.startswith("Dat bedrag staat in meer dan één veld. Welk bedrag bedoel je:")
    assert "Gas — Bedrag excl. btw" in answer and "Totaal te betalen" in answer
    assert session.ask("die van gas")["answer"].startswith("Nee: € 700,00")


def test_round3_yes_no_for_an_address_still_works_next_to_amount_picks():
    session = _session(SETTLEMENT)
    session.ask("waar woont ze?")
    assert "Zonnestraat 12" in session.ask("ja")["answer"]


@pytest.mark.parametrize("question, expected", [
    ("Wat betaal ik per jaar aan gas en wat betaal ik per jaar aan stroom?", ["€ 1.267,92", "€ 758,52"]),
    ("Wat betaal ik per jaar aan stroom en wat betaal ik per jaar aan gas?", ["€ 758,52", "€ 1.267,92"]),
])
def test_round3_yearly_per_energy_in_the_order_asked(question, expected):
    answer = _session(ADVANCE).ask(question)["answer"]
    assert all(amount in answer for amount in expected) and "2.148" not in answer
    assert answer.index(expected[0]) < answer.index(expected[1])


def test_round3_task_parameters_keep_tasks_apart():
    from luminus_harness.invoice_intent import task_parameters

    assert task_parameters(Task("question", "wat kost gas zonder btw")) != task_parameters(Task("question", "wat kost gas met btw"))
    assert task_parameters(Task("address", "het adres van de leverancier", "address")) != task_parameters(Task("address", "mijn adres", "address"))
    plan = parse_locally("wat kost gas zonder btw en wat kost gas met btw")
    assert len(plan.tasks) + len(plan.unclear) == 2
    assert len(parse_locally("vat samen, vat samen").tasks) == 1, "Identical tasks still merge"


@pytest.mark.parametrize("question, private", [
    ("zoek het adres Kerkstraat 12 bus 3, 9000 Gent en Rue des Tests 12 bte 4, 1000 Testville", ("Kerkstraat", "Gent", "Tests", "Testville", "12", "9000")),
    ("woon ik op nr 12 in de Dorpsstraat?", ("Dorpsstraat", "12")),
    ("is het 1000 Brussel, Rue Haute 5?", ("Brussel", "Haute", "1000")),
    ("Molenlaan 7A, 0001 Proefdorp", ("Molenlaan", "Proefdorp", "7A")),
    ("2000 Antwerpen", ("Antwerpen", "2000")),
])
def test_round3_addresses_are_masked_in_every_order(question, private):
    masked = masked_question(question, ())
    assert not any(word in masked for word in private), masked


def test_round3_when_masking_is_unsure_the_model_is_not_called(intent_model):
    from luminus_harness.invoice_intent import masking_is_safe

    assert masking_is_safe(masked_question("heb je advies?", ())) is True
    assert masking_is_safe("zoek het adres Testhoek 4") is False, "A name with a house number left over: unsure"
    seen = intent_model(json.dumps({"tasks": [{"type": "address", "text": "zoek het adres", "address_kind": "address"}], "unclear": []}))
    plan = _plan_for("zoek het adres Testhoek 4", _session(ADVANCE))
    assert plan.parsed_by == "rules" and "masked" not in seen, "Unsure masking: the local parser decides, nothing is sent"


def test_round3_consumption_never_answers_with_an_ean_or_a_cost():
    for fixture in (SETTLEMENT, ADVANCE):
        for question in ("Wat is mijn elektriciteitsverbruik?", "wat is mijn verbruik?", "hoeveel kWh heb ik verbruikt?"):
            answer = _session(fixture).ask(question)["answer"]
            assert "EAN" not in answer and "[X]" not in answer, (fixture, question, answer)
            assert not any(cost in answer for cost in ("€ 530,00", "€ 500,00", "€ 742,00", "€ 63,21 excl", "Bedrag excl")), (fixture, question, answer)


# --- review of 6a0e903: an amount and a row that contradict each other ask again ----------------------

@pytest.mark.parametrize("pick", ["de 42 van elektriciteit", "de 42 van water", "30 van gas"])
def test_round4_amount_and_row_that_disagree_ask_again(pick):
    session = _session(SETTLEMENT)
    _asked_which(session)
    reply = session.ask(pick)
    assert reply.get("found", True)
    assert reply["answer"].startswith("Dat bedrag en die regel passen niet bij elkaar."), reply["answer"]
    assert "btw-bedrag zelf" not in reply["answer"]
    assert "€ 30,00" in reply["answer"] and "€ 42,00" in reply["answer"], "Both choices offered again"
    assert session.ask("de 42 van gas")["answer"].startswith("€ 42,00 is het btw-bedrag zelf"), "The question stays open"


@pytest.mark.parametrize("pick, amount", [("42 alsjeblieft", "€ 42,00"), ("de 30 euro graag", "€ 30,00"), ("die van gas aub", "€ 42,00")])
def test_round4_courtesy_words_are_no_contradiction(pick, amount):
    session = _session(SETTLEMENT)
    _asked_which(session)
    assert session.ask(pick)["answer"].startswith(f"{amount} is het btw-bedrag zelf")


def test_round4_choose_returns_nothing_when_amount_and_row_disagree():
    from luminus_harness.invoice_session import AmountRef, _choose

    power = AmountRef("€ 30,00", "Elektriciteit — Btw 6%: € 30,00", "Btw 6%", 1)
    gas = AmountRef("€ 42,00", "Gas — Btw 6%: € 42,00", "Btw 6%", 1)
    assert _choose("de 42 van elektriciteit", (power, gas)) == []
    assert _choose("de 42 van gas", (power, gas)) == [gas]
    assert _choose("de 42 van water", (power, gas)) == []


# --- review of 5de97bd: amount, energy and column are separate conditions that must all fit --------------

@pytest.mark.parametrize("pick", [
    "de 42 btw van elektriciteit", "42 btw van water", "de 30 btw van gas", "42 btw elektriciteit",
    "de 42 incl. btw", "42 excl btw van gas", "de btw van water",
])
def test_round5_every_named_condition_must_fit(pick):
    session = _session(SETTLEMENT)
    _asked_which(session)
    answer = session.ask(pick)["answer"]
    assert "btw-bedrag zelf" not in answer, (pick, answer)
    assert answer.startswith(("Dat bedrag en die regel passen niet bij elkaar.", "Dat bedrag stond niet bij mijn keuzes.")), answer
    assert session.ask("de 42 btw van gas")["answer"].startswith("€ 42,00 is het btw-bedrag zelf"), "The question stays open"


@pytest.mark.parametrize("pick, amount", [
    ("de 42 btw van gas", "€ 42,00"), ("30 btw elektriciteit", "€ 30,00"), ("de btw van gas", "€ 42,00"),
    ("de btw van stroom", "€ 30,00"), ("42 btw", "€ 42,00"), ("de 42 euro btw graag", "€ 42,00"),
])
def test_round5_conditions_that_all_fit_pick(pick, amount):
    session = _session(SETTLEMENT)
    _asked_which(session)
    assert session.ask(pick)["answer"].startswith(f"{amount} is het btw-bedrag zelf")


def test_round5_choose_checks_each_condition():
    from luminus_harness.invoice_session import AmountRef, _choose

    power = AmountRef("€ 30,00", "Elektriciteit — Btw 6%: € 30,00", "Btw 6%", 1)
    gas = AmountRef("€ 42,00", "Gas — Btw 6%: € 42,00", "Btw 6%", 1)
    gas_excl = AmountRef("€ 700,00", "Gas — Bedrag excl. btw: € 700,00", "Bedrag excl. btw", 1)
    assert _choose("de 42 btw van elektriciteit", (power, gas)) == []
    assert _choose("42 btw van water", (power, gas)) == []
    assert _choose("de 42 btw van gas", (power, gas)) == [gas]
    assert _choose("die excl btw van gas", (gas, gas_excl)) == [gas_excl]
    assert _choose("de btw van gas", (gas, gas_excl)) == [gas], "'btw' alone names the btw column, not 'excl. btw'"


# --- live retest of main f80b96e: a contract term named in one word, a due date asked as a bare "wanneer" -----

@pytest.mark.parametrize("question", [
    "wat is mijn contractduur?", "wat is de looptijd van mijn contract?", "hoe lang loopt mijn contract?",
    "wat is de duur van mijn contract?",
])
def test_live_contract_term_in_any_wording_names_what_is_there(question):
    answer = _session(SETTLEMENT).ask(question)["answer"]
    assert answer.startswith("Op deze factuur vond ik geen contractduur."), (question, answer)
    assert "Opzegtermijn: 3 weken" in answer and "Dat is niet hetzelfde." in answer


def test_live_expensive_contract_is_no_contract_term():
    from luminus_harness.invoice_retrieval import expected_value

    assert expected_value("is mijn contract duur?") != "duration", "'duur' alone is 'expensive', not a term"


@pytest.mark.parametrize("question", [
    "hoeveel moet ik betalen en wanneer?", "hoeveel moet ik betalen en tegen wanneer?", "hoeveel moet ik betalen, en wanneer?",
])
def test_live_amount_and_bare_when_give_the_due_date_once(question):
    reply = _session(SETTLEMENT).ask(question)
    answer = reply["answer"]
    assert reply.get("found", True) and "€ 172,00" in answer, (question, answer)
    assert answer.count("10 maart 2026") + answer.count("10/03/2026") == 1, (question, answer)


def test_live_bare_when_borrows_the_previous_question():
    from luminus_harness.invoice_intent import parse_locally

    texts = [task.text for task in parse_locally("hoeveel moet ik betalen en tegen wanneer?").tasks]
    assert texts == ["hoeveel moet ik betalen", "tegen wanneer moet ik betalen"], texts
