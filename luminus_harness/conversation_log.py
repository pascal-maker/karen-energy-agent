"""A private log of every public question and Karen's answer, for the owner's overview page.

Each record holds the question, the answer, how Karen got there (Jev's route and
probabilities, the processing path, the sources) and how long it took. PDF and photo
answers are never logged. Parts of a question that look like account or invoice details
(e-mail, IBAN, EAN, customer numbers) are masked before anything is stored.

Records go to a local JSONL file right away and, when HF_TOKEN and KAREN_FEEDBACK_DATASET
are set, are uploaded in batches to that private Hugging Face dataset under
conversations/<date>/, one new file per batch, so a Space restart never overwrites
earlier batches.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

FLUSH_SECONDS = 5 * 60
FLUSH_RECORDS = 50
_MASK = "[verborgen]"
# The private-input pattern also flags phrases like "mijn factuur"; only mask real identifiers.
_IDENTIFIERS = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\b(?:BE\d{2}[\s-]?)?(?:\d{4}[\s-]?){3,4}\b|\b\d{18}\b",
    re.IGNORECASE,
)

_pending: list[dict[str, Any]] = []
_lock = threading.Lock()
_last_flush = time.monotonic()
_flusher: threading.Thread | None = None


def _flush_regularly() -> None:
    while True:
        time.sleep(FLUSH_SECONDS)
        flush()


def mask_private(text: str) -> str:
    return _IDENTIFIERS.sub(_MASK, text)


def build_log_record(question: str, reply: dict[str, Any], steps: list[dict[str, Any]], seconds: float) -> dict[str, Any]:
    route = next((step for step in steps if step.get("kind") == "route"), None)
    sources = [
        {"title": str(source.get("title", ""))[:200], "url": str(source.get("url", ""))[:500]}
        for source in reply.get("sources") or []
        if isinstance(source, dict) and source.get("url")
    ]
    return {
        "id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": mask_private(question)[:1000],
        "answer": str(reply.get("answer", ""))[:4000],
        "processing": str(reply.get("processing", "")),
        "route": route.get("choice") if route else None,
        "route_confidence": route.get("confidence") if route else None,
        "route_probabilities": route.get("probabilities") if route else None,
        "searches": sum(1 for step in steps if step.get("kind") == "search"),
        "search_errors": [str(step.get("message", ""))[:300] for step in steps if step.get("kind") == "search_error"],
        "sources": sources,
        "seconds": round(seconds, 1),
    }


def _local_path() -> Path:
    directory = os.getenv("KAREN_FEEDBACK_DIR", "").strip()
    if not directory:
        directory = "/data/karen-feedback" if os.access("/data", os.W_OK) else "/tmp/karen-feedback"
    return Path(directory) / "conversations.jsonl"


def _dataset() -> tuple[str, str] | None:
    token = os.getenv("HF_TOKEN", "").strip()
    dataset = os.getenv("KAREN_FEEDBACK_DATASET", "").strip()
    return (token, dataset) if token and dataset else None


def store_log_record(record: dict[str, Any]) -> None:
    """Append locally and queue for the next batch upload."""
    path = _local_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
    global _flusher
    with _lock:
        _pending.append(record)
        if _flusher is None:
            # A quiet hour after a burst still reaches the dataset.
            _flusher = threading.Thread(target=_flush_regularly, name="karen-log-flush", daemon=True)
            _flusher.start()
            # A Space restart stops the server normally: write what is still queued first.
            atexit.register(flush)
        due = len(_pending) >= FLUSH_RECORDS or time.monotonic() - _last_flush >= FLUSH_SECONDS
    if due:
        flush()


def flush() -> bool:
    """Upload queued records as one new file. True when something reached the dataset."""
    global _last_flush
    with _lock:
        batch = list(_pending)
        _pending.clear()
        _last_flush = time.monotonic()
    if not batch:
        return False
    target = _dataset()
    if target is None:
        return False
    token, dataset = target
    try:
        from huggingface_hub import HfApi

        api = HfApi(token=token)
        api.create_repo(dataset, repo_type="dataset", private=True, exist_ok=True)
        stamp = batch[0]["created_at"]
        api.upload_file(
            path_or_fileobj="".join(json.dumps(record, ensure_ascii=False) + "\n" for record in batch).encode("utf-8"),
            path_in_repo=f"conversations/{stamp[:10]}/{stamp[11:19].replace(':', '')}-{uuid.uuid4().hex[:8]}.jsonl",
            repo_id=dataset,
            repo_type="dataset",
            commit_message=f"Karen conversations: {len(batch)}",
        )
        return True
    except Exception:
        # Keep the records for the next try instead of losing them.
        with _lock:
            _pending[:0] = batch
        return False


def pending_records() -> list[dict[str, Any]]:
    with _lock:
        return list(_pending)


def _read_jsonl(text: str) -> list[dict[str, Any]]:
    records = []
    for line in text.splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def load_recent(days: int = 7) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Conversations and feedback of the last days, newest first, from the dataset plus what is still queued."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    conversations: dict[str, dict[str, Any]] = {}
    feedback: list[dict[str, Any]] = []
    target = _dataset()
    if target is not None:
        token, dataset = target
        from huggingface_hub import HfApi, hf_hub_download

        api = HfApi(token=token)
        try:
            files = api.list_repo_files(dataset, repo_type="dataset")
        except Exception:
            files = []
        for name in files:
            parts = name.split("/")
            if len(parts) < 3 or parts[1] < since or parts[0] not in ("conversations", "feedback"):
                continue
            try:
                text = Path(hf_hub_download(dataset, name, repo_type="dataset", token=token)).read_text(encoding="utf-8")
            except Exception:
                continue
            if parts[0] == "conversations":
                for record in _read_jsonl(text):
                    conversations[record.get("id", uuid.uuid4().hex)] = record
            else:
                try:
                    feedback.append(json.loads(text))
                except json.JSONDecodeError:
                    pass
    else:
        try:
            for record in _read_jsonl(_local_path().read_text(encoding="utf-8")):
                if record.get("created_at", "")[:10] >= since:
                    conversations[record.get("id", uuid.uuid4().hex)] = record
        except OSError:
            pass
    for record in pending_records():
        conversations[record["id"]] = record
    newest_first = sorted(conversations.values(), key=lambda record: record.get("created_at", ""), reverse=True)
    return newest_first, sorted(feedback, key=lambda record: record.get("created_at", ""), reverse=True)
