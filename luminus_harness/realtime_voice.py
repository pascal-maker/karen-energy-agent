"""Real-time voice for Karen: OpenAI Realtime is only her ears, a text-to-speech model her voice.

The browser streams the microphone to OpenAI Realtime over WebRTC in a *transcription* session. That
session only turns speech into text and notices when the visitor starts or stops talking; it has no
model that can answer. The browser never gets an OpenAI credential: it sends its WebRTC offer here,
this server opens the call with its own key, counts it, and hangs it up after the time limit.

Each transcribed question goes to Karen's normal answer route (safety layer, routes, sources), and
her answer is read by a text-to-speech model (``gpt-4o-mini-tts``) that says the given text and nothing else. A speech-to-speech model was
tried first: it paraphrased Karen's answers and added its own advice, also on emergency numbers.

Off unless ``KAREN_REALTIME=1``; it needs ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any, Iterator

TRANSCRIBE_MODEL = os.getenv("KAREN_REALTIME_STT_MODEL", "gpt-4o-transcribe")
TTS_MODEL = os.getenv("KAREN_REALTIME_TTS_MODEL", "gpt-4o-mini-tts")
VOICE = os.getenv("KAREN_REALTIME_VOICE", "marin")
MAX_CONVERSATION_SECONDS = int(os.getenv("KAREN_REALTIME_MAX_SECONDS", "300"))
DAILY_SESSIONS = int(os.getenv("KAREN_REALTIME_DAILY_SESSIONS", "100"))
# All visitors together, so the caps hold even when every visitor arrives from one proxy address.
MAX_OPEN_CALLS = int(os.getenv("KAREN_REALTIME_MAX_OPEN_CALLS", "5"))
MAX_ANSWERS_PER_CALL = int(os.getenv("KAREN_REALTIME_MAX_ANSWERS", "30"))
DAILY_SPOKEN_CHARS = int(os.getenv("KAREN_REALTIME_DAILY_SPOKEN_CHARS", "200000"))
MAX_SDP_CHARS = 20_000
CALLS_URL = "https://api.openai.com/v1/realtime/calls"
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

log = logging.getLogger(__name__)

_lock = threading.Lock()
_day: dt.date | None = None
_sessions_today = 0


def enabled() -> bool:
    return os.getenv("KAREN_REALTIME", "").strip() == "1" and bool(os.getenv("OPENAI_API_KEY", "").strip())


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


# --- calls: opened, counted and ended by this server ---

class CallRefused(RuntimeError):
    """OpenAI answered and did not open a call, so nothing runs there."""


# A call stays in `_calls`, and keeps its place, until OpenAI confirmed that it ended.
_calls: dict[str, dict[str, Any]] = {}
_pending = 0
_hangups: set[asyncio.Task] = set()
# Calls we could not end at OpenAI after every retry: kept and logged, never silently forgotten.
unended_calls: list[str] = []
HANGUP_RETRY_SECONDS = (2, 5, 15, 30, 60, 120, 300, 600)


def reserve_call(today: dt.date | None = None) -> str:
    """Take a place and a daily slot for a call that is about to be opened, in one step.

    Returns "" when reserved, else "busy" (too many calls right now) or "day" (today's calls are used up).
    """
    global _pending, _day, _sessions_today
    today = today or dt.date.today()
    with _lock:
        if _day != today:
            _day, _sessions_today = today, 0
        if len(_calls) + _pending >= MAX_OPEN_CALLS:
            return "busy"
        if _sessions_today >= DAILY_SESSIONS:
            return "day"
        _pending += 1
        _sessions_today += 1
        return ""


def release_reservation(refused: bool) -> None:
    """The call was not opened. Only a confirmed refusal gives the daily slot back: after a timeout
    OpenAI may have opened a call anyway, so that one stays counted."""
    global _pending, _sessions_today
    with _lock:
        _pending = max(0, _pending - 1)
        if refused:
            _sessions_today = max(0, _sessions_today - 1)


def register_call(call_id: str, now: float | None = None) -> str:
    """Turn the reservation into an open call; the returned id is what the browser shows to ask and to hear answers."""
    global _pending
    now = time.monotonic() if now is None else now
    session = secrets.token_urlsafe(24)
    with _lock:
        _pending = max(0, _pending - 1)
        _calls[session] = {"call_id": call_id, "ends": now + MAX_CONVERSATION_SECONDS, "answers": 0}
    return session


def call_is_open(session: str, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    with _lock:
        call = _calls.get(session or "")
        return bool(call) and call["ends"] > now


def take_answer(session: str, now: float | None = None) -> bool:
    """One question in an open call; False when the call is over or has asked its share."""
    now = time.monotonic() if now is None else now
    with _lock:
        call = _calls.get(session or "")
        if not call or call["ends"] <= now or call["answers"] >= MAX_ANSWERS_PER_CALL:
            return False
        call["answers"] += 1
        return True


def close_call(session: str) -> str | None:
    """No more questions or speech in this call. It keeps its place until OpenAI confirmed the end."""
    with _lock:
        call = _calls.get(session or "")
        if not call:
            return None
        call["ends"] = 0.0
        return call["call_id"]


def _forget_call(call_id: str) -> None:
    with _lock:
        for session, call in list(_calls.items()):
            if call["call_id"] == call_id:
                del _calls[session]


async def open_call(offer_sdp: str, language: str = "nl") -> tuple[str, str]:
    """Open the call at OpenAI with the server's key; returns (answer SDP, call id)."""
    import httpx

    headers = {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"].strip()}
    files = {"sdp": (None, offer_sdp), "session": (None, json.dumps(session_config(language)))}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(CALLS_URL, headers=headers, files=files)
    if response.status_code >= 400:
        raise CallRefused(f"OpenAI Realtime gaf HTTP {response.status_code}.")
    call_id = response.headers.get("location", "").rstrip("/").rsplit("/", 1)[-1]
    if not call_id:
        raise RuntimeError(f"OpenAI Realtime gaf HTTP {response.status_code} zonder gesprek-id.")
    return response.text, call_id


async def hang_up(call_id: str) -> None:
    """End the call at OpenAI; raises unless OpenAI confirmed (404 means it had already ended)."""
    import httpx

    headers = {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"].strip()}
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"{CALLS_URL}/{call_id}/hangup", headers=headers)
    if response.status_code != 404 and response.status_code >= 300:
        raise RuntimeError(f"OpenAI hangup gaf HTTP {response.status_code}.")


async def end_call(call_id: str, retries: tuple[float, ...] | None = None) -> bool:
    """Hang up until OpenAI confirms, then free the call's place. False when every try failed."""
    for wait in (0, *(HANGUP_RETRY_SECONDS if retries is None else retries)):
        await asyncio.sleep(wait)
        try:
            await hang_up(call_id)
        except Exception as error:
            log.warning("Real-time voice: hangup of %s failed (%s); trying again.", call_id, error)
            continue
        _forget_call(call_id)
        return True
    unended_calls.append(call_id)
    log.error("Real-time voice: call %s could NOT be ended at OpenAI after every retry.", call_id)
    _forget_call(call_id)
    return False


def _in_background(work: Any) -> None:
    task = asyncio.get_running_loop().create_task(work)
    _hangups.add(task)
    task.add_done_callback(_hangups.discard)


def hang_up_later(call_id: str, seconds: float | None = None) -> None:
    """End the call at OpenAI after the time limit, whatever the browser does."""
    async def end() -> None:
        await asyncio.sleep(MAX_CONVERSATION_SECONDS if seconds is None else seconds)
        await end_call(call_id)

    _in_background(end())


async def hang_up_now(session: str) -> bool:
    """The visitor stopped: close the call here and end it at OpenAI. If OpenAI does not confirm
    at once, the retries go on in the background and the call keeps its place meanwhile."""
    call_id = close_call(session)
    if not call_id:
        return False
    try:
        await asyncio.wait_for(hang_up(call_id), timeout=10)
    except Exception as error:
        log.warning("Real-time voice: hangup of %s failed (%s); trying again.", call_id, error)
        _in_background(end_call(call_id, retries=HANGUP_RETRY_SECONDS[1:]))
    else:
        _forget_call(call_id)
    return True


# --- speech: a daily budget, and each text is only made once ---

_spoken_day: dt.date | None = None
_spoken_today = 0
_audio: OrderedDict[str, bytes] = OrderedDict()
AUDIO_CACHE_ITEMS = 60


def take_spoken_chars(count: int, today: dt.date | None = None) -> bool:
    global _spoken_day, _spoken_today
    today = today or dt.date.today()
    with _lock:
        if _spoken_day != today:
            _spoken_day, _spoken_today = today, 0
        if _spoken_today + count > DAILY_SPOKEN_CHARS:
            return False
        _spoken_today += count
        return True


def _audio_key(text: str, language: str) -> str:
    return hashlib.sha256(f"{TTS_MODEL}|{VOICE}|{language}|{text}".encode("utf-8")).hexdigest()


def speech(text: str, language: str = "nl") -> Iterator[bytes] | None:
    """Karen's text as MP3 chunks. A text made before is replayed from memory; None when today's budget is spent."""
    key = _audio_key(text, language)
    with _lock:
        cached = _audio.get(key)
        if cached is not None:
            _audio.move_to_end(key)
    if cached is not None:
        return iter([cached])
    if not take_spoken_chars(len(text)):
        return None

    def made() -> Iterator[bytes]:
        parts: list[bytes] = []
        for chunk in stream_speech(text, language):
            parts.append(chunk)
            yield chunk
        # Only a complete reading is kept; an interrupted one is made again if it is asked again.
        with _lock:
            _audio[key] = b"".join(parts)
            while len(_audio) > AUDIO_CACHE_ITEMS:
                _audio.popitem(last=False)

    return made()


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
