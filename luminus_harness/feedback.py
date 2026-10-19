"""👍/👎 feedback on Karen's public answers.

Every public answer carries a short-lived signed token holding the question,
the answer and the route that produced it. The feedback endpoint only accepts
a valid token, so people can rate what Karen said but can't inject other text,
and PDF or comparison answers (which get no token) are never stored. A comment
that looks like account or invoice details is dropped; the rating is kept.

Records are appended to a local JSONL file and, when HF_TOKEN and
KAREN_FEEDBACK_DATASET are set, uploaded one file per record to that private
Hugging Face dataset, so they survive Space restarts. 👎 answers become test
cases for the routing and prompts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .perplexity_agent import _PRIVATE_INPUT

TOKEN_TTL_SECONDS = 60 * 60
MAX_COMMENT_CHARS = 500
_secret = secrets.token_bytes(32)


class FeedbackError(ValueError):
    pass


def sign_feedback(question: str, answer: str, processing: str) -> str:
    payload = json.dumps(
        {"q": question[:1000], "a": answer[:4000], "p": processing, "exp": int(time.time()) + TOKEN_TTL_SECONDS},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(_secret, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + signature).decode("ascii")


def read_feedback_token(token: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(token, altchars=b"-_", validate=True)
        payload, signature = raw[:-32], raw[-32:]
        if not hmac.compare_digest(signature, hmac.new(_secret, payload, hashlib.sha256).digest()):
            raise ValueError("invalid signature")
        data = json.loads(payload)
    except Exception as error:
        raise FeedbackError("Deze feedback hoort niet bij een recent antwoord van Karen.") from error
    if data.get("exp", 0) < time.time():
        raise FeedbackError("Dit antwoord is te oud om nog te beoordelen.")
    return data


def build_record(token: str, rating: str, comment: str | None) -> dict[str, Any]:
    if rating not in ("up", "down"):
        raise FeedbackError("Beoordeel met up of down.")
    data = read_feedback_token(token)
    comment = (comment or "").strip()[:MAX_COMMENT_CHARS]
    if _PRIVATE_INPUT.search(comment):
        comment = ""
    return {
        "id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rating": rating,
        "comment": comment,
        "question": data["q"],
        "answer": data["a"],
        "processing": data["p"],
    }


def _local_path() -> Path:
    directory = os.getenv("KAREN_FEEDBACK_DIR", "").strip()
    if not directory:
        directory = "/data/karen-feedback" if os.access("/data", os.W_OK) else "/tmp/karen-feedback"
    return Path(directory) / "feedback.jsonl"


def _upload(record: dict[str, Any]) -> bool:
    token = os.getenv("HF_TOKEN", "").strip()
    dataset = os.getenv("KAREN_FEEDBACK_DATASET", "").strip()
    if not token or not dataset:
        return False
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(dataset, repo_type="dataset", private=True, exist_ok=True)
    api.upload_file(
        path_or_fileobj=json.dumps(record, ensure_ascii=False, indent=1).encode("utf-8"),
        path_in_repo=f"feedback/{record['created_at'][:10]}/{record['id']}.json",
        repo_id=dataset,
        repo_type="dataset",
        commit_message=f"Karen feedback: {record['rating']}",
    )
    return True


def store_feedback(record: dict[str, Any]) -> bool:
    """Append locally and upload when configured. True when the record reached the dataset."""
    path = _local_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
    try:
        return _upload(record)
    except Exception:
        return False
