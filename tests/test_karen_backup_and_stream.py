import io
import json
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from luminus_harness import comparison_session as comparison
from luminus_harness import gemma_answer
from luminus_harness.rate_limit import SlidingWindowRateLimiter


def unavailable(*_args):
    raise gemma_answer.GemmaUnavailableError("Gemma is tijdelijk overbelast of de API-limiet is bereikt.")


def test_overloaded_gemma_falls_back_to_the_backup_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "_generate_gemma", unavailable)
    monkeypatch.setattr(gemma_answer, "_generate_backup", lambda prompt, system: f"backup: {prompt}")
    deltas = []
    assert gemma_answer._generate("vraag") == "backup: vraag"
    assert gemma_answer._generate("vraag", gemma_answer._SYSTEM_INSTRUCTION, deltas.append) == "backup: vraag"
    assert deltas == ["backup: vraag"]


def test_no_backup_without_a_key_or_for_a_configuration_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(gemma_answer, "_generate_gemma", unavailable)
    monkeypatch.setattr(gemma_answer, "_generate_backup", lambda *_: pytest.fail("no backup without a key"))
    with pytest.raises(gemma_answer.GemmaUnavailableError):
        gemma_answer._generate("vraag")

    def rejected(*_args):
        raise gemma_answer.GemmaAPIError("Gemma heeft de API-sleutel geweigerd; controleer GEMINI_API_KEY.")

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "_generate_gemma", rejected)
    with pytest.raises(gemma_answer.GemmaAPIError, match="geweigerd"):
        gemma_answer._generate("vraag")


def test_a_failing_backup_reports_the_gemma_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "_generate_gemma", unavailable)

    def broken(*_args):
        raise RuntimeError("openai down")

    monkeypatch.setattr(gemma_answer, "_generate_backup", broken)
    with pytest.raises(gemma_answer.GemmaUnavailableError, match="overbelast"):
        gemma_answer._generate("vraag")


@pytest.mark.parametrize("code", [429, 500, 503])
def test_overload_and_server_errors_count_as_unavailable(monkeypatch, code):
    monkeypatch.setenv("GEMINI_API_KEY", "test")

    def fail(*_args, **_kwargs):
        raise HTTPError("https://example.test", code, "busy", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr(gemma_answer, "urlopen", fail)
    with pytest.raises(gemma_answer.GemmaUnavailableError):
        gemma_answer._generate_gemma("vraag")


class FakeStream:
    def __init__(self, lines):
        self.lines = lines

    def __enter__(self):
        return iter(self.lines)

    def __exit__(self, *_args):
        return False


def chunk(*parts):
    return ("data: " + json.dumps({"candidates": [{"content": {"parts": list(parts)}}]}) + "\r\n").encode()


def test_streamed_gemma_text_arrives_piece_by_piece_without_thoughts(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    requests = []

    def stream(request, timeout):
        requests.append(request.full_url)
        return FakeStream([
            chunk({"text": "denken", "thought": True}),
            b"\r\n",
            chunk({"text": "  Het sociaal "}),
            chunk({"text": "tarief is een korting."}),
        ])

    monkeypatch.setattr(gemma_answer, "urlopen", stream)
    deltas = []
    text = gemma_answer._generate_gemma("vraag", gemma_answer._SYSTEM_INSTRUCTION, deltas.append)
    assert requests[0].endswith(":streamGenerateContent?alt=sse")
    assert deltas == ["Het sociaal ", "tarief is een korting."]
    assert text == "Het sociaal tarief is een korting."


@pytest.fixture
def api(monkeypatch):
    import energy_voice_app as app

    monkeypatch.setattr(app, "_comparison_sessions", comparison.ComparisonSessions())
    monkeypatch.setattr(app, "_api_rate_limiter", SlidingWindowRateLimiter(requests=100, window_seconds=60, max_clients=100))
    with TestClient(app.api_app) as client:
        yield app, client


def lines(response):
    return [json.loads(line) for line in response.text.splitlines()]


def test_answer_stream_sends_text_then_the_full_reply(api, monkeypatch):
    app, client = api

    async def answer(_body):
        send = app.answer_stream.get()
        send("Het sociaal ")
        send("tarief.")
        return {"answer": "Het sociaal tarief.", "sources": [], "processing": "hosted-public-knowledge"}

    monkeypatch.setattr(app, "_answer", answer)
    response = client.post("/api/answer-stream", json={"question": "Wat is het sociaal tarief?"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = lines(response)
    assert events[:2] == [{"delta": "Het sociaal "}, {"delta": "tarief."}]
    assert events[2]["reply"]["answer"] == "Het sociaal tarief."
    assert events[2]["reply"]["feedback_token"]
    assert app.answer_stream.get() is None, "The stream target must not leak outside the request"


def test_answer_stream_reports_errors_as_a_line(api, monkeypatch):
    app, client = api

    async def answer(_body):
        raise app.HTTPException(status_code=503, detail="Gemma is tijdelijk overbelast of de API-limiet is bereikt.")

    monkeypatch.setattr(app, "_answer", answer)
    events = lines(client.post("/api/answer-stream", json={"question": "Wat is het sociaal tarief?"}))
    assert events == [{"error": "Gemma is tijdelijk overbelast of de API-limiet is bereikt.", "status": 503}]


def test_text_written_in_a_worker_thread_arrives_before_the_reply(api, monkeypatch):
    app, client = api

    async def answer(_body):
        send = app.answer_stream.get()
        await app.asyncio.to_thread(lambda: [send(piece) for piece in ("Een ", "twee ", "drie.")])
        return {"answer": "Een twee drie.", "sources": [], "processing": "hosted-public-knowledge"}

    monkeypatch.setattr(app, "_answer", answer)
    events = lines(client.post("/api/answer-stream", json={"question": "Tel tot drie over energie"}))
    assert [event.get("delta") for event in events[:3]] == ["Een ", "twee ", "drie."], events
    assert events[3]["reply"]["answer"] == "Een twee drie."


@pytest.mark.parametrize("pieces", [
    ["thoughtGroene stroom is ", "hernieuwbaar."],
    ["thou", "ght\n", "Groene stroom is ", "hernieuwbaar."],
    ["  thought Groene stroom is hernieuwbaar."],
])
def test_a_leaked_thought_label_never_reaches_the_answer(monkeypatch, pieces):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "urlopen", lambda request, timeout: FakeStream([chunk({"text": piece}) for piece in pieces]))
    deltas = []
    text = gemma_answer._generate_gemma("vraag", gemma_answer._SYSTEM_INSTRUCTION, deltas.append)
    assert text.startswith("Groene stroom") and "".join(deltas) == text


def test_short_answers_and_real_words_are_kept():
    assert gemma_answer._drop_thought_marker("thoughtful advice") == "thoughtful advice"
    assert gemma_answer._drop_thought_marker("Ja.") == "Ja."
    assert gemma_answer._drop_thought_marker("thought\nJa.") == "Ja."


def test_a_short_streamed_answer_is_not_lost(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "urlopen", lambda request, timeout: FakeStream([chunk({"text": "Ja."})]))
    deltas = []
    assert gemma_answer._generate_gemma("vraag", gemma_answer._SYSTEM_INSTRUCTION, deltas.append) == "Ja."
    assert deltas == ["Ja."]
