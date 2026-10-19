"""Optional message routing with TypeSafe AI's Jev decision model.

Jev answers typed questions with probabilities instead of writing text. Karen
asks it one Choice question per public chat message: which route fits? That
works in any language and replaces guessing from word lists. It is off unless
both TYPESAFE_API_KEY and KAREN_JEV_ROUTING=1 are set, only public questions
are sent (never PDF sessions or messages with account or invoice details),
and any error, timeout or low confidence returns None so the word-list
routing answers instead.

API: POST https://api.typesafe.ai/v1/systemone with a Bearer key, body
{"model", "state", "questions"}; each Choice answer has "choice",
"confidence" and per-option "probabilities" (see typesafe-sdk 0.7.5).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.request import Request, urlopen

from .perplexity_agent import _PRIVATE_INPUT

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
DEFAULT_MIN_CONFIDENCE = 0.6
TIMEOUT_SECONDS = 4.0

ROUTES = {
    "price": "Asks for current electricity or energy prices or tariffs in general, without giving personal household details.",
    "compare": "Wants offers or suppliers compared for their own home, or gives their region, postcode or yearly kWh usage.",
    "energy_other": "Any other energy question: invoices, advance payments, heating, boilers, gas, network costs, premiums, rules, contracts or cancelling.",
    "small_talk": "Only a greeting, thanks, goodbye, how-are-you, or a question about who Karen is.",
    "off_topic": "Not about energy at all.",
}
ROUTE_INSTRUCTIONS = (
    "The state is one chat message sent to Karen, a Belgian energy assistant. "
    "The message can be in Dutch, English or French. Which kind of message is it?"
)


def jev_routing_enabled() -> bool:
    return bool(os.getenv("TYPESAFE_API_KEY", "").strip()) and os.getenv("KAREN_JEV_ROUTING", "").strip() == "1"


def _min_confidence() -> float:
    try:
        return float(os.getenv("KAREN_JEV_MIN_CONFIDENCE", DEFAULT_MIN_CONFIDENCE))
    except ValueError:
        return DEFAULT_MIN_CONFIDENCE


def _post_system_one(body: dict[str, Any]) -> dict[str, Any]:
    base_url = os.getenv("TYPESAFE_BASE_URL", "").strip() or DEFAULT_BASE_URL
    request = Request(
        f"{base_url.rstrip('/')}/v1/systemone",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.getenv('TYPESAFE_API_KEY', '').strip()}",
            "Content-Type": "application/json",
            "User-Agent": "karen-energy-agent",
        },
        method="POST",
    )
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


async def ask_choice(state: str, instructions: str, criteria: dict[str, str]) -> dict[str, Any] | None:
    """Jev's Choice answer ({"choice", "confidence", "probabilities"}), or None on any failure."""
    body = {
        "model": os.getenv("TYPESAFE_DEFAULT_MODEL", "").strip() or DEFAULT_MODEL,
        "state": state,
        "questions": {"answer": {"type": "choice", "instructions": instructions, "criteria": criteria}},
    }
    try:
        result = await asyncio.to_thread(_post_system_one, body)
    except Exception:
        return None
    answer = (result.get("answers") or {}).get("answer") if isinstance(result, dict) else None
    if not isinstance(answer, dict) or answer.get("type") != "choice" or answer.get("choice") not in criteria:
        return None
    if not isinstance(answer.get("confidence"), (int, float)):
        return None
    return answer


async def route_question(question: str) -> str | None:
    """One of ROUTES when Jev is enabled and confident enough, else None."""
    question = question.strip()
    if not jev_routing_enabled() or not 2 <= len(question) <= 500 or _PRIVATE_INPUT.search(question):
        return None
    answer = await ask_choice(question, ROUTE_INSTRUCTIONS, ROUTES)
    if answer is None or answer["confidence"] < _min_confidence():
        return None
    return answer["choice"]
