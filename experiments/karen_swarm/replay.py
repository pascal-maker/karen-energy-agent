"""Replay the visitor's questions of an earlier run against a local Karen (the regression set).

Usage:
    python experiments/karen_swarm/replay.py --from full-5175ecf --run pr1-accept social-4 elderly-3
    python experiments/karen_swarm/replay.py --from full-5175ecf --run pr1-full        # every conversation

The same questions are sent in the same order, each conversation with its own history, through
ask.py (localhost only, shared rate limit). A question the persona wrote in reaction to Karen's old
answer is still sent as it was, so a replay measures the fixed Karen on the audited questions; a
new, unseen set of conversations is a separate swarm run.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPLOAD = re.compile(r"^\[upload (.+)\]$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="source", required=True, help="earlier run whose questions are replayed")
    parser.add_argument("--run", required=True, help="new run name")
    parser.add_argument("conversations", nargs="*", help="conversation ids; all when left out")
    args = parser.parse_args()

    rows = [json.loads(line) for line in (HERE / "output" / args.source / "turns.jsonl").open(encoding="utf-8")]
    by_conversation: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("http_status") == 200 or row["question"].startswith("[upload"):
            by_conversation[row["conversation"]].append(row)
    wanted = args.conversations or sorted(by_conversation)
    for conversation in wanted:
        turns = sorted(by_conversation[conversation], key=lambda row: row["turn"])
        for row in turns:
            command = [sys.executable, str(HERE / "ask.py"), "--run", args.run,
                       "--persona", row["persona"], "--conv", conversation]
            upload = UPLOAD.match(row["question"])
            command += ["--upload", upload.group(1)] if upload else ["--question", row["question"]]
            subprocess.run(command, check=False, stdout=subprocess.DEVNULL)
        print(f"{conversation}: {len(turns)} turns", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
