"""The native app: in-process bridge (no server/port), self-contained page, single instance."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections import deque
from itertools import count

import pytest

from app.web import server


@pytest.fixture
def bridge(monkeypatch):
    from app.daemon import Bridge

    st = server.State.__new__(server.State)
    st.lock, st.events, st._ids = threading.Lock(), deque(), count(1)
    st.services = st.voice = st._listener = None
    st.enroll_clips, st.extra_hotkeys = [], {}
    st._listener_lock = threading.Lock()
    monkeypatch.setattr(server.State, "restart_background", lambda self, reload_speech: None)
    monkeypatch.setattr(server.State, "restart_scheduler", lambda self: None)
    from app.companion import Companion
    from app.workflow_extended import ExtendedWorkflow

    st.companion = Companion()
    st.workflow = ExtendedWorkflow(st.companion, None)
    monkeypatch.setattr(server, "state", st)
    monkeypatch.setattr(server, "_microphones", lambda: [])
    started = []
    monkeypatch.setattr(server.State, "start_background", lambda self: started.append(1))
    b = Bridge()
    b.started = started
    return b


def test_bridge_runs_routes_in_process(bridge):
    r = bridge.request("POST", "/api/message", {"text": "what time is it"})
    assert r["status"] == 200
    first = json.loads(r["body"].splitlines()[0])
    assert first["type"] == "reply" and first["text"].startswith("It's")
    r = bridge.request("GET", "/api/settings")
    assert r["status"] == 200 and "settings" in json.loads(r["body"])
    assert bridge.started == []  # the web app's start-up hook isn't run: no duplicate services


def test_bridge_reports_errors_as_status(bridge):
    r = bridge.request("PUT", "/api/settings", {"user": {"latitude": "abc"}})
    assert r["status"] == 200 and json.loads(r["body"])["errors"] == {"user.latitude": "must be a number"}
    assert bridge.request("GET", "/api/nope")["status"] == 404


def test_page_is_self_contained():
    from app.daemon import page_html

    html = page_html()
    assert "window.MD_NATIVE = true" in html
    assert "__TOKEN__" not in html and server.TOKEN in html
    assert '<link rel="stylesheet" href="/static/' not in html and '<script src="/static/' not in html
    assert "async function api(" in html and ":root {" in html


def test_single_instance_across_processes():
    from app.single_instance import SingleInstance

    got = threading.Event()
    with SingleInstance("MDPytest") as first:
        assert first.primary
        first.on_show_request(got.set)
        code = ("from app.single_instance import SingleInstance\n"
                "with SingleInstance('MDPytest') as s: print(s.primary, s.signal_running())")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60).stdout.split()
        assert out == ["False", "True"]
        assert got.wait(3)
    with SingleInstance("MDPytest") as again:
        assert again.primary
