import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from luminus_harness import feedback
from luminus_harness.rate_limit import SlidingWindowRateLimiter


@pytest.fixture
def api(monkeypatch, tmp_path):
    import energy_voice_app as app

    monkeypatch.setenv("KAREN_FEEDBACK_DIR", str(tmp_path))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(app, "_api_rate_limiter", SlidingWindowRateLimiter(requests=100, window_seconds=60, max_clients=100))
    monkeypatch.setattr(app, "answer_energy_question", AsyncMock(return_value={"answer": "Publiek antwoord", "sources": []}))
    with TestClient(app.api_app) as client:
        yield client, tmp_path / "feedback.jsonl"


def stored(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_public_answer_can_be_rated_with_its_token(api):
    client, path = api
    reply = client.post("/api/answer", json={"question": "Hoe werkt het capaciteitstarief?"}).json()
    response = client.post("/api/feedback", json={"feedback_token": reply["feedback_token"], "rating": "down", "comment": "Te vaag"})
    assert response.json() == {"stored": True}
    [record] = stored(path)
    assert record["rating"] == "down" and record["comment"] == "Te vaag"
    assert record["question"] == "Hoe werkt het capaciteitstarief?"
    assert record["answer"] == "Publiek antwoord"
    assert record["processing"] == "hosted-public-knowledge"


@pytest.mark.parametrize("token", ["x" * 60, feedback.sign_feedback("q", "a", "p")[:-8] + "AAAAAAAA"])
def test_forged_tokens_are_rejected(api, token):
    client, path = api
    response = client.post("/api/feedback", json={"feedback_token": token, "rating": "up"})
    assert response.status_code == 422
    assert not path.exists()


def test_expired_tokens_are_rejected(monkeypatch):
    token = feedback.sign_feedback("q", "a", "p")
    monkeypatch.setattr(feedback.time, "time", lambda: 10**12)
    with pytest.raises(feedback.FeedbackError):
        feedback.read_feedback_token(token)


def test_private_details_in_comments_are_dropped():
    record = feedback.build_record(feedback.sign_feedback("q", "a", "p"), "down", "mijn klantnummer is 1234")
    assert record["comment"] == ""
    assert record["rating"] == "down"


def test_upload_goes_to_the_private_dataset_when_configured(monkeypatch, tmp_path):
    calls = []

    class FakeApi:
        def __init__(self, token):
            calls.append(("token", token))

        def create_repo(self, repo_id, **kwargs):
            calls.append(("create", repo_id, kwargs["private"]))

        def upload_file(self, **kwargs):
            calls.append(("upload", kwargs["repo_id"], kwargs["path_in_repo"], json.loads(kwargs["path_or_fileobj"])["rating"]))

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "HfApi", FakeApi)
    monkeypatch.setenv("KAREN_FEEDBACK_DIR", str(tmp_path))
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.setenv("KAREN_FEEDBACK_DATASET", "someone/karen-feedback")
    record = feedback.build_record(feedback.sign_feedback("q", "a", "p"), "up", None)
    assert feedback.store_feedback(record) is True
    assert calls[1] == ("create", "someone/karen-feedback", True)
    assert calls[2][:2] == ("upload", "someone/karen-feedback")
    assert calls[2][2] == f"feedback/{record['created_at'][:10]}/{record['id']}.json"
    assert (tmp_path / "feedback.jsonl").exists()


def test_upload_failure_keeps_the_local_record(monkeypatch, tmp_path):
    monkeypatch.setenv("KAREN_FEEDBACK_DIR", str(tmp_path))
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.setenv("KAREN_FEEDBACK_DATASET", "someone/karen-feedback")
    monkeypatch.setattr(feedback, "_upload", lambda _record: (_ for _ in ()).throw(OSError("offline")))
    record = feedback.build_record(feedback.sign_feedback("q", "a", "p"), "up", None)
    assert feedback.store_feedback(record) is False
    assert stored(tmp_path / "feedback.jsonl")[0]["id"] == record["id"]
