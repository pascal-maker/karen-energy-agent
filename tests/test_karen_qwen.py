"""Qwen4-Max as Karen's answer model, in place of Gemma.

Order: Qwen when QWEN_API_KEY is set; Gemma (GEMINI_API_KEY) and then the OpenAI backup only when Qwen is
unavailable. No real call leaves the test: urlopen is replaced."""

import io
import json
from urllib.error import HTTPError, URLError

import pytest

from luminus_harness import gemma_answer


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def qwen(monkeypatch):
    """Records each request to the Qwen endpoint and answers from a list."""
    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "QWEN_BASE_URL", "QWEN_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("QWEN_API_KEY", "test-key")
    sent, answers = [], []

    def urlopen(request, timeout=None):
        sent.append(request)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)

    monkeypatch.setattr(gemma_answer, "urlopen", urlopen)
    monkeypatch.setattr(gemma_answer, "_generate_gemma", lambda *_: pytest.fail("no Gemma call while Qwen answers"))
    monkeypatch.setattr(gemma_answer, "_generate_backup", lambda *_: pytest.fail("no backup call while Qwen answers"))
    return sent, answers


def completion(text, usage=None):
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": text}}],
                       "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}}).encode()


def stream(*pieces):
    lines = [f"data: {json.dumps({'choices': [{'delta': {'content': piece}}]})}\n\n" for piece in pieces]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def test_with_a_qwen_key_qwen_answers(qwen):
    sent, answers = qwen
    answers.append(completion("Een kWh is 3.600 kJ."))
    assert gemma_answer._generate("vraag", "instructie") == "Een kWh is 3.600 kJ."
    request = sent[0]
    body = json.loads(request.data)
    assert request.full_url == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer test-key"
    assert body["model"] == "qwen4-max"
    assert body["messages"] == [{"role": "system", "content": "instructie"}, {"role": "user", "content": "vraag"}]
    assert body["stream"] is False


def test_model_and_address_can_be_set(qwen, monkeypatch):
    sent, answers = qwen
    monkeypatch.setenv("QWEN_MODEL", "qwen4-max-preview")
    monkeypatch.setenv("QWEN_BASE_URL", "https://example.test/v1/")
    answers.append(completion("ok"))
    gemma_answer._generate("vraag")
    assert sent[0].full_url == "https://example.test/v1/chat/completions"
    assert json.loads(sent[0].data)["model"] == "qwen4-max-preview"


def test_qwen_streams_piece_by_piece(qwen):
    sent, answers = qwen
    answers.append(stream("Een ", "kWh ", "is 3.600 kJ."))
    deltas = []
    assert gemma_answer._generate("vraag", gemma_answer._SYSTEM_INSTRUCTION, deltas.append) == "Een kWh is 3.600 kJ."
    assert deltas == ["Een ", "kWh ", "is 3.600 kJ."]
    assert json.loads(sent[0].data)["stream"] is True


def test_qwen_thinking_is_never_shown(qwen):
    _, answers = qwen
    answers.append(json.dumps({"choices": [{"message": {"content": "<think>reken</think>Een kWh is 3.600 kJ.",
                                                         "reasoning_content": "eerst rekenen"}}]}).encode())
    assert gemma_answer._generate("vraag") == "Een kWh is 3.600 kJ."
    answers.append(stream("<think>", "reken", "</think>", "Een kWh ", "is 3.600 kJ."))
    deltas = []
    assert gemma_answer._generate("vraag", gemma_answer._SYSTEM_INSTRUCTION, deltas.append) == "Een kWh is 3.600 kJ."
    assert "".join(deltas) == "Een kWh is 3.600 kJ."


@pytest.mark.parametrize("failure", [
    HTTPError("u", 429, "busy", {}, io.BytesIO(b"")), HTTPError("u", 503, "down", {}, io.BytesIO(b"")),
    HTTPError("u", 401, "key", {}, io.BytesIO(b"")), HTTPError("u", 400, "bad model", {}, io.BytesIO(b"")),
    HTTPError("u", 404, "wrong address", {}, io.BytesIO(b"")), URLError("no route"), TimeoutError(),
])
def test_unavailable_qwen_falls_back_to_the_backup_model(qwen, monkeypatch, failure):
    _, answers = qwen
    answers.append(failure)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "_generate_backup", lambda prompt, system: f"backup: {prompt}")
    assert gemma_answer._generate("vraag") == "backup: vraag"


def test_unavailable_qwen_falls_back_to_gemma_first_when_gemma_has_a_key(qwen, monkeypatch):
    _, answers = qwen
    answers.append(HTTPError("u", 503, "down", {}, io.BytesIO(b"")))
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "_generate_gemma", lambda prompt, system, on_delta=None: f"gemma: {prompt}")
    assert gemma_answer._generate("vraag") == "gemma: vraag"


def test_unavailable_qwen_without_another_model_says_so(qwen):
    _, answers = qwen
    answers.append(HTTPError("u", 503, "down", {}, io.BytesIO(b"")))
    with pytest.raises(gemma_answer.GemmaUnavailableError):
        gemma_answer._generate("vraag")


def test_a_broken_stream_after_text_is_shown_does_not_switch_models(qwen, monkeypatch):
    _, answers = qwen
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    class Broken(FakeResponse):
        def __iter__(self):
            yield f"data: {json.dumps({'choices': [{'delta': {'content': 'Half'}}]})}\n".encode()
            raise TimeoutError()

    answers.append(b"")
    monkeypatch.setattr(gemma_answer, "urlopen", lambda request, timeout=None: Broken(b""))
    with pytest.raises(gemma_answer.GemmaAPIError) as raised:
        gemma_answer._generate("vraag", gemma_answer._SYSTEM_INSTRUCTION, lambda piece: None)
    assert not isinstance(raised.value, gemma_answer.GemmaUnavailableError), "Half an answer is on screen: no second model"


def test_an_empty_qwen_answer_counts_as_unavailable(qwen, monkeypatch):
    _, answers = qwen
    answers.append(completion("  "))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "_generate_backup", lambda prompt, system: "backup")
    assert gemma_answer._generate("vraag") == "backup"


def test_without_a_qwen_key_nothing_changes(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "urlopen", lambda *_a, **_k: pytest.fail("no Qwen call without its key"))
    monkeypatch.setattr(gemma_answer, "_generate_gemma", lambda prompt, system, on_delta=None: f"gemma: {prompt}")
    assert gemma_answer._generate("vraag") == "gemma: vraag"


def test_a_qwen_key_alone_counts_as_a_configured_model(monkeypatch):
    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("QWEN_API_KEY", "test")
    assert gemma_answer._model_configured()


def test_qwen_usage_reaches_langfuse(qwen, monkeypatch):
    _, answers = qwen
    noted = []
    monkeypatch.setattr(gemma_answer.langfuse_trace, "note_generation", lambda **kwargs: noted.append(kwargs))
    answers.append(completion("ok", {"prompt_tokens": 12, "completion_tokens": 3}))
    gemma_answer._generate("vraag")
    assert noted[-1]["model"] == "qwen4-max"
    assert noted[-1]["usage_details"] == {"input": 12, "output": 3}


def test_health_names_the_model_that_answers(monkeypatch):
    from fastapi.testclient import TestClient

    import energy_voice_app

    monkeypatch.setenv("QWEN_API_KEY", "test")
    monkeypatch.setenv("QWEN_MODEL", "qwen4-max")
    assert TestClient(energy_voice_app.api_app).get("/api/health").json()["answer_model"] == "qwen4-max"
    monkeypatch.delenv("QWEN_API_KEY")
    assert TestClient(energy_voice_app.api_app).get("/api/health").json()["answer_model"].startswith("gemma")


def test_the_privacy_text_names_qwen():
    from pathlib import Path

    text = Path("energyagent-karen-prototype/EnergyAgentConversation.tsx").read_text()
    assert "Qwen" in text and "Alibaba" in text


# --- live, 24 Nov 2026: Qwen failed and gpt-5-mini answered, with no trace of why ---------------------------

def _http_error(status, body):
    return HTTPError("u", status, "err", {}, io.BytesIO(json.dumps(body).encode()))


@pytest.mark.parametrize("status, body, expected", [
    (401, {"error": {"code": "invalid_api_key", "message": "Incorrect API key provided."}}, "HTTP 401 invalid_api_key"),
    (404, {"error": {"code": "model_not_found", "message": "The model `qwen4-max` does not exist."}}, "HTTP 404 model_not_found"),
    (400, {"code": "InvalidParameter", "message": "url error"}, "HTTP 400 InvalidParameter"),
    (503, {}, "HTTP 503"),
])
def test_why_qwen_failed_is_kept_and_shown_in_health(qwen, monkeypatch, status, body, expected):
    from fastapi.testclient import TestClient

    import energy_voice_app

    _, answers = qwen
    answers.append(_http_error(status, body))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "_generate_backup", lambda prompt, system: "backup")
    assert gemma_answer._generate("vraag") == "backup"
    assert gemma_answer.qwen_problem().startswith(expected)
    health = TestClient(energy_voice_app.api_app).get("/api/health").json()
    assert health["answer_model_problem"].startswith(expected)


def test_a_qwen_answer_clears_the_problem(qwen, monkeypatch):
    _, answers = qwen
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "_generate_backup", lambda prompt, system: "backup")
    answers.extend([_http_error(503, {}), completion("ok")])
    gemma_answer._generate("vraag")
    assert gemma_answer.qwen_problem()
    gemma_answer._generate("vraag")
    assert gemma_answer.qwen_problem() == ""


def test_the_problem_never_holds_a_key(qwen, monkeypatch):
    _, answers = qwen
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "_generate_backup", lambda prompt, system: "backup")
    answers.append(_http_error(401, {"error": {"code": "invalid_api_key",
                                               "message": "Incorrect API key provided: sk-abcdef1234567890abcdef. Bearer test-key"}}))
    gemma_answer._generate("vraag")
    problem = gemma_answer.qwen_problem()
    assert "sk-abcdef" not in problem and "test-key" not in problem and "invalid_api_key" in problem


@pytest.mark.parametrize("failure, expected", [(URLError("Name or service not known"), "geen verbinding"), (TimeoutError(), "geen verbinding")])
def test_a_connection_problem_is_named_too(qwen, monkeypatch, failure, expected):
    _, answers = qwen
    answers.append(failure)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "_generate_backup", lambda prompt, system: "backup")
    gemma_answer._generate("vraag")
    assert expected in gemma_answer.qwen_problem()


def test_the_fallback_is_recorded_in_langfuse(qwen, monkeypatch):
    from contextlib import contextmanager

    _, answers = qwen
    steps = []

    @contextmanager
    def step(name, as_type="span", **fields):
        steps.append((name, fields))
        yield None

    monkeypatch.setattr(gemma_answer.langfuse_trace, "step", step)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(gemma_answer, "_generate_backup", lambda prompt, system: "backup")
    answers.append(_http_error(401, {"error": {"code": "invalid_api_key", "message": "bad"}}))
    gemma_answer._generate("vraag")
    fallback = [fields for name, fields in steps if name == "qwen-unavailable"]
    assert fallback and fallback[0]["level"] == "WARNING" and "invalid_api_key" in fallback[0]["status_message"]


def test_health_shows_no_problem_without_a_qwen_key(monkeypatch):
    from fastapi.testclient import TestClient

    import energy_voice_app

    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    assert "answer_model_problem" not in TestClient(energy_voice_app.api_app).get("/api/health").json()
