"""Langfuse tracing: off without keys, nested masked traces for public answers only, feedback as
scores, benchmark runs as experiments."""

import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from luminus_harness import expert_judge, gemma_answer, jev, langfuse_trace
from tests.test_karen_market_prices import api  # noqa: F401  (fixture)


@pytest.fixture
def traced(monkeypatch):
    """A real Langfuse client whose spans land in memory instead of Langfuse."""
    # The SDK shares one exporter per public key, so each test gets its own key.
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", f"pk-lf-{uuid.uuid4().hex}")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    exporter = InMemorySpanExporter()
    client = langfuse_trace.build_client(span_exporter=exporter, tracer_provider=TracerProvider())
    monkeypatch.setattr(langfuse_trace, "_client", client)

    def spans():
        client.flush()
        return {span.name: span for span in exporter.get_finished_spans()}

    spans.count = lambda: (client.flush(), len(exporter.get_finished_spans()))[1]
    yield spans
    client.shutdown()


def _attrs(span):
    return dict(span.attributes)


def test_nothing_is_traced_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(langfuse_trace, "_client", None)
    assert langfuse_trace.client() is None
    with langfuse_trace.public_turn("vraag", "0" * 32) as root:
        with langfuse_trace.step("search-web") as step:
            langfuse_trace.update(step, output="x")
        langfuse_trace.finish_turn(root, {"answer": "a"}, [])
    langfuse_trace.score_feedback("0" * 32, "up")


def test_steps_outside_a_public_turn_are_never_recorded(traced):
    with langfuse_trace.step("generate-answer", as_type="generation", input="PDF-tekst"):
        pass
    assert traced() == {}


def test_a_public_turn_is_one_trace_with_nested_typed_steps(traced, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    class Usage:
        input_tokens, output_tokens = 120, 30

    class Result:
        model, output_text, usage = "gpt-5-mini-2026", "Kies vast.", Usage()

    class Responses:
        def create(self, **_):
            return Result()

    class OpenAI:
        def __init__(self, **_):
            self.responses = Responses()

    import openai

    monkeypatch.setattr(openai, "OpenAI", OpenAI)
    trace_id = "ab" * 16
    with langfuse_trace.public_turn("vast of variabel?", trace_id, history_turns=2) as root:
        with langfuse_trace.step("search-web", as_type="retriever", input={"query": "vast", "scope": "official"}) as search:
            langfuse_trace.update(search, output=[{"title": "CREG", "url": "https://creg.be"}])
        gemma_answer._generate("VRAAG: vast of variabel?")
        langfuse_trace.finish_turn(root, {"answer": "Kies vast.", "processing": "hosted-public-knowledge",
                                          "sources": [{"title": "CREG", "url": "https://creg.be"}]},
                                   [{"kind": "route", "choice": "contract_advice", "confidence": 0.9, "used": True},
                                    {"kind": "search"}])
    spans = traced()
    assert set(spans) == {"answer-question", "search-web", "generate-answer"}
    root = spans["answer-question"]
    assert format(root.context.trace_id, "032x") == trace_id
    assert {span.context.trace_id for span in spans.values()} == {root.context.trace_id}
    assert spans["search-web"].parent.span_id == root.context.span_id
    assert spans["generate-answer"].parent.span_id == root.context.span_id
    attrs = _attrs(root)
    assert attrs["langfuse.trace.name"] == "karen-answer"
    assert attrs["langfuse.observation.type"] == "agent"
    assert attrs["langfuse.observation.input"] == "vast of variabel?"
    assert attrs["langfuse.observation.output"] == "Kies vast."
    assert json.loads(attrs["langfuse.observation.metadata.route"]) == "contract_advice"
    assert _attrs(spans["search-web"])["langfuse.observation.type"] == "retriever"
    generation = _attrs(spans["generate-answer"])
    assert generation["langfuse.observation.type"] == "generation"
    assert generation["langfuse.observation.model.name"] == "gpt-5-mini-2026"
    assert json.loads(generation["langfuse.observation.usage_details"]) == {"input": 120, "output": 30}
    assert json.loads(generation["langfuse.observation.input"])[1] == {"role": "user", "content": "VRAAG: vast of variabel?"}


def test_private_details_karen_repeats_are_masked_everywhere(traced):
    with langfuse_trace.public_turn("Mijn e-mailadres is fictief@example.com, wat kost stroom?", "cd" * 16) as root:
        with langfuse_trace.step("generate-answer", as_type="generation", input="mail fictief@example.com") as step:
            langfuse_trace.update(step, output="Je EAN is 541448800000000001.")
        langfuse_trace.finish_turn(root, {
            "answer": "Je e-mailadres is fictief@example.com.",
            "sources": [{"title": "Contact fictief@example.com", "url": "https://example.com/?m=fictief@example.com"}],
        }, [{"kind": "search_error", "message": "geen resultaat voor BE68 5390 0754 7034"}])
    spans = traced()
    root = _attrs(spans["answer-question"])
    assert root["langfuse.observation.input"] == "Mijn e-mailadres is [verborgen], wat kost stroom?"
    assert root["langfuse.observation.output"] == "Je e-mailadres is [verborgen]."
    exported = json.dumps([_attrs(span) for span in spans.values()], ensure_ascii=False)
    for private in ("fictief@example.com", "541448800000000001", "BE68 5390 0754 7034"):
        assert private not in exported


def test_jev_routing_is_a_generation_with_its_answer(traced, monkeypatch):
    monkeypatch.setattr(jev, "_post_system_one", lambda body: {
        "model": "jev-1.13.0", "usage": {"input": 40, "output": 2},
        "answers": {"answer": {"type": "choice", "choice": "price", "confidence": 0.9}},
    })
    with langfuse_trace.public_turn("wat kost stroom?", "ef" * 16):
        answers = asyncio.run(jev.ask_choices("wat kost stroom?", {"answer": ("Which kind?", {"price": "Prices"})}))
    assert answers["answer"]["choice"] == "price"
    route = _attrs(traced()["route-question"])
    assert route["langfuse.observation.type"] == "generation"
    assert route["langfuse.observation.model.name"] == "jev-1.13.0"
    assert json.loads(route["langfuse.observation.output"])["answer"]["choice"] == "price"


def test_feedback_becomes_a_score_on_the_answers_trace(monkeypatch):
    scores = []

    class Client:
        def create_score(self, **score):
            scores.append(score)

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setattr(langfuse_trace, "_client", Client())
    langfuse_trace.score_feedback("ab" * 16, "down", "fout, mail me op fictief@example.com")
    assert scores == [{"trace_id": "ab" * 16, "name": "gebruiker", "value": 0, "data_type": "BOOLEAN",
                       "comment": "fout, mail me op [verborgen]"}]


def test_only_public_answers_are_traced_never_pdf_ones(api, traced, monkeypatch):  # noqa: F811
    import energy_voice_app as app

    monkeypatch.setattr(app, "store_log_record", lambda record: None)
    monkeypatch.setattr(app, "route_question", AsyncMock(return_value=None))
    reply = api[0].post("/api/answer", json={"question": "waarom is mijn factuur zo hoog"}).json()
    spans = traced()
    assert "answer-question" in spans
    trace_id = format(spans["answer-question"].context.trace_id, "032x")
    # The 👍/👎 token points at the same id as the trace.
    assert app.build_record(reply["feedback_token"], "up", None)["log_id"] == trace_id

    pdf = (Path(__file__).parent / "fixtures" / "invoices" / "01-nl-afrekening-gegroepeerd.pdf").read_bytes()
    session = api[0].post("/api/upload", files={"file": ("01.pdf", pdf, "application/pdf")}).json()["session_id"]
    before = traced.count()
    api[0].post("/api/answer", json={"question": "vat samen", "session_id": session})
    api[0].post("/api/answer", json={"question": "oke maar real of barcelona", "session_id": session})
    assert traced.count() == before


def test_a_benchmark_run_is_an_experiment_with_jevs_scores(monkeypatch):
    calls = {"items": [], "experiments": []}

    class Client:
        def create_trace_id(self, seed):
            return "e" * 32

        def create_dataset(self, **dataset):
            calls["dataset"] = dataset

        def create_dataset_item(self, **item):
            calls["items"].append(item)
            return {"id": item["id"], **item}

        def run_experiment(self, **experiment):
            calls["experiments"].append(experiment)

            async def run():
                output = await experiment["task"](item=experiment["data"][0])
                return output, experiment["evaluators"][0](input=None, output=output)

            calls.setdefault("results", []).append(asyncio.run(run()))

        def flush(self):
            calls["flushed"] = True

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setattr(langfuse_trace, "_client", Client())
    monkeypatch.setattr(expert_judge, "judge", AsyncMock(return_value={
        "karen": 0.72, "equal": 0.02, "expert": 0.25, "verdict": "karen", "orders_agree": True, "direct": 0.82, "contradicts": 0.05,
    }))
    cases = [{"id": "q1", "expert_id": "luc", "question": "Hoger voorschot?", "expert_answer": "Nee."}]

    async def answer(question):
        return {"answer": "Nee, niet nodig.", "processing": "hosted-public-knowledge", "sources": []}

    run = asyncio.run(expert_judge.run_benchmark(answer, cases, lambda run: None, start_experiment=langfuse_trace.benchmark_experiment))
    assert run["results"][0]["karen_answer"] == "Nee, niet nodig." and run["results"][0]["judge"]["verdict"] == "karen"
    assert calls["dataset"]["name"] == "karen-expert-benchmark"
    assert calls["items"] == [{"dataset_name": "karen-expert-benchmark", "id": "q1", "input": "Hoger voorschot?",
                               "expected_output": "Nee.", "metadata": {"expert_id": "luc", "source_url": None}}]
    experiment = calls["experiments"][0]
    assert experiment["experiment_id"] == "e" * 32 and experiment["run_name"].endswith(run["id"])
    output, evaluations = calls["results"][0]
    assert output == "Nee, niet nodig."
    assert {e.name: e.value for e in evaluations} == {"karen_beter": 0.72, "meteen_antwoord": 0.82, "tegenspraak": 0.05, "oordeel": "karen"}
    assert calls["flushed"]


def test_a_benchmark_without_langfuse_runs_as_before(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.setattr(langfuse_trace, "_client", None)
    monkeypatch.setattr(expert_judge, "judge", AsyncMock(return_value={
        "karen": 0.5, "equal": 0.0, "expert": 0.5, "verdict": "karen", "orders_agree": True, "direct": None, "contradicts": None,
    }))

    async def answer(question):
        return {"answer": "Ja."}

    run = asyncio.run(expert_judge.run_benchmark(answer, [{"id": "q1", "question": "?", "expert_answer": "Ja."}], lambda run: None,
                                                 start_experiment=langfuse_trace.benchmark_experiment))
    assert run["results"][0]["karen_answer"] == "Ja." and run["summary"]["judged"] == 1


def test_benchmark_dataset_items_are_masked(monkeypatch):
    items = []

    class Client:
        def create_trace_id(self, seed):
            return "e" * 32

        def create_dataset(self, **dataset):
            pass

        def create_dataset_item(self, **item):
            items.append(item)
            return item

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setattr(langfuse_trace, "_client", Client())
    langfuse_trace.benchmark_experiment({"id": "r1", "started_at": "2026-11-02T14:14"}, [{
        "id": "q1", "question": "Mail fictief@example.com?", "expert_answer": "Stort op BE68 5390 0754 7034.",
        "expert_id": "fictief@example.com", "source_url": "https://example.com/?m=fictief@example.com",
    }])
    sent = json.dumps(items, ensure_ascii=False)
    for private in ("fictief@example.com", "BE68 5390 0754 7034"):
        assert private not in sent
    assert items[0]["input"] == "Mail [verborgen]?" and items[0]["expected_output"] == "Stort op [verborgen]."


class _QueueApi:
    def __init__(self, existing=()):
        self.queues = list(existing)
        self.items = []
        self.configs = []

    @property
    def annotation_queues(self):
        api = self

        class Queues:
            def list_queues(self, **_):
                return type("Page", (), {"data": api.queues})()

            def create_queue(self, *, name, score_config_ids, description=None):
                queue = type("Queue", (), {"id": "queue-1", "name": name, "score_config_ids": score_config_ids})()
                api.queues.append(queue)
                return queue

            def create_queue_item(self, queue_id, *, object_id, object_type):
                api.items.append((queue_id, object_id, str(getattr(object_type, "value", object_type))))

        return Queues()

    @property
    def score_configs(self):
        api = self

        class Configs:
            def get(self, **_):
                return type("Page", (), {"data": api.configs})()

            def create(self, *, name, data_type, categories, description=None):
                config = type("Config", (), {"id": "config-1", "name": name, "categories": categories})()
                api.configs.append(config)
                return config

        return Configs()


def _queue_client(monkeypatch, api):
    class Client:
        def __init__(self):
            self.api = api

        def create_score(self, **_):
            pass

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setattr(langfuse_trace, "_client", Client())
    monkeypatch.setattr(langfuse_trace, "_review_queue_id", None)


def test_a_thumbs_down_puts_the_answer_in_the_review_queue(monkeypatch):
    api = _QueueApi()
    _queue_client(monkeypatch, api)
    langfuse_trace.review_thumbs_down("ab" * 16)
    langfuse_trace.review_thumbs_down("cd" * 16)
    assert [queue.name for queue in api.queues] == ["Karen 👎 nakijken"]
    assert api.queues[0].score_config_ids == ["config-1"]
    assert [config.name for config in api.configs] == ["beoordeling"]
    assert api.items == [("queue-1", "ab" * 16, "TRACE"), ("queue-1", "cd" * 16, "TRACE")]


def test_an_existing_review_queue_is_reused(monkeypatch):
    queue = type("Queue", (), {"id": "queue-9", "name": "Karen 👎 nakijken"})()
    api = _QueueApi([queue])
    _queue_client(monkeypatch, api)
    langfuse_trace.review_thumbs_down("ab" * 16)
    assert api.items == [("queue-9", "ab" * 16, "TRACE")] and api.configs == []


def test_feedback_reaches_langfuse_as_score_and_review_item(api, monkeypatch):  # noqa: F811
    import energy_voice_app as app

    calls = []
    monkeypatch.setattr(app, "store_log_record", lambda record: None)
    monkeypatch.setattr(app, "store_feedback", lambda record: None)
    monkeypatch.setattr(app, "route_question", AsyncMock(return_value=None))
    monkeypatch.setattr(app.langfuse_trace, "score_feedback", lambda *args: calls.append(("score", args[0], args[1])))
    monkeypatch.setattr(app.langfuse_trace, "review_thumbs_down", lambda trace_id: calls.append(("review", trace_id)))
    token = api[0].post("/api/answer", json={"question": "waarom is mijn factuur zo hoog"}).json()["feedback_token"]
    from luminus_harness.feedback import read_feedback_token

    log_id = read_feedback_token(token)["l"]
    api[0].post("/api/feedback", json={"feedback_token": token, "rating": "up"})
    api[0].post("/api/feedback", json={"feedback_token": token, "rating": "down"})
    assert calls == [("score", log_id, "up"), ("score", log_id, "down"), ("review", log_id)]


def test_stored_feedback_masks_the_question_too():
    from luminus_harness.feedback import build_record, sign_feedback

    token = sign_feedback("mail fictief@example.com, klopt mijn voorschot?", "Ja.", "hosted-public-knowledge", "ab" * 16)
    record = build_record(token, "down", None)
    assert record["question"] == "mail [verborgen], klopt mijn voorschot?"


def test_simultaneous_first_thumbs_down_create_one_queue(monkeypatch):
    import threading
    import time as clock

    api = _QueueApi()
    _queue_client(monkeypatch, api)
    slow_list = type(api).annotation_queues.fget

    class SlowApi(_QueueApi):
        @property
        def annotation_queues(self):
            queues = slow_list(self)
            original = queues.list_queues

            def list_queues(**kwargs):
                page = original(**kwargs)
                clock.sleep(0.05)  # both threads see an empty list before either creates the queue
                return page

            queues.list_queues = list_queues
            return queues

    api.__class__ = SlowApi
    threads = [threading.Thread(target=langfuse_trace.review_thumbs_down, args=(trace_id,)) for trace_id in ("ab" * 16, "cd" * 16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(api.queues) == 1 and len(api.configs) == 1
    assert sorted(item[1] for item in api.items) == ["ab" * 16, "cd" * 16]
    assert {item[0] for item in api.items} == {"queue-1"}
