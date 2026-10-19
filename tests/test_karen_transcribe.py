"""/api/transcribe: speech fallback for browsers without built-in recognition.
OpenAI is never called; transcribe_dutch is replaced by a fake."""

from fastapi.testclient import TestClient

from luminus_harness import transcribe


def _client(monkeypatch, transcript="Wat kost stroom vandaag?"):
    import energy_voice_app

    calls = []

    def fake_transcribe(audio, content_type, language="nl"):
        calls.append((len(audio), content_type) if language == "nl" else (len(audio), content_type, language))
        return transcript

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(energy_voice_app, "transcribe_dutch", fake_transcribe)
    # Fresh limiters, so these requests don't use up the shared budget of other API tests.
    monkeypatch.setattr(energy_voice_app, "_api_rate_limiter",
                        energy_voice_app.SlidingWindowRateLimiter(requests=20, window_seconds=60, max_clients=10))
    monkeypatch.setattr(energy_voice_app, "_transcribe_rate_limiter",
                        energy_voice_app.SlidingWindowRateLimiter(requests=6, window_seconds=60, max_clients=10))
    return TestClient(energy_voice_app.api_app), calls


def test_recording_returns_text_for_review(monkeypatch) -> None:
    client, calls = _client(monkeypatch)
    response = client.post("/api/transcribe", files={"file": ("q.webm", b"\x1aE\xdf\xa3audio", "audio/webm;codecs=opus")})
    assert response.status_code == 200
    assert response.json() == {"text": "Wat kost stroom vandaag?"}
    assert calls == [(9, "audio/webm;codecs=opus")]


def test_recording_language_is_passed_on(monkeypatch) -> None:
    client, calls = _client(monkeypatch)
    response = client.post(
        "/api/transcribe", data={"language": "fr"}, files={"file": ("q.webm", b"audio", "audio/webm")}
    )
    assert response.status_code == 200
    assert calls == [(5, "audio/webm", "fr")]


def test_prompt_echo_is_stripped_in_each_language() -> None:
    for prompt in transcribe.PROMPTS.values():
        assert transcribe.strip_prompt_echo(prompt, prompt) == ""


def test_safari_mp4_recordings_are_accepted(monkeypatch) -> None:
    client, calls = _client(monkeypatch)
    assert client.post("/api/transcribe", files={"file": ("q.mp4", b"....ftypM4A", "audio/mp4")}).status_code == 200
    assert calls[0][1] == "audio/mp4"


def test_rejects_non_audio_oversized_and_empty_transcripts(monkeypatch) -> None:
    client, calls = _client(monkeypatch)
    assert client.post("/api/transcribe", files={"file": ("x.pdf", b"%PDF-1.7", "application/pdf")}).status_code == 415
    big = b"0" * (transcribe.MAX_AUDIO_BYTES + 1)
    assert client.post("/api/transcribe", files={"file": ("q.webm", big, "audio/webm")}).status_code == 413
    assert calls == []

    silent, _ = _client(monkeypatch, transcript="")
    assert silent.post("/api/transcribe", files={"file": ("q.webm", b"audio", "audio/webm")}).status_code == 422


def test_unavailable_without_openai_key(monkeypatch) -> None:
    client, calls = _client(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY")
    response = client.post("/api/transcribe", files={"file": ("q.webm", b"audio", "audio/webm")})
    assert response.status_code == 503 and calls == []


def test_rate_limited_per_client(monkeypatch) -> None:
    client, _ = _client(monkeypatch)
    statuses = [client.post("/api/transcribe", files={"file": ("q.webm", b"audio", "audio/webm")}).status_code
                for _ in range(7)]
    assert statuses[:6] == [200] * 6 and statuses[6] == 429


def test_audio_extension_ignores_codec_parameters() -> None:
    assert transcribe.audio_extension("audio/webm;codecs=opus") == "webm"
    assert transcribe.audio_extension("Audio/MP4") == "mp4"
    assert transcribe.audio_extension("video/webm") is None


def test_prompt_echo_from_a_silent_clip_is_dropped() -> None:
    assert transcribe.strip_prompt_echo(transcribe.PROMPT) == ""
    assert transcribe.strip_prompt_echo("nederlandse belgische energievragen") == ""
    assert transcribe.strip_prompt_echo(f"{transcribe.PROMPT} Wat kost stroom vandaag?") == "Wat kost stroom vandaag?"
    assert transcribe.strip_prompt_echo(" Hoeveel kWh verbruik ik? ") == "Hoeveel kWh verbruik ik?"


def test_silent_clip_reports_no_question(monkeypatch) -> None:
    client, _calls = _client(monkeypatch, transcript="")
    response = client.post("/api/transcribe", files={"file": ("q.webm", b"\x1aE\xdf\xa3audio", "audio/webm")})
    assert response.status_code == 422
