"""Regression tests for saving from the Settings page (bad input used to reject the whole
save, and blank numbers were stored as null and later crashed the app)."""

from __future__ import annotations

import threading
from collections import deque
from itertools import count

import pytest
import yaml

from app import config as config_mod
from app.web import server


@pytest.mark.parametrize("kind, value, field, expected", [
    (float, "24.86", "user.latitude", 24.86),
    (float, "24,86", "user.latitude", 24.86),          # comma decimal separator
    (float, "24.8607° N", "user.latitude", 24.8607),   # copied from a map
    (float, "33.9 S", "user.latitude", -33.9),
    (int, "400.0", "llm.max_tokens", 400),
    (int, " 25 ", "focus.default_minutes", 25),
    (list, "hey zoya, zoya", "voice.wake_phrases", ["hey zoya", "zoya"]),
    (list, "", "voice.wake_phrases", []),
    (bool, True, "calls.enabled", True),
    (str, "", "user.city", ""),
])
def test_coerce_accepts_real_input(kind, value, field, expected):
    assert server._coerce(kind, value, field) == expected


@pytest.mark.parametrize("kind, value, field", [
    (float, "", "llm.temperature"), (int, "", "llm.max_tokens"), (str, "", "assistant.name"),
    (str, "", "scheduler.checkin_time"),
])
def test_blank_means_default(kind, value, field):
    assert server._coerce(kind, value, field) is server.KEEP_DEFAULT


@pytest.mark.parametrize("kind, value, field", [
    (float, "abc", "user.latitude"), (float, "123", "user.latitude"), (int, "99999", "llm.max_tokens"),
    (str, "fast", "expert.effort"), (str, "25:99", "scheduler.checkin_time"),
])
def test_invalid_values_are_reported(kind, value, field):
    with pytest.raises(ValueError):
        server._coerce(kind, value, field)


def test_null_overrides_do_not_wipe_defaults(tmp_path, monkeypatch):
    settings = tmp_path / "settings.yaml"
    settings.write_text("llm:\n  temperature: null\n  max_tokens: null\nexpert:\n  weekly_budget_usd: null\n"
                        "focus:\n  default_minutes: null\n", encoding="utf-8")
    monkeypatch.setattr(config_mod, "SETTINGS_FILE", settings)
    cfg = config_mod.load_config()
    assert cfg.llm.temperature == 0.7 and cfg.llm.max_tokens == 400
    assert cfg.expert.weekly_budget_usd == 5.0 and cfg.focus.default_minutes == 25


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    settings = tmp_path / "settings.yaml"
    monkeypatch.setattr(config_mod, "SETTINGS_FILE", settings)
    cfg = config_mod.get_config()
    saved = dict(cfg.__dict__)

    st = server.State.__new__(server.State)
    st.lock, st.events, st._ids = threading.Lock(), deque(), count(1)
    st.services = st.voice = st._listener = None
    st.enroll_clips = []

    class NoScheduler:
        def shutdown(self):
            pass
    st.scheduler = NoScheduler()
    monkeypatch.setattr(server, "state", st)
    monkeypatch.setattr(server.State, "start_background", lambda self: None)
    monkeypatch.setattr(server.State, "restart_background", lambda self, reload_speech: None)
    monkeypatch.setattr(server.State, "restart_scheduler", lambda self: None)
    monkeypatch.setattr(server, "_microphones", lambda: [])
    monkeypatch.setattr(server.get_llm(), "available_models", lambda: ["qwen2.5:1.5b"])
    with TestClient(server.app) as c:
        c.headers["X-Companion-Token"] = server.TOKEN
        c.settings_file = settings
        yield c
    cfg.__dict__.update(saved)


def test_one_bad_field_does_not_block_the_rest(client):
    r = client.put("/api/settings", json={"user": {"name": "Muheb", "latitude": "24,86", "longitude": "abc"},
                                          "assistant": {"name": "Zoya"}})
    assert r.status_code == 200
    data = r.json()
    assert data["errors"] == {"user.longitude": "must be a number"}
    assert data["settings"]["user"]["name"] == "Muheb" and data["settings"]["user"]["latitude"] == 24.86
    assert data["settings"]["assistant"]["name"] == "Zoya"


def test_blank_numbers_fall_back_and_are_not_stored_as_null(client):
    r = client.put("/api/settings", json={"llm": {"max_tokens": "", "temperature": ""}, "expert": {"weekly_budget_usd": ""}})
    assert r.status_code == 200 and r.json()["errors"] == {}
    stored = yaml.safe_load(client.settings_file.read_text(encoding="utf-8")) or {}
    assert "max_tokens" not in stored.get("llm", {}) and "weekly_budget_usd" not in stored.get("expert", {})
    s = r.json()["settings"]
    assert s["llm"]["max_tokens"] == 400 and s["expert"]["weekly_budget_usd"] == 5.0
    assert client.get("/api/expert").status_code == 200          # used to be a 500 after a blank budget


def test_whole_form_round_trip(client):
    form = client.get("/api/settings").json()["settings"]
    body = {section: {k: ("" if v is None else ", ".join(v) if isinstance(v, list) else v) for k, v in values.items()}
            for section, values in form.items()}
    r = client.put("/api/settings", json=body)
    assert r.status_code == 200 and r.json()["errors"] == {}
    assert r.json()["settings"] == form                         # saving unchanged settings changes nothing
