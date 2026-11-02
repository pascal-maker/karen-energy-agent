"""Trace Karen's public answers and expert-benchmark runs in Langfuse, when the owner sets its keys.

Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (LANGFUSE_BASE_URL, or the older
LANGFUSE_HOST, defaults to the EU cloud). Uses the Langfuse Python SDK (OpenTelemetry), following
https://langfuse.com/docs/observability/best-practices:

- One trace per public chat turn ("karen-answer"), with a root `answer-question` agent observation
  (input: the visitor's message, output: Karen's answer) and nested steps: Jev's routing
  (`route-question`), each site search (`search-web`, a retriever) and each model call
  (`rewrite-follow-up`, `translate-search-question`, `generate-answer`, ...), as generations with
  model name and token usage.
- A 👍/👎 on an answer becomes a `gebruiker` score on that answer's trace.
- Benchmark runs go through the SDK's experiment runner on the `karen-expert-benchmark` dataset,
  with Jev's verdict per question as scores.

Privacy: steps are only recorded inside a public turn or a benchmark question, so PDF and photo
answers are never traced. E-mail addresses, account and EAN numbers are masked in every input,
output and metadata field at export time, also where Karen repeats them in her answer.
Tracing never breaks or slows down an answer: the SDK exports in the background and drops errors.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from luminus_harness.conversation_log import mask_private

DEFAULT_HOST = "https://cloud.langfuse.com"
DATASET = "karen-expert-benchmark"
EXPERIMENT = "Karen tegen de experts"
TRACE_NAME = "karen-answer"
# Span attributes that carry text written by the visitor, Karen, a web page or an expert.
_TEXT_ATTRIBUTES = ("input", "output", "metadata", "status_message", "expected_output")

# True while a public answer or a benchmark question is being worked on: only then are steps recorded.
_recording: ContextVar[bool] = ContextVar("karen_langfuse_recording", default=False)
_client: Any = None
_client_lock = threading.Lock()


def enabled() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY", "").strip() and os.getenv("LANGFUSE_SECRET_KEY", "").strip())


def _mask_spans(*, params: Any) -> Any:
    """Mask private identifiers in every text attribute of the spans about to be exported."""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    for identifier, span in params.spans.items():
        replacements = {}
        for key, value in span.attributes.items():
            if not any(part in key for part in _TEXT_ATTRIBUTES):
                continue
            if isinstance(value, str):
                masked = mask_private(value)
                if masked != value:
                    replacements[key] = masked
            elif isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
                masked_items = [mask_private(item) for item in value]
                if masked_items != list(value):
                    replacements[key] = masked_items
        if replacements:
            patches[identifier] = OtelSpanPatch(set_attributes=replacements)
    return MaskOtelSpansResult(span_patches=patches)


def build_client(**options: Any) -> Any:
    from langfuse import Langfuse
    from langfuse.span_filter import is_langfuse_span

    return Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
        base_url=(os.getenv("LANGFUSE_BASE_URL", "").strip() or os.getenv("LANGFUSE_HOST", "").strip() or DEFAULT_HOST).rstrip("/"),
        environment=os.getenv("LANGFUSE_TRACING_ENVIRONMENT", "").strip() or "production",
        mask_otel_spans=_mask_spans,
        # Only Karen's own observations: no HTTP or framework spans from other libraries.
        should_export_span=is_langfuse_span,
        **options,
    )


def client() -> Any:
    """The Langfuse client, or None when the keys are not set or the SDK cannot start."""
    global _client
    if not enabled():
        return None
    with _client_lock:
        if _client is None:
            try:
                _client = build_client()
            except Exception:
                return None
        return _client


class _Nothing:
    """Stands in for an observation when nothing is recorded."""

    id = None
    trace_id = None

    def update(self, **_: Any) -> "_Nothing":
        return self


_NOTHING = _Nothing()


@contextmanager
def public_turn(question: str, trace_id: str, history_turns: int = 0) -> Iterator[Any]:
    """The root of one public chat turn; steps inside it are recorded. Yields the root observation."""
    langfuse = client()
    if langfuse is None:
        yield _NOTHING
        return
    from langfuse import propagate_attributes

    try:
        root_context = langfuse.start_as_current_observation(
            trace_context={"trace_id": trace_id}, name="answer-question", as_type="agent",
            input=question, metadata={"history_turns": history_turns},
        )
        root = root_context.__enter__()
    except Exception:
        yield _NOTHING
        return
    token = _recording.set(True)
    try:
        with propagate_attributes(trace_name=TRACE_NAME, tags=["chat"]):
            yield root
    except BaseException as error:
        _quietly(lambda: root.update(level="ERROR", status_message=str(error)[:300] or type(error).__name__))
        raise
    finally:
        _recording.reset(token)
        _quietly(lambda: root_context.__exit__(None, None, None))


def finish_turn(root: Any, reply: dict[str, Any], steps: list[dict[str, Any]]) -> None:
    """Karen's answer as the trace output, with the route, sources and searches as metadata."""
    route = next((step for step in steps if step.get("kind") == "route"), None)
    _quietly(lambda: root.update(
        output=str(reply.get("answer", "")),
        metadata={
            "processing": str(reply.get("processing", "")),
            "route": route.get("choice") if route else None,
            "route_confidence": route.get("confidence") if route else None,
            "route_used": route.get("used") if route else None,
            "sources": [
                {"title": str(source.get("title", ""))[:200], "url": str(source.get("url", ""))[:500]}
                for source in reply.get("sources") or [] if isinstance(source, dict)
            ],
            "searches": sum(1 for step in steps if step.get("kind") == "search"),
            "search_errors": [str(step.get("message", ""))[:300] for step in steps if step.get("kind") == "search_error"],
        },
    ))


@contextmanager
def step(name: str, as_type: str = "span", **fields: Any) -> Iterator[Any]:
    """A nested observation inside a public turn or a benchmark question; a no-op anywhere else."""
    langfuse = client() if _recording.get() else None
    if langfuse is None:
        yield _NOTHING
        return
    try:
        context = langfuse.start_as_current_observation(name=name, as_type=as_type, **fields)
        observation = context.__enter__()
    except Exception:
        yield _NOTHING
        return
    try:
        yield observation
    except BaseException as error:
        _quietly(lambda: observation.update(level="ERROR", status_message=str(error)[:300] or type(error).__name__))
        raise
    finally:
        _quietly(lambda: context.__exit__(None, None, None))


def update(observation: Any, **fields: Any) -> None:
    _quietly(lambda: observation.update(**fields))


def note_generation(model: str | None = None, usage_details: dict[str, Any] | None = None, **fields: Any) -> None:
    """Model name and token usage on the generation being recorded, if any (Langfuse computes the cost)."""
    langfuse = client() if _recording.get() else None
    if langfuse is None:
        return
    usage = {key: int(value) for key, value in (usage_details or {}).items() if isinstance(value, (int, float))}
    _quietly(lambda: langfuse.update_current_generation(
        model=model or None, usage_details=usage or None, **{key: value for key, value in fields.items() if value is not None},
    ))


def score_feedback(trace_id: str, rating: str, comment: str = "") -> None:
    """A visitor's 👍 (1) or 👎 (0) on an answer, as a score on that answer's trace."""
    langfuse = client()
    if langfuse is None or not trace_id:
        return
    _quietly(lambda: langfuse.create_score(
        trace_id=trace_id, name="gebruiker", value=1 if rating == "up" else 0, data_type="BOOLEAN",
        comment=mask_private(comment) or None,
    ))


def masked(value: Any) -> Any:
    """`value` with private identifiers masked in every text, also inside lists and mappings."""
    if isinstance(value, str):
        return mask_private(value)
    if isinstance(value, dict):
        return {key: masked(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [masked(item) for item in value]
    return value


REVIEW_QUEUE = "Karen 👎 nakijken"
REVIEW_SCORE = "beoordeling"
_REVIEW_CATEGORIES = (("goed", 1), ("onvolledig", 0.5), ("ontwijkend", 0.25), ("fout", 0))
_review_queue_id: str | None = None


def _review_queue(langfuse: Any) -> str:
    """The id of the review queue for 👎 answers, created with its `beoordeling` score on first use."""
    global _review_queue_id
    if _review_queue_id:
        return _review_queue_id
    queues = langfuse.api.annotation_queues
    existing = next((queue for queue in queues.list_queues(limit=100).data if queue.name == REVIEW_QUEUE), None)
    if existing is None:
        from langfuse.api import ConfigCategory

        configs = langfuse.api.score_configs
        config = next((item for item in configs.get(limit=100).data if item.name == REVIEW_SCORE), None) or configs.create(
            name=REVIEW_SCORE, data_type="CATEGORICAL",
            categories=[ConfigCategory(label=label, value=value) for label, value in _REVIEW_CATEGORIES],
            description="Jouw oordeel over een antwoord dat een bezoeker 👎 gaf. 'Ontwijkend': geen duidelijk antwoord.",
        )
        existing = queues.create_queue(
            name=REVIEW_QUEUE, score_config_ids=[config.id],
            description="Antwoorden van Karen met een 👎 van een bezoeker. Goede voorbeelden gaan naar de testset.",
        )
    _review_queue_id = existing.id
    return _review_queue_id


def review_thumbs_down(trace_id: str) -> None:
    """Put an answer a visitor gave 👎 in the Langfuse review queue (blocking; run it off the event loop)."""
    langfuse = client()
    if langfuse is None or not trace_id:
        return

    def add() -> None:
        from langfuse.api import AnnotationQueueObjectType

        langfuse.api.annotation_queues.create_queue_item(
            _review_queue(langfuse), object_id=trace_id, object_type=AnnotationQueueObjectType.TRACE,
        )

    _quietly(add)


def _quietly(send: Callable[[], Any]) -> None:
    try:
        send()
    except Exception:
        pass


# Benchmark ----------------------------------------------------------------------------------

_JUDGE_SCORES = (("karen_beter", "karen"), ("meteen_antwoord", "direct"), ("tegenspraak", "contradicts"))


def _evaluations(row: dict[str, Any]) -> list[Any]:
    from langfuse import Evaluation

    judge = row.get("judge") or {}
    found = [
        Evaluation(name=name, value=float(judge[key]))
        for name, key in _JUDGE_SCORES if isinstance(judge.get(key), (int, float))
    ]
    if judge.get("verdict"):
        found.append(Evaluation(name="oordeel", value=str(judge["verdict"]), data_type="CATEGORICAL"))
    return found


class BenchmarkExperiment:
    """Runs each benchmark question as one item of a Langfuse experiment on the shared dataset."""

    def __init__(self, langfuse: Any, run: dict[str, Any], cases: list[dict[str, Any]]):
        self.langfuse = langfuse
        self.run_name = f"{str(run.get('started_at', ''))[:16]} {run.get('id', '')}".strip()
        self.experiment_id = langfuse.create_trace_id(seed=f"karen-benchmark-{run.get('id', '')}")
        _quietly(lambda: langfuse.create_dataset(name=DATASET, description="Karen tegen de energie-experts"))
        self.items: dict[str, Any] = {}
        for case in cases:
            case_id = str(case.get("id") or "")
            if not case_id:
                continue
            try:
                # A direct API call, not a span: mask_otel_spans does not see it, so mask here.
                self.items[case_id] = langfuse.create_dataset_item(
                    dataset_name=DATASET, id=case_id, input=masked(case.get("question", "")),
                    expected_output=masked(case.get("expert_answer", "")),
                    metadata=masked({"expert_id": case.get("expert_id"), "source_url": case.get("source_url")}),
                )
            except Exception:
                pass

    def run_case(self, case: dict[str, Any], work: Callable[[], Any]) -> dict[str, Any]:
        """Run `work()` (Karen answers, Jev judges; returns the result row) as one experiment item.

        Called from a worker thread; the SDK runs the coroutine on its own event loop there."""
        item = self.items.get(str(case.get("id") or ""))
        if item is None:
            return _run_coroutine(work())
        rows: list[dict[str, Any]] = []

        async def task(**_: Any) -> str:
            token = _recording.set(True)
            try:
                row = await work()
            finally:
                _recording.reset(token)
            rows.append(row)
            if row.get("error"):
                raise RuntimeError(row["error"])
            return row.get("karen_answer", "")

        def evaluator(**_: Any) -> list[Any]:
            return _evaluations(rows[0]) if rows else []

        try:
            self.langfuse.run_experiment(
                name=EXPERIMENT, run_name=self.run_name, experiment_id=self.experiment_id,
                description="Karen beantwoordt de expertvragen; Jev vergelijkt met het expertantwoord.",
                data=[item], task=task, evaluators=[evaluator], max_concurrency=1,
            )
        except Exception:
            pass
        if rows:
            return rows[0]
        return _run_coroutine(work())

    def finish(self) -> None:
        _quietly(self.langfuse.flush)


def _run_coroutine(coroutine: Any) -> Any:
    import asyncio

    return asyncio.run(coroutine)


def benchmark_experiment(run: dict[str, Any], cases: list[dict[str, Any]]) -> BenchmarkExperiment | None:
    langfuse = client()
    if langfuse is None:
        return None
    try:
        return BenchmarkExperiment(langfuse, run, cases)
    except Exception:
        return None
