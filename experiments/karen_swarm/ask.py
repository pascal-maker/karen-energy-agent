"""Send one swarm turn to a local Karen and log it.

Usage (one call per turn; persona agents run this through their shell):

    python experiments/karen_swarm/ask.py --run pilot --persona elderly --conv elderly-1 \
        --question "Wat is dat capaciteitstarief nu eigenlijk?"

- History per conversation is kept in output/<run>/state/<conv>.json (at most 6 turns, as the API allows).
- All agents share one rate limit (default 15 questions per minute) through a lock file.
- Every turn is appended to output/<run>/turns.jsonl: persona, conversation, question, answer,
  route, sources, latency, HTTP status and error.
- Refuses any base URL that is not localhost, so the live Space is never hit by accident.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

OUTPUT = Path(__file__).resolve().parent / "output"
HISTORY_LIMIT = 6


def _wait_for_slot(lock_path: Path, per_minute: int) -> None:
    """Block until fewer than `per_minute` questions were sent in the last 60 seconds, across all agents."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        while True:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            now = time.time()
            stamps = [float(line) for line in handle.read().split() if now - float(line) < 60]
            if len(stamps) < per_minute:
                stamps.append(now)
                handle.seek(0)
                handle.truncate()
                handle.write("\n".join(f"{s:.3f}" for s in stamps))
                handle.flush()
                fcntl.flock(handle, fcntl.LOCK_UN)
                return
            wait = 60 - (now - min(stamps)) + 0.1
            fcntl.flock(handle, fcntl.LOCK_UN)
            time.sleep(max(wait, 0.5))


def _post(url: str, payload: dict, timeout: float) -> tuple[int, dict | None, str | None]:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode()), None
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")[:1000]
        return error.code, None, body
    except Exception as error:  # timeouts, connection refused
        return 0, None, f"{type(error).__name__}: {error}"


FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "invoices"


def _upload(url: str, path: Path, timeout: float) -> tuple[int, dict | None, str | None]:
    """POST one fictional invoice as multipart/form-data to /api/upload."""
    boundary = uuid.uuid4().hex
    kind = "application/pdf" if path.suffix.lower() == ".pdf" else "text/plain"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
        f"Content-Type: {kind}\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode()), None
    except urllib.error.HTTPError as error:
        return error.code, None, error.read().decode(errors="replace")[:1000]
    except Exception as error:
        return 0, None, f"{type(error).__name__}: {error}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, help="run name, e.g. pilot or baseline")
    parser.add_argument("--persona", required=True)
    parser.add_argument("--conv", required=True, help="conversation id, unique within the run")
    parser.add_argument("--question", help="the visitor's question (required unless --upload)")
    parser.add_argument("--upload", help="file name of a fictional invoice in tests/fixtures/invoices to upload first")
    parser.add_argument("--base-url", default=os.getenv("KAREN_SWARM_URL", "http://127.0.0.1:7860"))
    parser.add_argument("--per-minute", type=int, default=int(os.getenv("KAREN_SWARM_PER_MINUTE", "15")))
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if not args.question and not args.upload:
        parser.error("give --question or --upload")

    if urlparse(args.base_url).hostname not in {"127.0.0.1", "localhost"}:
        print("Refusing: the swarm only runs against a local Karen.", file=sys.stderr)
        return 2

    run_dir = OUTPUT / args.run
    state_path = run_dir / "state" / f"{args.conv}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = json.loads(state_path.read_text()) if state_path.exists() else {"history": [], "turn": 0}

    _wait_for_slot(OUTPUT / "rate.lock", args.per_minute)
    started = time.monotonic()
    if args.upload:
        fixture = (FIXTURES / args.upload).resolve()
        if fixture.parent != FIXTURES.resolve() or not fixture.is_file():
            print(f"Refusing: {args.upload} is not a fixture in tests/fixtures/invoices.", file=sys.stderr)
            return 2
        args.question = f"[upload {fixture.name}]"
        payload = {"history": []}
        status, reply, error = _upload(f"{args.base_url}/api/upload", fixture, args.timeout)
        if reply and reply.get("session_id"):
            state["session_id"] = reply["session_id"]
    else:
        payload = {"question": args.question, "history": state["history"][-HISTORY_LIMIT:]}
        if state.get("session_id"):
            payload["session_id"] = state["session_id"]
        status, reply, error = _post(f"{args.base_url}/api/answer", payload, args.timeout)
    latency = round(time.monotonic() - started, 2)
    answer = (reply or {}).get("answer") or ""

    state["turn"] += 1
    if answer:
        state["history"].append({"question": args.question[:1000], "answer": answer[:4000]})
        state["history"] = state["history"][-HISTORY_LIMIT:]
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1))

    record = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run": args.run,
        "persona": args.persona,
        "conversation": args.conv,
        "turn": state["turn"],
        "question": args.question,
        "history_sent": len(payload["history"]),
        "upload_session": bool(state.get("session_id")),
        "answer": answer,
        "route": (reply or {}).get("processing"),
        "sources": (reply or {}).get("sources"),
        "latency_s": latency,
        "http_status": status,
        "error": error,
        "reply_keys": sorted(reply) if reply else None,
    }
    with (run_dir / "turns.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")

    # What the persona agent sees: only Karen's answer (plus an error line), like a real visitor.
    if error or not answer:
        print(f"[Karen gaf geen antwoord: HTTP {status} {error or 'leeg antwoord'}]")
    else:
        print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
