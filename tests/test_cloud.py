"""The Vercel function in cloud/api/index.py — no network: the Claude client is replaced by a fake."""

import importlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "cloud" / "api"))


class FakeStream:
    def __init__(self, params, stop="end_turn"):
        self.params = params
        self.text_stream = iter(["Hello ", "there."])
        self.stop_reason = stop

    def get_final_message(self):
        return self


def make_fake(calls, stop="end_turn"):
    class Messages:
        @staticmethod
        @contextmanager
        def stream(**params):
            calls.append(params)
            yield FakeStream(params, stop)

    class Beta:
        messages = Messages

    class Client:
        beta = Beta

    return Client()


@pytest.fixture
def cloud(monkeypatch):
    for name in ("NOVA_PUBLIC", "NOVA_ACCESS_CODE", "NOVA_MODEL", "NOVA_DOWNLOAD_URL", "NOVA_GITHUB_REPO",
                 "NOVA_RATE_LIMIT", "NOVA_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    module = importlib.reload(importlib.import_module("index"))
    calls = []
    monkeypatch.setattr(module, "get_client", lambda: make_fake(calls))
    return module, TestClient(module.app), calls


def events(resp):
    return [json.loads(line[6:]) for line in resp.text.split("\n\n") if line.startswith("data: ")]


def test_chat_off_until_access_code_or_public(cloud):
    module, client, _ = cloud
    assert client.get("/api/config").json()["chat_enabled"] is False
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503


def test_access_code_checked(cloud, monkeypatch):
    module, client, calls = cloud
    monkeypatch.setenv("NOVA_ACCESS_CODE", "s3cret")
    body = {"messages": [{"role": "user", "content": "hi"}]}
    assert client.post("/api/chat", json=body).status_code == 401
    assert client.post("/api/chat", json=body, headers={"X-Nova-Access-Code": "nope"}).status_code == 401
    r = client.post("/api/chat", json=body, headers={"X-Nova-Access-Code": "s3cret"})
    assert r.status_code == 200
    assert events(r) == [{"t": "Hello "}, {"t": "there."}, {"done": True, "stop": "end_turn"}]
    assert client.get("/api/config").json()["chat_enabled"] is True


def test_request_shape(cloud, monkeypatch):
    module, client, calls = cloud
    monkeypatch.setenv("NOVA_PUBLIC", "1")
    client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}],
                                   "memories": ["User is a web developer"], "local_time": "Monday 9:00"})
    params = calls[0]
    assert params["model"] == "claude-opus-5"
    assert params["fallbacks"] == "default" and params["betas"] == ["server-side-fallback-2026-07-01"]
    assert params["output_config"] == {"effort": "low"}
    # the fixed persona comes first (cacheable); the per-user details come after it
    assert params["system"][0]["text"] == module.SYSTEM_PROMPT
    assert "web developer" in params["system"][1]["text"] and "Monday 9:00" in params["system"][1]["text"]


def test_invalid_conversations_rejected(cloud, monkeypatch):
    module, client, _ = cloud
    monkeypatch.setenv("NOVA_PUBLIC", "1")
    assert client.post("/api/chat", json={"messages": []}).status_code == 422
    assert client.post("/api/chat", json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422
    assert client.post("/api/chat", json={"messages": [{"role": "user", "content": "x" * 5000}]}).status_code == 422
    r = client.post("/api/chat", json={"messages": [{"role": "user", "content": "a"},
                                                    {"role": "assistant", "content": "b"}]})
    assert r.status_code == 422


def test_refusal_reported(cloud, monkeypatch):
    module, client, _ = cloud
    monkeypatch.setenv("NOVA_PUBLIC", "1")
    monkeypatch.setattr(module, "get_client", lambda: make_fake([], stop="refusal"))
    got = events(client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}))
    assert any("error" in e for e in got)


def test_rate_limit(cloud, monkeypatch):
    module, client, _ = cloud
    monkeypatch.setenv("NOVA_PUBLIC", "1")
    monkeypatch.setenv("NOVA_RATE_LIMIT", "2")
    body = {"messages": [{"role": "user", "content": "hi"}]}
    codes = [client.post("/api/chat", json=body).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_download_url(cloud, monkeypatch):
    module, client, _ = cloud
    monkeypatch.setenv("NOVA_GITHUB_REPO", "someone/nova")
    assert client.get("/api/config").json()["download_url"] == "https://github.com/someone/nova/archive/refs/heads/main.zip"
    monkeypatch.setenv("NOVA_DOWNLOAD_URL", "https://example.com/nova.zip")
    assert client.get("/api/config").json()["download_url"] == "https://example.com/nova.zip"
