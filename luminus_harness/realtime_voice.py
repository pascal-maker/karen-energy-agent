"""Real-time voice for Karen: OpenAI Realtime is only her ears and her voice, never her answer.

The browser talks to OpenAI Realtime over WebRTC with a short-lived key minted here. Turn detection
runs there, but with ``create_response`` off: the model never answers on its own. When a question is
transcribed, the browser asks Karen's normal answer route (safety layer, routes, sources) and then
asks the model to read that answer aloud, word for word. A spoken answer is therefore always one
that Karen wrote in code or with her sources; Realtime only says it.

Off unless ``KAREN_REALTIME=1``; it needs ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import threading
from typing import Any

MODEL = os.getenv("KAREN_REALTIME_MODEL", "gpt-realtime")
VOICE = os.getenv("KAREN_REALTIME_VOICE", "marin")
TRANSCRIBE_MODEL = os.getenv("KAREN_REALTIME_STT_MODEL", "gpt-4o-mini-transcribe")
# The key is only needed to open the connection; the browser ends a conversation after this long.
KEY_SECONDS = 60
MAX_CONVERSATION_SECONDS = int(os.getenv("KAREN_REALTIME_MAX_SECONDS", "300"))
DAILY_SESSIONS = int(os.getenv("KAREN_REALTIME_DAILY_SESSIONS", "100"))
MAX_SPOKEN_CHARS = 900

SESSION_INSTRUCTIONS = (
    "Je bent de stem van Karen, een Belgische energieassistent. Je bent alleen een voorleesstem. "
    "Je beantwoordt nooit zelf een vraag en je geeft nooit eigen informatie, advies, nummers of bedragen. "
    "Je spreekt alleen wanneer je een tekst krijgt om voor te lezen, en dan lees je die letterlijk voor. "
    "Spreek warm, rustig en duidelijk."
)

GREETINGS = {
    "nl": "Hey, ik ben Karen, jouw energieagent. Stel me gerust je vraag over energie.",
    "fr": "Bonjour, je suis Karen, votre agente énergie. Posez-moi votre question sur l'énergie.",
    "en": "Hi, I'm Karen, your energy agent. Ask me anything about energy.",
}
WAIT_LINES = {"nl": "Even kijken.", "fr": "Un instant, je regarde.", "en": "One moment, let me check."}

_lock = threading.Lock()
_day: dt.date | None = None
_sessions_today = 0


def enabled() -> bool:
    return os.getenv("KAREN_REALTIME", "").strip() == "1" and bool(os.getenv("OPENAI_API_KEY", "").strip())


def take_daily_slot(today: dt.date | None = None) -> bool:
    """Count one conversation against the daily cap; False when the cap is reached."""
    global _day, _sessions_today
    today = today or dt.date.today()
    with _lock:
        if _day != today:
            _day, _sessions_today = today, 0
        if _sessions_today >= DAILY_SESSIONS:
            return False
        _sessions_today += 1
        return True


def session_config() -> dict[str, Any]:
    transcription: dict[str, Any] = {
        "model": TRANSCRIBE_MODEL,
        "prompt": "Belgische energievragen: Fluvius, Sibelga, ORES, RESA, sociaal tarief, kWh, postcodes. "
                  "Questions sur l'énergie en Belgique.",
    }
    return {
        "type": "realtime",
        "model": MODEL,
        "instructions": SESSION_INSTRUCTIONS,
        "output_modalities": ["audio"],
        "tool_choice": "none",
        "max_output_tokens": 1500,
        "audio": {
            "input": {
                "transcription": transcription,
                "noise_reduction": {"type": "near_field"},
                # The model never answers by itself; the browser decides what is said.
                "turn_detection": {
                    "type": "server_vad",
                    "create_response": False,
                    "interrupt_response": True,
                    "silence_duration_ms": 700,
                },
            },
            "output": {"voice": VOICE},
        },
    }


async def create_client_secret(language: str = "nl") -> dict[str, Any]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    secret = await client.realtime.client_secrets.create(
        expires_after={"anchor": "created_at", "seconds": KEY_SECONDS},
        session=session_config(),  # type: ignore[arg-type]
    )
    return {
        "client_secret": secret.value,
        "expires_at": secret.expires_at,
        "model": MODEL,
        "max_seconds": MAX_CONVERSATION_SECONDS,
        "greeting": read_aloud(GREETINGS.get(language, GREETINGS["nl"])),
        "wait": read_aloud(WAIT_LINES.get(language, WAIT_LINES["nl"])),
    }


_URL = re.compile(r"https?://\S+|www\.\S+")
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def spoken_text(answer: str) -> str:
    """The answer as it should sound: no links, markdown or source lists, at most MAX_SPOKEN_CHARS."""
    text = _MARKDOWN_LINK.sub(r"\1", answer or "")
    text = _URL.sub("", text)
    # A pointer left without its link ("Zie .", "Plus d'infos :") says nothing aloud.
    text = re.sub(r"(?im)\b(?:zie|bekijk|meer info(?:rmatie)?(?: op)?|voir|plus d'infos?|see|more info(?: at)?)\s*[:.]?\s*$", "", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line)
        line = re.sub(r"[*#_`>|]+", "", line).strip()
        if line:
            lines.append(line if re.search(r"[.!?:;]$", line) else line + ".")
    text = re.sub(r"\s+", " ", " ".join(lines)).strip()
    text = re.sub(r"\(\s*\)", "", text).replace(" .", ".")
    if len(text) <= MAX_SPOKEN_CHARS:
        return text
    cut = text[:MAX_SPOKEN_CHARS]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > MAX_SPOKEN_CHARS // 3 else cut.rsplit(" ", 1)[0] + "…"


def read_aloud(text: str) -> dict[str, Any]:
    """The `response` of a response.create event that reads `text` and nothing else."""
    return {
        "instructions": (
            "Lees de tekst tussen <tekst> en </tekst> letterlijk voor, in de taal van die tekst. "
            "Voeg niets toe en laat niets weg: geen inleiding, geen eigen uitleg, geen vraag achteraf. "
            "Beantwoord geen vragen. Lees telefoonnummers cijfer per cijfer en traag voor. "
            f"<tekst>{text}</tekst>"
        ),
        "tool_choice": "none",
        "output_modalities": ["audio"],
        "max_output_tokens": 1500,
    }
