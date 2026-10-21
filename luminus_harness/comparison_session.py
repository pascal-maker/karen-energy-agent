"""Recognises offer-comparison questions; Karen answers them with supplier cards, not a form."""

from __future__ import annotations

import re


def is_offer_comparison_question(question: str) -> bool:
    text = question.casefold()
    action = re.search(r"\b(?:vergelijk\w*|compar(?:e|ing|ison|er)|goedkoop\w*|voordelig\w*|cheapest|best(?:e)?)\b", text)
    subject = re.search(r"\b(?:energie\w*|elektriciteit|elektriciteits\w*|stroom|aardgas\w*|gas(?:aanbiedingen|contracten|tarieven)?|electricity|\w*leveranciers?|providers?|offers?|offres?|aanbiedingen|contracten?|tarieven|v-test|brusim|compacwape|kwh)\b", text)
    return bool(action and subject)


def _reply(answer: str, status: str, session_id: str | None = None, **extra) -> dict:
    return {
        "answer": answer, "sources": [], "suggested_sources": [],
        "processing": "regional-offer-comparison", "comparison_status": status,
        "comparison_session_id": session_id, "live_source_status": "not-needed", **extra,
    }


def pdf_comparison_reply() -> dict:
    return _reply("Verwijder eerst je PDF via ‘Verwijder’ om energieaanbiedingen te vergelijken. Je PDF blijft alleen op Karen’s backend verwerkt.", "pdf-blocked", processing="local-only")
