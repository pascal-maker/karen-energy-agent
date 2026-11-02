"""Send Karen's public answers and expert-benchmark runs to Langfuse, when the owner sets its keys.

Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (LANGFUSE_HOST defaults to the EU
cloud). Only what the private conversation log already keeps is sent: the masked public question,
Karen's answer, the route, sources and timing. PDF and photo answers never reach this module.
Everything goes over Langfuse's public HTTP API from a background thread, and a failure is
dropped: tracing never slows down or breaks an answer.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.request import Request, urlopen

DEFAULT_HOST = "https://cloud.langfuse.com"
DATASET = "karen-expert-benchmark"
TIMEOUT_SECONDS = 10


def _config() -> tuple[str, str] | None:
    public, secret = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(), os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    if not public or not secret:
        return None
    host = (os.getenv("LANGFUSE_HOST", "").strip() or DEFAULT_HOST).rstrip("/")
    return host, base64.b64encode(f"{public}:{secret}".encode()).decode()


def enabled() -> bool:
    return _config() is not None


def _post(path: str, body: dict[str, Any]) -> None:
    config = _config()
    if config is None:
        return
    host, auth = config
    request = Request(
        f"{host}{path}", data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        response.read()


def _quietly(send: Callable[[], None]) -> None:
    try:
        send()
    except Exception:
        pass


def in_background(send: Callable[[], None]) -> None:
    """Run `send` on a daemon thread when Langfuse is configured; never raises."""
    if enabled():
        threading.Thread(target=_quietly, args=(send,), name="karen-langfuse", daemon=True).start()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _event(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    return {"id": uuid.uuid4().hex, "type": kind, "timestamp": _now(), "body": body}


def answer_trace(record: dict[str, Any]) -> dict[str, Any]:
    """One trace for one logged public answer (the conversation-log record, already masked)."""
    metadata = {key: record.get(key) for key in (
        "processing", "route", "route_confidence", "route_probabilities", "searches", "search_errors", "sources", "seconds",
    )}
    tags = [str(value) for value in (record.get("processing"), record.get("route")) if value]
    return _event("trace-create", {
        "id": record["id"], "timestamp": record.get("created_at") or _now(), "name": "karen-answer",
        "input": record.get("question", ""), "output": record.get("answer", ""), "metadata": metadata, "tags": tags,
    })


def send_answer(record: dict[str, Any]) -> None:
    _post("/api/public/ingestion", {"batch": [answer_trace(record)]})


def benchmark_events(run: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(dataset items, ingestion events, dataset-run links) for a finished benchmark run."""
    items, events, links = [], [], []
    run_name = f"{str(run.get('started_at', ''))[:16]} {run.get('id', '')}".strip()
    for row in run.get("results") or []:
        case_id = str(row.get("id") or "")
        if not case_id:
            continue
        items.append({
            "datasetName": DATASET, "id": case_id, "input": row.get("question", ""),
            "expectedOutput": row.get("expert_answer", ""),
            "metadata": {"expert_id": row.get("expert_id"), "source_url": row.get("source_url")},
        })
        if "karen_answer" not in row:
            continue
        trace_id = f"{run.get('id')}-{case_id}"
        events.append(_event("trace-create", {
            "id": trace_id, "name": "karen-benchmark", "input": row.get("question", ""), "output": row["karen_answer"],
            "metadata": {"processing": row.get("processing"), "sources": row.get("sources"), "seconds": row.get("seconds")},
            "tags": ["benchmark", str(row.get("processing") or "")],
        }))
        judge = row.get("judge") or {}
        for name, key in (("karen_beter", "karen"), ("meteen_antwoord", "direct"), ("tegenspraak", "contradicts")):
            if isinstance(judge.get(key), (int, float)):
                events.append(_event("score-create", {"id": f"{trace_id}-{name}", "traceId": trace_id, "name": name, "value": judge[key]}))
        if judge.get("verdict"):
            events.append(_event("score-create", {
                "id": f"{trace_id}-oordeel", "traceId": trace_id, "name": "oordeel", "value": judge["verdict"], "dataType": "CATEGORICAL",
            }))
        links.append({"runName": run_name, "datasetItemId": case_id, "traceId": trace_id,
                      "metadata": {"run_id": run.get("id"), "karen_avg": (run.get("summary") or {}).get("karen_avg")}})
    return items, events, links


def send_benchmark_run(run: dict[str, Any]) -> None:
    """The 33 questions as a dataset, and this run as an experiment on it, with Jev's scores per answer."""
    items, events, links = benchmark_events(run)
    _quietly(lambda: _post("/api/public/datasets", {"name": DATASET, "description": "Karen tegen de energie-experts"}))
    for item in items:
        _post("/api/public/dataset-items", item)
    for start in range(0, len(events), 50):
        _post("/api/public/ingestion", {"batch": events[start:start + 50]})
    for link in links:
        _post("/api/public/dataset-run-items", link)
