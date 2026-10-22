import base64
import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from luminus_harness import conversation_log
from luminus_harness.rate_limit import SlidingWindowRateLimiter


@pytest.fixture
def api(monkeypatch, tmp_path):
    import energy_voice_app as app

    monkeypatch.setenv("KAREN_FEEDBACK_DIR", str(tmp_path))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("KAREN_ADMIN_KEY", "geheim")
    monkeypatch.setattr(conversation_log, "_pending", [])
    monkeypatch.setattr(app, "_api_rate_limiter", SlidingWindowRateLimiter(requests=100, window_seconds=60, max_clients=100))
    monkeypatch.setattr(app, "answer_energy_question", AsyncMock(return_value={
        "answer": "Publiek antwoord", "sources": [{"title": "CREG", "url": "https://www.creg.be/x"}],
    }))
    with TestClient(app.api_app) as client:
        yield client, tmp_path


def logged(tmp_path):
    return [json.loads(line) for line in (tmp_path / "conversations.jsonl").read_text().splitlines()]


def auth(password):
    return {"Authorization": "Basic " + base64.b64encode(f"pascal:{password}".encode()).decode()}


def test_every_public_answer_is_logged_and_linked_to_its_rating(api):
    client, tmp_path = api
    reply = client.post("/api/answer", json={"question": "Hoe werkt het capaciteitstarief?"}).json()
    [record] = logged(tmp_path)
    assert record["question"] == "Hoe werkt het capaciteitstarief?"
    assert record["answer"] == "Publiek antwoord"
    assert record["sources"] == [{"title": "CREG", "url": "https://www.creg.be/x"}]
    client.post("/api/feedback", json={"feedback_token": reply["feedback_token"], "rating": "down"})
    feedback = json.loads((tmp_path / "feedback.jsonl").read_text())
    assert feedback["log_id"] == record["id"]


def test_identifiers_in_a_question_are_masked():
    record = conversation_log.build_log_record(
        "Mijn EAN is 541448820012345678 en mail me op jan@example.com", {"answer": "x"}, [], 1.0
    )
    assert "541448820012345678" not in record["question"] and "jan@example.com" not in record["question"]
    assert record["question"].count("[verborgen]") == 2


def test_route_and_search_steps_are_kept():
    steps = [
        {"kind": "route", "choice": "energy_other", "confidence": 0.84, "probabilities": {"energy_other": 0.87}},
        {"kind": "search", "sites": ["creg.be"]},
        {"kind": "search_error", "message": "Browserbase Search gaf HTTP 400."},
    ]
    record = conversation_log.build_log_record("Wat is energie?", {"answer": "x", "processing": "general-knowledge"}, steps, 2.04)
    assert record["route"] == "energy_other" and record["route_confidence"] == 0.84
    assert record["searches"] == 1 and record["search_errors"] == ["Browserbase Search gaf HTTP 400."]
    assert record["seconds"] == 2.0


def test_admin_page_needs_the_password(api):
    client, _ = api
    assert client.get("/admin").status_code == 401
    assert client.get("/admin", headers=auth("fout")).status_code == 401
    client.post("/api/answer", json={"question": "Hoe werkt het capaciteitstarief?"})
    page = client.get("/admin", headers=auth("geheim"))
    assert page.status_code == 200
    assert "Hoe werkt het capaciteitstarief?" in page.text
    csv = client.get("/admin/conversations.csv", headers=auth("geheim"))
    assert "Hoe werkt het capaciteitstarief?" in csv.text


def test_admin_page_is_hidden_without_a_key(api, monkeypatch):
    client, _ = api
    monkeypatch.delenv("KAREN_ADMIN_KEY")
    assert client.get("/admin", headers=auth("geheim")).status_code == 404


def test_failed_upload_keeps_the_batch(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_TOKEN", "t")
    monkeypatch.setenv("KAREN_FEEDBACK_DATASET", "me/karen")
    monkeypatch.setattr(conversation_log, "_pending", [{"id": "a", "created_at": "2026-10-22T19:00:00+00:00"}])

    class Broken:
        def __init__(self, **_):
            pass

        def create_repo(self, *_, **__):
            raise RuntimeError("offline")

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "HfApi", Broken)
    assert conversation_log.flush() is False
    assert [record["id"] for record in conversation_log.pending_records()] == ["a"]


def test_queued_records_are_flushed_when_the_server_stops(monkeypatch, tmp_path):
    registered = []
    monkeypatch.setenv("KAREN_FEEDBACK_DIR", str(tmp_path))
    monkeypatch.setattr(conversation_log, "_pending", [])
    monkeypatch.setattr(conversation_log, "_flusher", None)
    monkeypatch.setattr(conversation_log.threading, "Thread", lambda **_: type("T", (), {"start": lambda self: None})())
    monkeypatch.setattr(conversation_log.atexit, "register", registered.append)
    conversation_log.store_log_record({"id": "a", "created_at": "2026-10-22T19:00:00+00:00"})
    assert registered == [conversation_log.flush]
