"""Real-time voice: Realtime is only Karen's ears and voice; every spoken answer comes from her own route."""

import datetime as dt

import pytest

from luminus_harness import realtime_voice
from tests.test_karen_market_prices import api  # noqa: F401  (api is a fixture)


@pytest.fixture
def realtime_on(monkeypatch):
    monkeypatch.setenv("KAREN_REALTIME", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


def test_the_model_never_answers_by_itself():
    config = realtime_voice.session_config()
    detection = config["audio"]["input"]["turn_detection"]
    assert detection["create_response"] is False and detection["interrupt_response"] is True
    assert config["tool_choice"] == "none" and "tools" not in config
    assert "nooit zelf" in config["instructions"]


def test_read_aloud_carries_only_the_given_text():
    response = realtime_voice.read_aloud("Bel Fluvius op 078 35 35 00.")
    assert "<tekst>Bel Fluvius op 078 35 35 00.</tekst>" in response["instructions"]
    assert "letterlijk" in response["instructions"] and response["tool_choice"] == "none"
    assert response["output_modalities"] == ["audio"]


@pytest.mark.parametrize("answer, spoken", [
    ("Kort antwoord: ja.  \n- **Wat het is:** je betaalt ook voor je piek (kW).",
     "Kort antwoord: ja. Wat het is: je betaalt ook voor je piek (kW)."),
    ("Bel ORES op 078 78 78 00.\nZie https://www.ores.be/aide-et-contact", "Bel ORES op 078 78 78 00."),
    ("Lees de [VREG-regels](https://www.vreg.be) na", "Lees de VREG-regels na."),
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
    assert client.get("/realtime").status_code == 404


def test_a_spoken_emergency_answer_comes_from_the_safety_layer(api, realtime_on):  # noqa: F811
    client, _, hosted = api
    reply = client.post("/api/realtime-answer", json={
        "question": "Merci. S'il y a une panne de courant chez moi, quel numéro dois-je appeler ?",
        "history": [{"question": "Bonjour, j'habite à Namur.", "answer": "Bonjour !"}],
    }).json()
    assert reply["processing"] == "safety-emergency"
    assert "078 78 78 00" in reply["spoken"]
    assert f"<tekst>{reply['spoken']}</tekst>" in reply["speak"]["instructions"]
    hosted.assert_not_called()


def test_the_session_key_is_minted_with_the_listening_only_config(api, realtime_on, monkeypatch):  # noqa: F811
    client, _, _ = api
    seen = {}

    async def fake_secret(language):
        seen["language"] = language
        return {"client_secret": "ek_test", "expires_at": 0, "model": "gpt-realtime", "max_seconds": 300,
                "greeting": realtime_voice.read_aloud("Hoi"), "wait": realtime_voice.read_aloud("Even kijken.")}

    monkeypatch.setattr(realtime_voice, "create_client_secret", fake_secret)
    reply = client.post("/api/realtime-session", json={"language": "fr"})
    assert reply.status_code == 200 and reply.json()["client_secret"] == "ek_test" and seen["language"] == "fr"
    assert client.post("/api/realtime-session", json={"language": "de"}).status_code == 422
