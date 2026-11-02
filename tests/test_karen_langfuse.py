"""Langfuse tracing: off without keys, only masked public answers, benchmark runs as experiments."""

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from luminus_harness import langfuse_trace
from luminus_harness.conversation_log import build_log_record
from tests.test_karen_market_prices import api  # noqa: F401  (fixture)


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)


@pytest.fixture
def posted(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def urlopen(request, timeout):
        calls.append((request.full_url, dict(request.header_items()), json.loads(request.data)))
        return Response()

    monkeypatch.setattr(langfuse_trace, "urlopen", urlopen)
    return calls


def test_nothing_is_sent_without_keys(monkeypatch, posted):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert not langfuse_trace.enabled()
    langfuse_trace.in_background(lambda: pytest.fail("no tracing without keys"))
    langfuse_trace.send_answer({"id": "x", "question": "q", "answer": "a"})
    assert posted == []


def test_a_public_answer_becomes_one_masked_trace(keys, posted):
    reply = {"answer": "Kies vast.", "processing": "contract-advice", "sources": [{"title": "CREG", "url": "https://creg.be"}]}
    steps = [{"kind": "route", "choice": "contract_advice", "confidence": 0.9, "probabilities": {"contract_advice": 0.9}}]
    record = build_log_record("vast of variabel? mijn mail is jan@example.com", reply, steps, 1.24)
    langfuse_trace.send_answer(record)
    url, headers, body = posted[0]
    assert url == "https://cloud.langfuse.com/api/public/ingestion"
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"pk-lf-test:sk-lf-test").decode()
    event = body["batch"][0]
    assert event["type"] == "trace-create"
    trace = event["body"]
    assert trace["id"] == record["id"] and trace["name"] == "karen-answer"
    assert trace["input"] == "vast of variabel? mijn mail is [verborgen]"
    assert trace["output"] == "Kies vast."
    assert trace["metadata"]["route"] == "contract_advice" and trace["metadata"]["seconds"] == 1.2
    assert trace["tags"] == ["contract-advice", "contract_advice"]


def test_a_benchmark_run_is_a_dataset_experiment_with_jevs_scores(keys, posted):
    run = {
        "id": "abc123", "started_at": "2026-11-02T14:14:00+00:00", "summary": {"karen_avg": 0.58},
        "results": [
            {"id": "q1", "expert_id": "luc", "source_url": "https://vrt.be/x", "question": "Hoger voorschot?",
             "expert_answer": "Nee.", "karen_answer": "Dat hangt ervan af.", "processing": "hosted-public-knowledge",
             "sources": [], "seconds": 3.0,
             "judge": {"karen": 0.72, "equal": 0.02, "expert": 0.25, "verdict": "karen", "direct": 0.82, "contradicts": 0.95}},
            {"id": "q2", "question": "Waterstof?", "expert_answer": "Nee.", "error": "timeout"},
        ],
    }
    langfuse_trace.send_benchmark_run(run)
    paths = [url.removeprefix("https://cloud.langfuse.com") for url, _, _ in posted]
    assert paths == ["/api/public/datasets", "/api/public/dataset-items", "/api/public/dataset-items",
                     "/api/public/ingestion", "/api/public/dataset-run-items"]
    assert posted[1][2] == {"datasetName": "karen-expert-benchmark", "id": "q1", "input": "Hoger voorschot?",
                            "expectedOutput": "Nee.", "metadata": {"expert_id": "luc", "source_url": "https://vrt.be/x"}}
    events = posted[3][2]["batch"]
    assert events[0]["body"]["id"] == "abc123-q1" and events[0]["body"]["output"] == "Dat hangt ervan af."
    scores = {event["body"]["name"]: event["body"]["value"] for event in events[1:]}
    assert scores == {"karen_beter": 0.72, "meteen_antwoord": 0.82, "tegenspraak": 0.95, "oordeel": "karen"}
    assert posted[4][2] == {"runName": "2026-11-02T14:14 abc123", "datasetItemId": "q1", "traceId": "abc123-q1",
                            "metadata": {"run_id": "abc123", "karen_avg": 0.58}}


def test_only_public_answers_are_traced_never_pdf_ones(api, keys, monkeypatch):  # noqa: F811
    import energy_voice_app as app

    sent = []
    monkeypatch.setattr(app.langfuse_trace, "in_background", lambda send: sent.append(send))
    monkeypatch.setattr(app, "store_log_record", lambda record: None)
    monkeypatch.setattr(app, "route_question", AsyncMock(return_value=None))
    api[0].post("/api/answer", json={"question": "waarom is mijn factuur zo hoog"})
    assert len(sent) == 1

    pdf = (Path(__file__).parent / "fixtures" / "invoices" / "01-nl-afrekening-gegroepeerd.pdf").read_bytes()
    session = api[0].post("/api/upload", files={"file": ("01.pdf", pdf, "application/pdf")}).json()["session_id"]
    api[0].post("/api/answer", json={"question": "vat samen", "session_id": session})
    assert len(sent) == 1
