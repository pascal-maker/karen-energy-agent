"""Browserbase Search quota: one 402 pauses searching, and one question has a limited number of calls."""

import io
from urllib.error import HTTPError

import pytest

from luminus_harness import browserbase_sources as bb


@pytest.fixture
def calls(monkeypatch):
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb_test")
    monkeypatch.setattr(bb, "_search_paused_until", 0.0)
    made = []

    def fake_urlopen(request, timeout=0):
        made.append(request)
        raise HTTPError(request.full_url, 402, "Payment Required", {}, io.BytesIO(b""))

    monkeypatch.setattr(bb, "urlopen", fake_urlopen)
    return made


def test_a_402_pauses_every_further_search(calls):
    assert bb._search("kilowattuur", "all_public") == bb.SEARCH_QUOTA_MESSAGE
    assert len(calls) == 1  # the other site groups of the same scope are not tried
    assert bb._search("kilowattuur", "consumer") == bb.SEARCH_QUOTA_MESSAGE
    assert len(calls) == 1


def test_the_pause_ends(calls, monkeypatch):
    bb._search("kilowattuur", "consumer")
    monkeypatch.setattr(bb, "_search_paused_until", 0.0)
    bb._search("kilowattuur", "consumer")
    assert len(calls) == 2


def test_one_question_has_a_search_budget(monkeypatch):
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb_test")
    monkeypatch.setattr(bb, "_search_paused_until", 0.0)
    made = []
    monkeypatch.setattr(bb, "_search_once", lambda query, key: made.append(query) or [])
    with bb.search_budget(3):
        for _ in range(4):
            bb._search("kilowattuur", "all_public")  # 4 site groups each
    assert len(made) == 3
    bb._search("kilowattuur", "consumer")  # outside a question: no limit
    assert len(made) == 4


def test_the_daily_cap_stops_searching_for_the_day(monkeypatch):
    monkeypatch.setenv("BROWSERBASE_API_KEY", "bb_test")
    monkeypatch.setattr(bb, "_search_paused_until", 0.0)
    monkeypatch.setattr(bb, "SEARCH_DAILY_CAP", 2)
    monkeypatch.setattr(bb, "_search_day", "")
    made = []
    monkeypatch.setattr(bb, "_search_once", lambda query, key: made.append(query) or [])
    for _ in range(3):
        result = bb._search("kilowattuur", "consumer")
    assert len(made) == 2 and result == bb.SEARCH_DAILY_CAP_MESSAGE
    monkeypatch.setattr(bb, "_search_day", "2000-01-01")  # a new day
    bb._search("kilowattuur", "consumer")
    assert len(made) == 3
