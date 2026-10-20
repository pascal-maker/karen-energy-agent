"""Ask the live Karen backend a fixed set of knowledge questions and score the answers.

    python scripts/check_search_quality.py [--base https://...hf.space] [--only fluvius,news]

Each answer is scored as `sourced` (answer with at least one source), `no-source`
("Dat weet ik niet zeker"), `off-topic` (Karen treated an energy question as off-topic),
or `error`. Prints one line per question and a summary; rerun after a search change to
compare. Questions run one by one with a pause, within Karen's own rate limit.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE = "https://pascal-maker-belgian-energy-voice-assistant.hf.space"

# (topic, question). Topics are the kinds of sites that should answer them.
CASES = [
    ("official", "Wie heeft recht op het sociaal tarief?"),
    ("official", "Wat is het prosumententarief?"),
    ("official", "Wat is het capaciteitstarief?"),
    ("official", "Wat zijn netkosten?"),
    ("official", "Krijg ik een premie voor een warmtepomp?"),
    ("official", "Hoe vaak moet mijn gasketel onderhouden worden?"),
    ("official", "Wat doet de VREG?"),
    ("official", "Wat is Mijn VerbouwPremie?"),
    ("official", "Hoeveel btw betaal ik op elektriciteit?"),
    ("official", "Wat is een energielening?"),
    ("official", "Wat moet ik doen als ik mijn energiefactuur niet kan betalen?"),
    ("official", "Waar kan ik klagen over mijn energieleverancier?"),
    ("fluvius", "Hoe lees ik mijn digitale meter af?"),
    ("fluvius", "Wat moet ik doen bij een stroompanne?"),
    ("fluvius", "Hoe vraag ik een nieuwe aansluiting aan?"),
    ("fluvius", "Wat is een budgetmeter?"),
    ("fluvius", "Wat moet ik doen als ik verhuis met mijn energiecontract?"),
    ("fluvius", "Hoe kan ik mijn capaciteitstarief verlagen?"),
    ("fluvius", "Wat is energiedelen?"),
    ("practical", "Is een thuisbatterij interessant?"),
    ("practical", "Zijn zonnepanelen nog interessant?"),
    ("practical", "Wat is een dynamisch energiecontract?"),
    ("practical", "Hoe kan ik energie besparen in huis?"),
    ("practical", "Wat kost een laadpaal thuis?"),
    ("practical", "Wat is groene stroom precies?"),
    ("practical", "Kost het iets om van leverancier te veranderen?"),
    ("news", "Waarom stijgt de gasprijs?"),
    ("news", "Waarom zijn de energieprijzen zo hoog?"),
    ("news", "Wat is het laatste nieuws over de energieprijzen?"),
    ("other-language", "Who is entitled to the social tariff?"),
    ("other-language", "Comment lire mon compteur digital ?"),
]

_NO_SOURCE_STARTS = ("Dat weet ik niet zeker", "I'm not sure about that one", "Je n'en suis pas sûre")
_OFF_TOPIC_HINTS = ("focus me", "alleen energievragen", "mostly know about energy", "surtout en énergie", "buiten energie")


def ask(base: str, question: str) -> tuple[str, dict, float]:
    request = Request(
        f"{base}/api/answer",
        data=json.dumps({"question": question}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urlopen(request, timeout=150) as response:
            reply = json.loads(response.read())
    except HTTPError as error:
        return "error", {"detail": error.read().decode(errors="replace")[:200]}, time.monotonic() - started
    except (URLError, TimeoutError) as error:
        return "error", {"detail": str(error)}, time.monotonic() - started
    answer = reply.get("answer", "")
    if answer.startswith(_NO_SOURCE_STARTS):
        verdict = "no-source"
    elif reply.get("sources"):
        verdict = "sourced"
    elif any(hint in answer for hint in _OFF_TOPIC_HINTS):
        verdict = "off-topic"
    else:
        verdict = "unsourced"
    return verdict, reply, time.monotonic() - started


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--only", default="", help="comma-separated topics")
    parser.add_argument("--pause", type=float, default=4.0)
    args = parser.parse_args()
    topics = {topic for topic in args.only.split(",") if topic}
    counts: dict[str, int] = {}
    for topic, question in CASES:
        if topics and topic not in topics:
            continue
        verdict, reply, seconds = ask(args.base, question)
        counts[verdict] = counts.get(verdict, 0) + 1
        domains = sorted({source["url"].split("/")[2] for source in reply.get("sources", []) if source.get("url", "").startswith("http")})
        text = (reply.get("answer") or reply.get("detail") or "").replace("\n", " ")[:90]
        print(f"{verdict:10} {seconds:5.1f}s [{topic}] {question}\n           {', '.join(domains) or '-'} | {text}", flush=True)
        time.sleep(args.pause)
    total = sum(counts.values())
    print("\n" + ", ".join(f"{verdict}: {count}" for verdict, count in sorted(counts.items())) + f" (of {total})")
    return 0 if counts.get("sourced", 0) == total else 1


if __name__ == "__main__":
    sys.exit(main())
