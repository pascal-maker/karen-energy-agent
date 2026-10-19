"""Check Jev's routing on real Karen questions before turning KAREN_JEV_ROUTING on.

    TYPESAFE_API_KEY=... python scripts/check_jev_routing.py

Prints Jev's route and confidence next to the expected route for each question,
and exits non-zero if any confident answer picks the wrong route.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from luminus_harness import jev  # noqa: E402

CASES = [
    ("look up energy prices", "price"),
    ("can you look up the cheapest energy prices", "price"),
    ("wat zijn de stroomprijzen vandaag", "price"),
    ("zoek de beste energieprijzen", "price"),
    ("what is the electricity price today", "price"),
    ("how much does power go for these days?", "price"),
    ("quel est le prix de l'électricité aujourd'hui ?", "price"),
    ("Vergelijk aanbiedingen", "compare"),
    ("Ik woon in Gent, 9000, en verbruik 3500 kWh per jaar. Wat is het goedkoopste contract?", "compare"),
    ("ik wil overstappen naar een goedkopere leverancier", "compare"),
    ("Waarom verschilt mijn voorschot van mijn jaarafrekening?", "energy_other"),
    ("Hoe vaak moet mijn gasketel in Brussel gekeurd worden?", "energy_other"),
    ("Wat is het capaciteitstarief?", "energy_other"),
    ("Wat kost het om mijn energiecontract op te zeggen?", "energy_other"),
    ("Hoeveel kost aardgas per kWh?", "energy_other"),
    ("hey hoe gaat het ermee?", "small_talk"),
    ("thanks a lot!", "small_talk"),
    ("Wie ben jij?", "small_talk"),
    ("Wie wint de Ronde van Vlaanderen?", "off_topic"),
    ("what's the weather tomorrow?", "off_topic"),
]


async def main() -> int:
    if not jev.os.getenv("TYPESAFE_API_KEY", "").strip():
        print("Set TYPESAFE_API_KEY first.")
        return 2
    wrong = unsure = 0
    for question, expected in CASES:
        answer = await jev.ask_choice(question, jev.ROUTE_INSTRUCTIONS, jev.ROUTES)
        if answer is None:
            print(f"ERROR     {expected:12} {question}")
            wrong += 1
            continue
        confident = answer["confidence"] >= jev._min_confidence()
        ok = answer["choice"] == expected
        mark = "ok" if ok else ("WRONG" if confident else "unsure")
        wrong += confident and not ok
        unsure += not confident
        print(f"{mark:9} {answer['choice']:12} {answer['confidence']:.2f}  (expected {expected}) {question}")
    print(f"\n{len(CASES)} questions, {wrong} wrong while confident, {unsure} below the confidence threshold")
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
