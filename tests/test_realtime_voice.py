"""Real-time voice: OpenAI only transcribes; every spoken word is text Karen's own route wrote and signed."""

import datetime as dt

import pytest

from luminus_harness import realtime_voice
from tests.test_karen_market_prices import api  # noqa: F401  (api is a fixture)


@pytest.fixture
def realtime_on(monkeypatch):
    monkeypatch.setenv("KAREN_REALTIME", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


def test_the_session_only_transcribes():
    config = realtime_voice.session_config("fr")
    assert config["type"] == "transcription"
    assert "instructions" not in config and "tools" not in config and "output" not in config["audio"]
    transcription = config["audio"]["input"]["transcription"]
    assert transcription["language"] == "fr" and "prompt" not in transcription


@pytest.mark.parametrize("answer, spoken", [
    ("Kort antwoord: ja.  \n- **Wat het is:** je betaalt ook voor je piek (kW).",
     "Kort antwoord: ja. Wat het is: je betaalt ook voor je piek (kW)."),
    ("Bel ORES op 078 78 78 00.\nZie https://www.ores.be/aide-et-contact", "Bel ORES op 078 78 78 00."),
    ("Lees de [VREG-regels](https://www.vreg.be) na", "Lees de VREG-regels na."),
    ("Short answer. - **Why:** a reason. - **Next step:** do this.", "Short answer. Why: a reason. Next step: do this."),
])
def test_spoken_text_drops_links_and_markdown(answer, spoken):
    assert realtime_voice.spoken_text(answer) == spoken


def test_spoken_text_is_cut_at_a_sentence():
    spoken = realtime_voice.spoken_text("Dit is een zin over energie. " * 60)
    assert len(spoken) <= realtime_voice.MAX_SPOKEN_CHARS and spoken.endswith(".")


def test_daily_cap(monkeypatch):
    monkeypatch.setattr(realtime_voice, "DAILY_SESSIONS", 2)
    day = dt.date(2026, 11, 9)
    assert [realtime_voice.take_daily_slot(day) for _ in range(3)] == [True, True, False]
    assert realtime_voice.take_daily_slot(day + dt.timedelta(days=1)) is True


def test_off_by_default(api, monkeypatch):  # noqa: F811
    client, _, _ = api
    monkeypatch.delenv("KAREN_REALTIME", raising=False)
    assert client.post("/api/realtime-session", json={}).status_code == 404
    assert client.post("/api/realtime-answer", json={"question": "Ik ruik gas"}).status_code == 404
    assert client.get("/api/realtime-speech", params={"token": "x" * 50}).status_code == 404
    assert client.get("/realtime").status_code == 404


def test_a_spoken_emergency_answer_is_the_safety_layer_text(api, realtime_on, monkeypatch):  # noqa: F811
    client, _, hosted = api
    reply = client.post("/api/realtime-answer", json={
        "question": "Merci. S'il y a une panne de courant chez moi, quel numéro dois-je appeler ?",
        "history": [{"question": "Bonjour, j'habite à Namur.", "answer": "Bonjour !"}],
    }).json()
    assert reply["processing"] == "safety-emergency" and "078 78 78 00" in reply["spoken"]
    hosted.assert_not_called()

    read = {}

    def fake_speech(text, language):
        read["text"], read["language"] = text, language
        yield b"ID3fake-mp3"

    monkeypatch.setattr(realtime_voice, "stream_speech", fake_speech)
    audio = client.get("/api/realtime-speech", params={"token": reply["speech_token"], "language": "fr"})
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg"
    assert audio.content == b"ID3fake-mp3"
    assert read == {"text": reply["spoken"], "language": "fr"}  # exactly the signed text, nothing else


def test_only_signed_text_can_be_spoken(api, realtime_on):  # noqa: F811
    client, _, _ = api
    assert client.get("/api/realtime-speech", params={"token": "A" * 60}).status_code == 404


def test_the_session_key_comes_with_spoken_greeting_and_wait_line(api, realtime_on, monkeypatch):  # noqa: F811
    client, _, _ = api
    seen = {}

    async def fake_secret(language):
        seen["language"] = language
        return {"client_secret": "ek_test", "expires_at": 0, "max_seconds": 300}

    monkeypatch.setattr(realtime_voice, "create_client_secret", fake_secret)
    reply = client.post("/api/realtime-session", json={"language": "fr"})
    body = reply.json()
    assert reply.status_code == 200 and body["client_secret"] == "ek_test" and seen["language"] == "fr"
    assert body["greeting_token"] and body["wait_token"]
    assert client.post("/api/realtime-session", json={"language": "de"}).status_code == 422


def test_a_fourth_conversation_in_five_minutes_is_refused(api, realtime_on, monkeypatch):  # noqa: F811
    import energy_voice_app

    client, _, _ = api

    async def fake_secret(language):
        return {"client_secret": "ek_test", "expires_at": 0, "max_seconds": 300}

    monkeypatch.setattr(realtime_voice, "create_client_secret", fake_secret)
    monkeypatch.setattr(energy_voice_app, "_realtime_rate_limiter",
                        energy_voice_app.SlidingWindowRateLimiter(requests=3, window_seconds=300, max_clients=10))
    codes = [client.post("/api/realtime-session", json={"language": "nl"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_a_signed_text_can_no_longer_be_spoken_after_five_minutes(api, realtime_on, monkeypatch):  # noqa: F811
    import energy_voice_app

    client, _, _ = api
    monkeypatch.setattr(realtime_voice, "stream_speech", lambda text, language: iter([b"ID3fake-mp3"]))
    token = energy_voice_app._sign_public_speech("Bel 0800 65 0 65.")
    assert client.get("/api/realtime-speech", params={"token": token}).status_code == 200
    now = energy_voice_app.time.time()
    monkeypatch.setattr(energy_voice_app.time, "time", lambda: now + 301)
    assert client.get("/api/realtime-speech", params={"token": token}).status_code == 404
