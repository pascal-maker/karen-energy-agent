"""Real-time voice for Karen: OpenAI Realtime is only her ears, a text-to-speech model her voice.

The browser streams the microphone to OpenAI Realtime over WebRTC in a *transcription* session, with
a short-lived key minted here. That session only turns speech into text and notices when the visitor
starts or stops talking; it has no model that can answer. Each transcribed question goes to Karen's
normal answer route (safety layer, routes, sources), and her answer is read by a text-to-speech
model (``gpt-4o-mini-tts``) that says the given text and nothing else. A speech-to-speech model was
tried first: it paraphrased Karen's answers and added its own advice, also on emergency numbers.

Off unless ``KAREN_REALTIME=1``; it needs ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import threading
from typing import Any, Iterator

TRANSCRIBE_MODEL = os.getenv("KAREN_REALTIME_STT_MODEL", "gpt-4o-transcribe")
TTS_MODEL = os.getenv("KAREN_REALTIME_TTS_MODEL", "gpt-4o-mini-tts")
VOICE = os.getenv("KAREN_REALTIME_VOICE", "marin")
# The key is only needed to open the connection; the browser ends a conversation after this long.
KEY_SECONDS = 60
MAX_CONVERSATION_SECONDS = int(os.getenv("KAREN_REALTIME_MAX_SECONDS", "300"))
DAILY_SESSIONS = int(os.getenv("KAREN_REALTIME_DAILY_SESSIONS", "100"))
MAX_SPOKEN_CHARS = 900
LANGUAGES = ("nl", "fr", "en")

# Style only: a text-to-speech model reads its input and does not answer it.
VOICE_STYLE = {
    "nl": "Spreek warm, rustig en duidelijk Belgisch Nederlands. Lees telefoonnummers traag, cijfer per cijfer.",
    "fr": "Parle d'une voix chaleureuse, calme et claire, en français de Belgique. Lis les numéros lentement.",
    "en": "Speak warmly, calmly and clearly. Read phone numbers slowly, digit by digit.",
}
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


def session_config(language: str = "nl") -> dict[str, Any]:
    """A transcription-only session: OpenAI hears the visitor, but nothing there can answer."""
    return {
        "type": "transcription",
        "audio": {
            "input": {
                # No prompt: on silence the transcriber can repeat its prompt as if it was said.
                "transcription": {"model": TRANSCRIBE_MODEL, "language": language},
                "noise_reduction": {"type": "near_field"},
                "turn_detection": {"type": "server_vad", "silence_duration_ms": 700, "threshold": 0.6},
            },
        },
    }


async def create_client_secret(language: str = "nl") -> dict[str, Any]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI()
    secret = await client.realtime.client_secrets.create(
        expires_after={"anchor": "created_at", "seconds": KEY_SECONDS},
        session=session_config(language),  # type: ignore[arg-type]
    )
    return {"client_secret": secret.value, "expires_at": secret.expires_at, "max_seconds": MAX_CONVERSATION_SECONDS}


def stream_speech(text: str, language: str = "nl") -> Iterator[bytes]:
    """Karen's text as MP3, streamed while it is made; the model reads exactly `text`."""
    from openai import OpenAI

    with OpenAI().audio.speech.with_streaming_response.create(
        model=TTS_MODEL, voice=VOICE, input=text, response_format="mp3",
        instructions=VOICE_STYLE.get(language, VOICE_STYLE["nl"]),
    ) as response:
        yield from response.iter_bytes(4096)


_URL = re.compile(r"https?://\S+|www\.\S+")
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def spoken_text(answer: str) -> str:
    """The answer as it should sound: no links, markdown or source lists, at most MAX_SPOKEN_CHARS."""
    text = _MARKDOWN_LINK.sub(r"\1", answer or "")
    text = _URL.sub("", text)
    # A pointer left without its link ("Zie .", "Plus d'infos :") says nothing aloud.
    text = re.sub(r"(?im)\b(?:zie|bekijk|meer info(?:rmatie)?(?: op)?|voir|plus d'infos?|see|more info(?: at)?)\s*[:.]?\s*$", "", text)
    # Bullets written inline (" - **Why:** …", " • Frank: …") become sentences too.
    text = re.sub(r"\s+(?:-|•)\s+", "\n", text)
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
