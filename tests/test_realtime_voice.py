"""Real-time voice: OpenAI only transcribes; every spoken word is text Karen's own route wrote and signed."""

import datetime as dt

import pytest

from luminus_harness import realtime_voice
from tests.test_karen_market_prices import api  # noqa: F401  (api is a fixture)


SDP = "v=0 an offer from the browser" + " x" * 20


@pytest.fixture
def realtime_on(monkeypatch):
    """Voice on, with fresh counters and no call to OpenAI: every opened call is recorded in `calls`."""
    import energy_voice_app

    monkeypatch.setenv("KAREN_REALTIME", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(realtime_voice, "_calls", {})
    monkeypatch.setattr(realtime_voice, "_audio", realtime_voice.OrderedDict())
    monkeypatch.setattr(realtime_voice, "_sessions_today", 0)
    monkeypatch.setattr(realtime_voice, "_spoken_today", 0)
    monkeypatch.setattr(energy_voice_app, "_realtime_rate_limiter",
                        energy_voice_app.SlidingWindowRateLimiter(requests=3, window_seconds=300, max_clients=10))
    calls = {"opened": [], "hung_up": [], "later": []}

    async def open_call(sdp, language):
        calls["opened"].append((sdp, language))
        return "v=0 answer", f"rtc_{len(calls['opened'])}"

    async def hang_up(call_id):
        calls["hung_up"].append(call_id)

    monkeypatch.setattr(realtime_voice, "open_call", open_call)
    monkeypatch.setattr(realtime_voice, "hang_up", hang_up)
    monkeypatch.setattr(realtime_voice, "hang_up_later", lambda call_id, seconds=None: calls["later"].append(call_id))
    return calls


def start_call(client, language="nl"):
    reply = client.post("/api/realtime-call", json={"sdp": SDP, "language": language})
    assert reply.status_code == 200, reply.text
    return reply.json()


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
    assert client.post("/api/realtime-call", json={"sdp": SDP}).status_code == 404
    assert client.post("/api/realtime-hangup").status_code == 404
    assert client.post("/api/realtime-answer", json={"question": "Ik ruik gas"}).status_code == 404
    assert client.get("/api/realtime-speech", params={"token": "x" * 50}).status_code == 404
    assert client.get("/realtime").status_code == 404


def test_a_spoken_emergency_answer_is_the_safety_layer_text(api, realtime_on, monkeypatch):  # noqa: F811
    client, _, hosted = api
    call = start_call(client, "fr")
    reply = client.post("/api/realtime-answer", headers={"X-Karen-Voice-Session": call["session"]}, json={
        "question": "Merci. S'il y a une panne de courant chez moi, quel numéro dois-je appeler ?",
        "history": [{"question": "Bonjour, j'habite à Namur.", "answer": "Bonjour !"}],
    }).json()
    assert reply["processing"] == "safety-emergency" and "078 78 78 00" in reply["spoken"]
    hosted.assert_not_called()

    read = []

    def fake_speech(text, language):
        read.append((text, language))
        yield b"ID3fake-mp3"

    monkeypatch.setattr(realtime_voice, "stream_speech", fake_speech)
    params = {"token": reply["speech_token"], "language": "fr", "session": call["session"]}
    audio = client.get("/api/realtime-speech", params=params)
    assert audio.status_code == 200 and audio.headers["content-type"] == "audio/mpeg"
    assert audio.content == b"ID3fake-mp3"
    assert read == [(reply["spoken"], "fr")]  # exactly the signed text, nothing else


def test_only_signed_text_can_be_spoken(api, realtime_on):  # noqa: F811
    client, _, _ = api
    call = start_call(client)
    assert client.get("/api/realtime-speech", params={"token": "A" * 60, "session": call["session"]}).status_code == 404


def test_the_server_opens_the_call_and_the_browser_gets_no_openai_key(api, realtime_on):  # noqa: F811
    client, _, _ = api
    body = start_call(client, "fr")
    assert realtime_on["opened"] == [(SDP, "fr")] and body["sdp"] == "v=0 answer"
    assert realtime_on["later"] == ["rtc_1"]  # the server will hang this call up after the time limit
    assert body["greeting_token"] and body["wait_token"] and body["session"]
    assert "client_secret" not in body and "sk-test" not in str(body) and "rtc_1" not in str(body)
    assert client.post("/api/realtime-call", json={"sdp": SDP, "language": "de"}).status_code == 422


def test_a_fourth_conversation_in_five_minutes_is_refused(api, realtime_on):  # noqa: F811
    client, _, _ = api
    codes = [client.post("/api/realtime-call", json={"sdp": SDP}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert len(realtime_on["opened"]) == 3


def test_the_daily_cap_counts_calls_that_were_really_opened(api, realtime_on, monkeypatch):  # noqa: F811
    client, _, _ = api
    monkeypatch.setattr(realtime_voice, "DAILY_SESSIONS", 1)

    async def refused(sdp, language):
        raise RuntimeError("OpenAI said no")

    opened = realtime_voice.open_call
    monkeypatch.setattr(realtime_voice, "open_call", refused)
    assert client.post("/api/realtime-call", json={"sdp": SDP}).status_code == 503  # not counted
    monkeypatch.setattr(realtime_voice, "open_call", opened)
    assert client.post("/api/realtime-call", json={"sdp": SDP}).status_code == 200
    assert client.post("/api/realtime-call", json={"sdp": SDP}).status_code == 503
    assert len(realtime_on["opened"]) == 1


def test_no_more_calls_at_once_than_the_limit(api, realtime_on, monkeypatch):  # noqa: F811
    client, _, _ = api
    monkeypatch.setattr(realtime_voice, "MAX_OPEN_CALLS", 1)
    call = start_call(client)
    assert client.post("/api/realtime-call", json={"sdp": SDP}).status_code == 503
    ended = client.post("/api/realtime-hangup", headers={"X-Karen-Voice-Session": call["session"]})
    assert ended.json() == {"ended": True} and realtime_on["hung_up"] == ["rtc_1"]
    start_call(client)  # the place is free again


def test_answers_and_speech_need_an_open_call(api, realtime_on, monkeypatch):  # noqa: F811
    import energy_voice_app

    client, _, _ = api
    monkeypatch.setattr(realtime_voice, "stream_speech", lambda text, language: iter([b"ID3fake-mp3"]))
    token = energy_voice_app._sign_public_speech("Bel 0800 65 0 65.")
    question = {"question": "Ik ruik gas"}
    assert client.post("/api/realtime-answer", json=question).status_code == 403
    assert client.post("/api/realtime-answer", json=question, headers={"X-Karen-Voice-Session": "guess"}).status_code == 403
    assert client.get("/api/realtime-speech", params={"token": token}).status_code == 403

    call = start_call(client)
    headers = {"X-Karen-Voice-Session": call["session"]}
    assert client.post("/api/realtime-answer", json=question, headers=headers).status_code == 200
    assert client.get("/api/realtime-speech", params={"token": token, "session": call["session"]}).status_code == 200
    client.post("/api/realtime-hangup", headers=headers)
    assert client.post("/api/realtime-answer", json=question, headers=headers).status_code == 403
    assert client.get("/api/realtime-speech", params={"token": token, "session": call["session"]}).status_code == 403


def test_a_call_is_over_after_the_time_limit_and_has_a_share_of_questions(monkeypatch):
    monkeypatch.setattr(realtime_voice, "_calls", {})
    monkeypatch.setattr(realtime_voice, "MAX_ANSWERS_PER_CALL", 2)
    session = realtime_voice.register_call("rtc_1", now=1000.0)
    assert [realtime_voice.take_answer(session, now=1001.0) for _ in range(3)] == [True, True, False]
    late = 1000.0 + realtime_voice.MAX_CONVERSATION_SECONDS + 1
    assert realtime_voice.call_is_open(session, now=1001.0) and not realtime_voice.call_is_open(session, now=late)
    assert realtime_voice.room_for_a_call(now=late) and session not in realtime_voice._calls


def test_the_server_hangs_the_call_up_after_the_time_limit(monkeypatch):
    import asyncio

    hung_up = []

    async def hang_up(call_id):
        hung_up.append(call_id)

    async def run():
        realtime_voice.hang_up_later("rtc_9", seconds=0.01)
        await asyncio.sleep(0.05)

    monkeypatch.setattr(realtime_voice, "hang_up", hang_up)
    asyncio.run(run())
    assert hung_up == ["rtc_9"]


def test_a_replayed_text_is_not_made_again_and_speech_has_a_daily_budget(api, realtime_on, monkeypatch):  # noqa: F811
    import energy_voice_app

    client, _, _ = api
    made = []

    def fake_speech(text, language):
        made.append(text)
        yield b"ID3"
        yield b"fake-mp3"

    monkeypatch.setattr(realtime_voice, "stream_speech", fake_speech)
    monkeypatch.setattr(realtime_voice, "DAILY_SPOKEN_CHARS", 30)
    call = start_call(client)
    first = {"token": energy_voice_app._sign_public_speech("Bel 0800 65 0 65."), "session": call["session"]}
    replies = [client.get("/api/realtime-speech", params=first) for _ in range(3)]
    assert [r.status_code for r in replies] == [200, 200, 200] and {r.content for r in replies} == {b"ID3fake-mp3"}
    assert made == ["Bel 0800 65 0 65."]  # made once, replayed twice

    second = {"token": energy_voice_app._sign_public_speech("Een heel ander antwoord van Karen."), "session": call["session"]}
    assert client.get("/api/realtime-speech", params=second).status_code == 503  # over today's budget
    assert len(made) == 1


def test_a_signed_text_can_no_longer_be_spoken_after_five_minutes(api, realtime_on, monkeypatch):  # noqa: F811
    import energy_voice_app

    client, _, _ = api
    monkeypatch.setattr(realtime_voice, "stream_speech", lambda text, language: iter([b"ID3fake-mp3"]))
    call = start_call(client)
    params = {"token": energy_voice_app._sign_public_speech("Bel 0800 65 0 65."), "session": call["session"]}
    assert client.get("/api/realtime-speech", params=params).status_code == 200
    now = energy_voice_app.time.time()
    monkeypatch.setattr(energy_voice_app.time, "time", lambda: now + 301)
    assert client.get("/api/realtime-speech", params=params).status_code == 404
