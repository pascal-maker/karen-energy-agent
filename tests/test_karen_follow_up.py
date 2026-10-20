from unittest.mock import AsyncMock

import pytest

from luminus_harness import gemma_answer, market_prices
from tests.test_karen_gas_prices import with_gas
from tests.test_karen_market_prices import api  # noqa: F401  (fixture)

HISTORY = [{"question": "Wat kost stroom vandaag?", "answer": "De groothandelsprijs is 22,2 cent per kWh."}]


@pytest.mark.asyncio
async def test_short_follow_up_is_rewritten_with_the_conversation(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    prompts = []

    def generate(prompt, instruction):
        prompts.append((prompt, instruction))
        return ' "Wat zijn de gasprijzen?" '

    monkeypatch.setattr(gemma_answer, "_generate", generate)
    assert await gemma_answer.standalone_question("en voor gas?", HISTORY) == "Wat zijn de gasprijzen?"
    prompt, instruction = prompts[0]
    assert "GEBRUIKER: Wat kost stroom vandaag?" in prompt and "KAREN: De groothandelsprijs" in prompt
    assert prompt.endswith("NIEUW BERICHT:\nen voor gas?")
    assert instruction is gemma_answer._FOLLOW_UP_INSTRUCTION


@pytest.mark.asyncio
@pytest.mark.parametrize("question, history, key", [
    ("en voor gas?", [], "test"),
    ("en voor gas?", HISTORY, ""),
    (" ".join(["woord"] * 16), HISTORY, "test"),
])
async def test_no_rewrite_without_history_key_or_for_long_questions(monkeypatch, question, history, key):
    monkeypatch.setenv("GEMINI_API_KEY", key)
    monkeypatch.setattr(gemma_answer, "_generate", lambda *_: pytest.fail("no Gemma call expected"))
    assert await gemma_answer.standalone_question(question, history) == question


@pytest.mark.asyncio
@pytest.mark.parametrize("output", ["", "x" * 301])
async def test_unusable_rewrites_keep_the_original(monkeypatch, output):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "_generate", lambda *_: output)
    assert await gemma_answer.standalone_question("en voor gas?", HISTORY) == "en voor gas?"


@pytest.mark.asyncio
async def test_gemma_errors_keep_the_original(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")

    def fail(*_):
        raise gemma_answer.GemmaAPIError("down")

    monkeypatch.setattr(gemma_answer, "_generate", fail)
    assert await gemma_answer.standalone_question("en voor gas?", HISTORY) == "en voor gas?"


def test_api_answers_a_follow_up_from_the_rewritten_question(api, monkeypatch):  # noqa: F811
    import energy_voice_app as app

    rewrite = AsyncMock(return_value="Wat zijn de gasprijzen?")
    monkeypatch.setattr(app, "standalone_question", rewrite)
    monkeypatch.setattr(market_prices, "load_market_data", AsyncMock(return_value=with_gas()))
    response = api[0].post("/api/answer", json={"question": "en voor gas?", "history": HISTORY})
    assert response.status_code == 200, response.text
    assert response.json()["processing"] == "market-data"
    rewrite.assert_awaited_once_with("en voor gas?", HISTORY)


def test_api_limits_the_history(api):  # noqa: F811
    too_long = HISTORY * 7
    assert api[0].post("/api/answer", json={"question": "en voor gas?", "history": too_long}).status_code == 422
