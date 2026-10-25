import asyncio
import base64
import time

import pytest
from fastapi.testclient import TestClient

from luminus_harness import expert_judge
from luminus_harness.rate_limit import SlidingWindowRateLimiter


def fake_jev(compare_karen_first, compare_expert_first, direct=0.8, contradicts=0.1):
    """Answers Jev's questions by looking at which answer the state shows first."""
    def post(body):
        state, questions = body["state"], body["questions"]
        if "compare" in questions:
            p = compare_karen_first if state.index("KAREN") < state.index("EXPERT") else compare_expert_first
            return {"answers": {"compare": {"type": "choice", "choice": max(p, key=p.get), "confidence": max(p.values()), "probabilities": p}}}
        return {"answers": {
            "direct": {"type": "choice", "choice": "yes", "confidence": direct, "probabilities": {"yes": direct, "no": 1 - direct}},
            "contradicts": {"type": "choice", "choice": "no", "confidence": 1 - contradicts,
                            "probabilities": [{"option": "yes", "probability": contradicts}, {"option": "no", "probability": 1 - contradicts}]},
        }}
    return post


def test_judge_averages_both_orders_so_position_does_not_decide(monkeypatch):
    # Shown first, Karen wins; shown second, the expert wins: Jev just prefers answer A.
    monkeypatch.setattr(expert_judge, "_post", fake_jev(
        {"a_better": 0.7, "equal": 0.2, "b_better": 0.1}, {"a_better": 0.7, "equal": 0.2, "b_better": 0.1}))
    result = asyncio.run(expert_judge.judge("Vraag?", "EXPERT antwoord", "KAREN antwoord"))
    assert result["karen"] == result["expert"] == 0.4
    assert result["orders_agree"] is False
    assert result["direct"] == 0.8 and result["contradicts"] == 0.1


def test_judge_names_karen_when_both_orders_prefer_her(monkeypatch):
    monkeypatch.setattr(expert_judge, "_post", fake_jev(
        {"a_better": 0.8, "equal": 0.15, "b_better": 0.05}, {"a_better": 0.1, "equal": 0.2, "b_better": 0.7}))
    result = asyncio.run(expert_judge.judge("Vraag?", "EXPERT", "KAREN"))
    assert result["verdict"] == "karen" and result["orders_agree"] is True
    assert result["karen"] == 0.75


def test_a_failing_case_does_not_stop_the_run(monkeypatch):
    monkeypatch.setattr(expert_judge, "_post", fake_jev(
        {"a_better": 0.1, "equal": 0.2, "b_better": 0.7}, {"a_better": 0.7, "equal": 0.2, "b_better": 0.1}))

    async def answer(question):
        if question == "kapot":
            raise TimeoutError("te traag")
        return {"answer": "KAREN zegt nee", "processing": "general-knowledge", "sources": [{"url": "https://creg.be"}]}

    cases = [{"id": "1", "question": "Mag dat?", "expert_answer": "EXPERT: nee"}, {"id": "2", "question": "kapot", "expert_answer": "EXPERT"}]
    seen = []
    run = asyncio.run(expert_judge.run_benchmark(answer, cases, lambda r: seen.append(len(r["results"]))))
    assert run["status"] == "done" and seen[-1] == 2
    assert run["results"][0]["judge"]["verdict"] == "expert"
    assert run["results"][1]["error"] == "te traag"
    assert run["summary"] == {"judged": 1, "errors": 1, "karen": 0, "equal": 0, "expert": 1,
                              "karen_avg": 0.1, "direct": 1, "contradicts": 0}
    page = expert_judge.render_benchmark_page(run, True, 2)
    assert "Expert beter" in page and "te traag" in page and "Start benchmark (2 vragen)" in page


def test_benchmark_set_holds_the_vrt_questions_with_expert_answers():
    cases = expert_judge.load_cases()
    assert len(cases) >= 13
    assert all(case["question"].endswith("?") and len(case["expert_answer"]) > 40 and case["source_url"].startswith("https://") for case in cases)
    assert len({case["id"] for case in cases}) == len(cases)


@pytest.fixture
def client(monkeypatch):
    import energy_voice_app as app

    monkeypatch.setattr(app, "_api_rate_limiter", SlidingWindowRateLimiter(requests=100, window_seconds=60, max_clients=100))
    monkeypatch.setattr(app, "_benchmark", {"run": None, "task": None})
    with TestClient(app.api_app) as test_client:
        yield test_client, app


def auth(password):
    return {"Authorization": "Basic " + base64.b64encode(f"x:{password}".encode()).decode()}


def test_benchmark_page_is_for_the_owner_only(client, monkeypatch):
    test_client, _ = client
    monkeypatch.delenv("KAREN_ADMIN_KEY", raising=False)
    assert test_client.get("/admin/benchmark").status_code == 404
    monkeypatch.setenv("KAREN_ADMIN_KEY", "geheim")
    assert test_client.get("/admin/benchmark").status_code == 401
    assert test_client.post("/admin/benchmark", follow_redirects=False).status_code == 401
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    page = test_client.get("/admin/benchmark", headers=auth("geheim")).text
    assert "TYPESAFE_API_KEY" in page and "<form" not in page


def test_owner_starts_a_run_and_sees_the_result(client, monkeypatch):
    test_client, app = client
    monkeypatch.setenv("KAREN_ADMIN_KEY", "geheim")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(expert_judge, "load_cases", lambda: [{"id": "1", "expert_id": "x", "question": "Mag dat?", "expert_answer": "EXPERT nee", "source_url": "https://vrt.be/a"}])
    monkeypatch.setattr(expert_judge, "_post", fake_jev(
        {"a_better": 0.6, "equal": 0.3, "b_better": 0.1}, {"a_better": 0.1, "equal": 0.3, "b_better": 0.6}))
    asked = []

    async def answer(body):
        asked.append(body.question)
        return {"answer": "KAREN: nee, je mag weigeren.", "processing": "hosted-public-knowledge", "sources": []}

    monkeypatch.setattr(app, "_answer", answer)
    monkeypatch.setattr(expert_judge, "save_run", lambda run: None)
    started = test_client.post("/admin/benchmark", headers=auth("geheim"), follow_redirects=False)
    assert started.status_code == 303 and started.headers["location"] == "/admin/benchmark"
    for _ in range(50):
        run = app._benchmark["run"]
        if run and run["status"] == "done":
            break
        time.sleep(0.02)  # the run goes on in the app's own event loop
    page = test_client.get("/admin/benchmark", headers=auth("geheim")).text
    assert asked == ["Mag dat?"]
    assert "Karen beter" in page and "KAREN: nee, je mag weigeren." in page


def test_cases_need_their_fields_and_each_carry_their_own_source(tmp_path, monkeypatch):
    path = tmp_path / "cases.json"
    path.write_text('{"cases": [{"id": "a", "expert_id": "x", "question": "Mag dat?", "expert_answer": "Nee."}]}', encoding="utf-8")
    monkeypatch.setattr(expert_judge, "CASES_PATH", path)
    with pytest.raises(ValueError, match="a mist source_url"):
        expert_judge.load_cases()


def test_benchmark_blocks_each_cases_source_and_the_registry_holdout(tmp_path, monkeypatch):
    holdout = tmp_path / "holdout.json"
    holdout.write_text('{"benchmark_only_urls": ["https://www.uhasselt.be/paper.pdf"]}', encoding="utf-8")
    monkeypatch.setattr(expert_judge, "HOLDOUT_PATH", holdout)
    cases = [{"source_url": "https://www.vrt.be/vrtnws/nl/2026/10/23/vragen/"}]
    assert expert_judge.blocked_sources(cases) == ("https://www.uhasselt.be/paper.pdf", "https://www.vrt.be/vrtnws/nl/2026/10/23/vragen")
    monkeypatch.setattr(expert_judge, "HOLDOUT_PATH", tmp_path / "missing.json")
    assert len(expert_judge.blocked_sources(cases)) == 1


def test_judge_tells_jev_how_old_the_expert_answer_is(monkeypatch):
    states = []
    fake = fake_jev({"a_better": 0.5, "equal": 0.3, "b_better": 0.2}, {"a_better": 0.2, "equal": 0.3, "b_better": 0.5})
    monkeypatch.setattr(expert_judge, "_post", lambda body: states.append(body["state"]) or fake(body))
    asyncio.run(expert_judge.judge("Vraag?", "EXPERT", "KAREN", "2020-11-06"))
    assert len(states) == 3 and all("2020-11-06" in state for state in states)
