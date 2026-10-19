"""Dutch speech-to-text for browsers without built-in speech recognition.

Chrome and the Google app on iPhone, Firefox and in-app browsers have no Web
Speech API. The Karen component then records a short clip and posts it here;
the clip is transcribed once and never stored.
"""

import os

MAX_AUDIO_BYTES = 2 * 1024 * 1024  # ~30 s of compressed speech
AUDIO_TYPES = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/aac": "m4a",
    "audio/x-m4a": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
}
PROMPT = "Nederlandse Belgische energievragen. Behoud postcodes en kWh-getallen exact."


def audio_extension(content_type: str) -> str | None:
    """File extension for an accepted recording type ('audio/webm;codecs=opus' -> 'webm')."""
    return AUDIO_TYPES.get(content_type.split(";", 1)[0].strip().lower())


def transcribe_dutch(audio: bytes, content_type: str) -> str:
    """Transcribe one short Dutch recording with OpenAI; returns the text."""
    extension = audio_extension(content_type)
    if not extension:
        raise ValueError("Unsupported audio type")
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise ValueError("Recording must contain 1 byte to 2 MB of audio")

    from openai import OpenAI

    result = OpenAI(timeout=30).audio.transcriptions.create(
        model=os.getenv("KAREN_STT_MODEL", "gpt-4o-mini-transcribe"),
        file=(f"question.{extension}", audio, content_type.split(";", 1)[0]),
        language="nl",
        prompt=PROMPT,
    )
    return (result.text or "").strip()
