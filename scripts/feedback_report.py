"""List Karen's 👎 answers from the feedback dataset, newest first, to turn into test cases.

    HF_TOKEN=... KAREN_FEEDBACK_DATASET=you/karen-feedback python scripts/feedback_report.py [--all]
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter


def main() -> int:
    from huggingface_hub import HfApi, hf_hub_download

    dataset = os.getenv("KAREN_FEEDBACK_DATASET", "").strip()
    token = os.getenv("HF_TOKEN", "").strip()
    if not dataset or not token:
        print("Set HF_TOKEN and KAREN_FEEDBACK_DATASET first.")
        return 2
    files = sorted(
        (name for name in HfApi(token=token).list_repo_files(dataset, repo_type="dataset") if name.startswith("feedback/")),
        reverse=True,
    )
    records = []
    for name in files:
        with open(hf_hub_download(dataset, name, repo_type="dataset", token=token), encoding="utf-8") as file:
            records.append(json.load(file))
    ratings = Counter(record["rating"] for record in records)
    print(f"{len(records)} ratings: {ratings['up']} 👍, {ratings['down']} 👎")
    by_route = Counter(record["processing"] for record in records if record["rating"] == "down")
    if by_route:
        print("👎 by route: " + ", ".join(f"{route or 'unknown'} {count}" for route, count in by_route.most_common()))
    for record in records:
        if record["rating"] == "down" or "--all" in sys.argv:
            print(f"\n{record['created_at']}  {record['rating']}  [{record['processing']}]")
            print(f"Q: {record['question']}")
            print(f"A: {record['answer'][:400]}")
            if record["comment"]:
                print(f"Comment: {record['comment']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
