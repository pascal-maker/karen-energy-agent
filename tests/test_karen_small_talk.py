import pytest

from luminus_harness import gemma_answer
from luminus_harness.knowledge import KnowledgeChunk


@pytest.mark.parametrize("message, kind", [
    ("hey hoe gaat het ermee?", "how_are_you"),
    ("Hallo", "greeting"),
    ("Goedemorgen Karen!", "greeting"),
    ("dankjewel!", "thanks"),
    ("Oké, bedankt hoor", "thanks"),
    ("Tot ziens", "goodbye"),
    ("Wie ben jij?", "about"),
    ("hoi, wat kan je allemaal?", "about"),
])
def test_small_talk_is_recognised(message, kind):
    assert gemma_answer.small_talk_kind(message) == kind


@pytest.mark.parametrize("message", [
    "Hoi, waarom is mijn voorschot hoger dan vorig jaar?",
    "Hallo, wat kost stroom vandaag?",
    "Dag, hoe vaak moet mijn verwarmingsketel onderhouden worden?",
    "Wanneer kan ik mijn contract opzeggen?",
    "Hoe hoog is de stroomprijs?",
])
def test_real_questions_after_a_greeting_keep_the_source_path(message):
    assert gemma_answer.small_talk_kind(message) is None


@pytest.mark.asyncio
async def test_greeting_chats_through_gemma_without_sources(monkeypatch):
    calls = []

    def generate(prompt, system_instruction=None):
        calls.append((prompt, system_instruction))
        return "Met mij gaat het goed, dank je! En met jou?"

    async def no_search(*_args, **_kwargs):
        raise AssertionError("Small talk must not search energy sources")

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemma_answer, "_generate", generate)
    monkeypatch.setattr(gemma_answer, "search_energy_pages", no_search)
    monkeypatch.setattr(gemma_answer, "fetch_energy_page", no_search)
    result = await gemma_answer.answer_energy_question("hey hoe gaat het ermee?", [])
    assert result == {
        "answer": "Met mij gaat het goed, dank je! En met jou?",
        "sources": [],
        "live_source_status": "not-needed",
        "processing": "small-talk",
    }
    assert calls[0][1] is gemma_answer._SMALL_TALK_INSTRUCTION
    assert "hey hoe gaat het ermee?" in calls[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_small_talk_falls_back_to_a_fixed_reply(monkeypatch, configured):
    def failing_generate(_prompt, _system_instruction=None):
        raise gemma_answer.GemmaAPIError("Gemma kon nu geen verbinding maken.")

    if configured:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    else:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(gemma_answer, "_generate", failing_generate)
    result = await gemma_answer.answer_energy_question("dankjewel!", [])
    assert result["answer"] == gemma_answer._SMALL_TALK_FALLBACK["thanks"]
    assert result["processing"] == "small-talk"


@pytest.mark.asyncio
async def test_unrelated_question_without_sources_explains_what_karen_does(monkeypatch):
    def generate(prompt, system_instruction=None):
        assert system_instruction is gemma_answer._SMALL_TALK_INSTRUCTION
        return "Daar help ik niet bij, maar wel met je energiefactuur."

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemma_answer, "_generate", generate)
    result = await gemma_answer.answer_energy_question("Wie wint de Ronde van Vlaanderen?", [])
    assert result["processing"] == "small-talk"
    assert result["sources"] == []


@pytest.mark.asyncio
async def test_energy_question_without_sources_still_says_so(monkeypatch):
    async def no_pages(*_args, **_kwargs):
        return []

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemma_answer, "search_energy_pages", no_pages)
    monkeypatch.setattr(gemma_answer, "_generate", lambda *_: pytest.fail("No sources means no generation"))
    chunks = [KnowledgeChunk(page=1, text="Iets over isolatiepremies.", urls=(), source="faq.pdf")]
    result = await gemma_answer.answer_energy_question("Welke premie krijg ik voor een warmtepomp in Wallonië?", chunks)
    assert result["answer"] == gemma_answer._NO_SOURCE_REPLY["nl"]
