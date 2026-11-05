"""Compare how two versions of Karen answer questions about an uploaded invoice (evals/invoice_understanding.json).

    python scripts/eval_invoice_understanding.py                      # this checkout
    python scripts/eval_invoice_understanding.py --baseline ../main   # this checkout against another one

Runs locally on the synthetic invoices in tests/fixtures/invoices; no hosted model, no network apart from the
one-time download of the local embedding model. A version without invoice_session (before 2026-11-05) is asked
the way the app asked it then: one question at a time, no turns kept, found=False meaning "left the bill".

Scores per item (1 = pass):
- source: the supporting passage (or value) appears in the answer or its sources
- supported: everything the answer must say is there and nothing it must not say
- address: for address questions, the supported score (type named, no claim about where someone lives)
- behavior: answered, said what is missing, asked back, or left the bill, as expected
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "invoices"
EVAL = ROOT / "evals" / "invoice_understanding.json"


def _ask_all(code: Path) -> list[dict]:
    sys.path.insert(0, str(code))
    items = json.loads(EVAL.read_text())["items"]
    try:
        from luminus_harness.invoice_session import open_invoice_session
    except ImportError:
        open_invoice_session = None
    from luminus_harness.invoice_analysis import analyze_invoice_chunks, answer_uploaded_pdf_question
    from luminus_harness.knowledge import load_pdf_chunks_from_bytes

    results = []
    for item in items:
        data = (FIXTURES / item["fixture"]).read_bytes()
        if open_invoice_session is not None:
            session = open_invoice_session(data, item["fixture"])
            for turn in item.get("turns", []):
                session.ask(turn)
            reply = session.ask(item["question"])
        else:
            chunks = load_pdf_chunks_from_bytes(data, source=item["fixture"])
            analysis = analyze_invoice_chunks(chunks)
            reply = answer_uploaded_pdf_question(item["question"], chunks, analysis["facts"], analysis["invoice"])
        shown = reply["answer"] + "\n" + "\n".join(source.get("excerpt", "") for source in reply.get("sources", []))
        results.append({"id": item["id"], "answer": reply["answer"], "shown": shown, "found": reply.get("found", True)})
    return results


def score(item: dict, result: dict) -> dict[str, int | None]:
    answer, shown, found = result["answer"], result["shown"], result["found"]
    behavior = item["behavior"]
    supported = all(text in answer for text in item["contains"]) and not any(text in shown for text in item["excludes"])
    asks = found and "?" in answer
    if behavior == "leave":
        right = not found
    elif behavior == "no_bill_answer":
        right = (not found) or asks or "Ik weet niet zeker" in answer
    elif behavior == "ask_back":
        right = asks
    else:  # answer, missing: from the bill, no question back, not left
        right = found and not answer.startswith("Ik weet niet zeker")
    supporting = item.get("supporting")
    return {
        "source": None if not supporting else int(" ".join(supporting.split()) in " ".join(shown.split())),
        "supported": int(supported and right) if behavior in ("answer", "missing", "ask_back", "no_bill_answer") else None,
        "address": int(supported and right) if item["category"] == "address" else None,
        "behavior": int(right),
    }


def _run(code: Path) -> list[dict]:
    output = subprocess.run(
        [sys.executable, "-W", "ignore", __file__, "--answers-from", str(code)], capture_output=True, text=True, check=True, cwd=ROOT,
    ).stdout
    return json.loads(output)


def _summary(name: str, items: list[dict], results: list[dict]) -> tuple[dict, list[dict]]:
    scores = [score(item, result) for item, result in zip(items, results)]
    totals = {}
    for metric in ("source", "supported", "address", "behavior"):
        values = [entry[metric] for entry in scores if entry[metric] is not None]
        totals[metric] = f"{sum(values)}/{len(values)}"
    return {"version": name, **totals}, scores


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, help="another checkout (for example a worktree of main)")
    parser.add_argument("--answers-from", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--details", action="store_true", help="print every answer")
    args = parser.parse_args()
    if args.answers_from:
        print(json.dumps(_ask_all(args.answers_from), ensure_ascii=False))
        return
    items = json.loads(EVAL.read_text())["items"]
    versions = ([("baseline", args.baseline.resolve())] if args.baseline else []) + [("new", ROOT)]
    rows, per_version = [], {}
    for name, code in versions:
        results = _run(code)
        row, scores = _summary(name, items, results)
        rows.append(row)
        per_version[name] = (results, scores)
    print("| version | right source | answer supported | address type | answer / missing / ask back / leave |")
    print("|---|---|---|---|---|")
    for row in rows:
        print(f"| {row['version']} | {row['source']} | {row['supported']} | {row['address']} | {row['behavior']} |")
    print()
    print("| item | " + " | ".join(name for name, _ in versions) + " |")
    print("|---|" + "---|" * len(versions))
    for index, item in enumerate(items):
        cells = []
        for name, _ in versions:
            entry = per_version[name][1][index]
            cells.append("".join("✓" if entry[m] else ("·" if entry[m] is None else "✗") for m in ("source", "supported", "behavior")))
        print(f"| {item['id']} | " + " | ".join(cells) + " |")
    if args.details:
        for index, item in enumerate(items):
            print(f"\n## {item['id']}: {item['question']}")
            for name, _ in versions:
                print(f"- {name}: {per_version[name][0][index]['answer'][:300]!r}")


if __name__ == "__main__":
    main()
