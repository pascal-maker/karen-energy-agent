"""Code checks over one swarm run (phase 3 of docs/swarm-test-plan.md).

Usage:
    python experiments/karen_swarm/checks.py full-5175ecf

Writes output/<run>/checks.jsonl (one row per turn, with the failed checks) and prints counts per
check and per persona. These checks are cheap heuristics; the judge (Claude + Codex) decides the
content questions (invented claim, not answered, wrong route, personal data).
"""

from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent / "output"
FRENCH_PERSONAS = {"brussels-fr", "wallonia-fr"}
FRENCH_CONVERSATIONS = {"angry-bill-5"}

NL_WORDS = re.compile(r"\b(het|een|niet|ik|dat|voor|wordt|kan|geen|bronnen|zeker|jouw|wat|ook)\b", re.I)
FR_WORDS = re.compile(r"\b(le|la|les|une|tu|pas|est|pour|ton|des|que|vous|votre)\b", re.I)
DUTCH_IN_FRENCH = re.compile(
    r"\b(netbeheerder|vergelijkingsinstrument|leverancier|voorschot|afrekening|zeker|bronnen|verbruik|"
    r"energieovernamedocument|beschermde klant|Hoe aanvragen)\b", re.I)
FLEMISH_SOURCES = re.compile(r"\b(V-test|Fluvius|VREG|Vlaanderen|Vlaamse|Flandre)\b", re.I)
STOCK_REFUSAL = re.compile(
    r"^(Dat weet ik niet zeker|Je n'en suis pas sûre|Je n’en suis pas sûre|Je weet het niet zeker|Je sais pas)", re.I)
TEMPLATE_LEAK = re.compile(r"\*\*Kort label:\*\*|\{\{|\}\}|<\?claude|\bTODO\b", re.I)
MAX_CHARS = 1800
SLOW_S = 30


def _french_expected(row: dict) -> bool:
    return row["persona"] in FRENCH_PERSONAS or row["conversation"] in FRENCH_CONVERSATIONS


def check(row: dict) -> list[str]:
    answer = row.get("answer") or ""
    failed = []
    if row.get("http_status") != 200 or row.get("error"):
        failed.append("http_error")
    if not answer.strip():
        failed.append("empty_answer")
        return failed
    if (row.get("latency_s") or 0) > SLOW_S:
        failed.append("slow_over_30s")
    if len(answer) > MAX_CHARS:
        failed.append("too_long")
    if STOCK_REFUSAL.search(answer.strip()):
        failed.append("stock_refusal")
    if TEMPLATE_LEAK.search(answer):
        failed.append("template_leak")
    if not row.get("sources") and not row["question"].startswith("[upload"):
        failed.append("no_sources")
    head = answer[:500]
    if _french_expected(row):
        if len(NL_WORDS.findall(head)) > len(FR_WORDS.findall(head)):
            failed.append("wrong_language_nl_for_fr")
        elif DUTCH_IN_FRENCH.search(answer):
            failed.append("dutch_words_in_french")
        if FLEMISH_SOURCES.search(answer):
            failed.append("flemish_source_for_fr_region")
    elif len(FR_WORDS.findall(head)) > len(NL_WORDS.findall(head)) + 3:
        failed.append("wrong_language_fr_for_nl")
    return failed


def main() -> int:
    run = sys.argv[1] if len(sys.argv) > 1 else "full-5175ecf"
    run_dir = OUTPUT / run
    rows = [json.loads(line) for line in (run_dir / "turns.jsonl").open(encoding="utf-8")]
    per_check: collections.Counter[str] = collections.Counter()
    per_persona: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    with (run_dir / "checks.jsonl").open("w", encoding="utf-8") as out:
        for row in rows:
            failed = check(row)
            per_check.update(failed)
            per_persona[row["persona"]].update(failed)
            per_persona[row["persona"]]["_turns"] += 1
            out.write(json.dumps({"conversation": row["conversation"], "turn": row["turn"],
                                  "route": row.get("route"), "failed": failed}, ensure_ascii=False) + "\n")
    latencies = sorted(r.get("latency_s") or 0 for r in rows)
    print(f"run {run}: {len(rows)} turns, {len({r['conversation'] for r in rows})} conversations, "
          f"latency median {latencies[len(latencies) // 2]}s, max {latencies[-1]}s")
    print("\nper check:")
    for name, count in per_check.most_common():
        print(f"  {name:32} {count}")
    print("\nper persona (turns, then failed checks):")
    for persona, counts in sorted(per_persona.items()):
        turns = counts.pop("_turns")
        print(f"  {persona:14} {turns:3}  " + ", ".join(f"{k}={v}" for k, v in counts.most_common()))
    print("\nroutes:", dict(collections.Counter(r.get("route") for r in rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
