"""Tests for wake word, accounts, notifications, translation helpers, WhatsApp flow and the
web question/answer protocol. No microphone, Ollama, WhatsApp or browser needed."""

from __future__ import annotations

import sqlite3

import pytest

from app.accessibility import translate
from app.automation import accounts, whatsapp
from app.command_parser import parse
from app.config import get_config
from app.interaction import match_option
from app.mentor import notification_monitor as nm
from app.voice.wake import find_wake

# ---- wake word ---------------------------------------------------------------------

COMPANION = ["hey companion", "companion"]


@pytest.mark.parametrize("said, rest", [
    ("Hey companion, what time is it?", "what time is it"),
    ("Hey Compaion open notepad", "open notepad"),
    ("Hey Kampanion, remind me in 5 minutes to drink water", "remind me in 5 minutes to drink water"),
    ("OK companion what is the weather", "what is the weather"),
    ("Companion.", ""),
    ("companion kitne baje hain", "kitne baje hain"),
])
def test_wake_detected(said, rest):
    hit = find_wake(said, COMPANION)
    assert hit is not None and hit[1] == rest


@pytest.mark.parametrize("said", [
    "I was talking to my companion yesterday about the weather and the news today",
    "compassion is important", "Company meeting today", "Champion league tonight", "Hey come here", "Hello how are you",
])
def test_wake_not_triggered(said):
    assert find_wake(said, COMPANION) is None


def test_custom_short_wake_name():
    names = ["hey dost", "dost"]
    for said in ["Dost, namaz ka time batao", "Hey Dhost what time is it", "Hey dost"]:
        assert find_wake(said, names), said
    for said in ["Most of the time", "Lost my keys", "Hey most people", "Just do it"]:
        assert find_wake(said, names) is None, said


# ---- answers to questions ------------------------------------------------------------

def test_match_option():
    opts = ["Muhammad", "Muheb", "Ruhani Nasheeds", "NexTube"]
    assert match_option("next tube", opts) == "NexTube"
    assert match_option("the second one", opts) == "Muheb"
    assert match_option("ruhani", opts) == "Ruhani Nasheeds"
    assert match_option("pehla", opts) == "Muhammad"
    assert match_option("banana", opts) is None
    assert match_option("cancel", opts) is None


# ---- accounts -------------------------------------------------------------------------

def test_split_target():
    assert accounts.split_target("youtube on nextube account") == ("youtube", "nextube")
    assert accounts.split_target("nextube wala youtube") == ("youtube", "nextube")
    assert accounts.split_target("instagram with viral") == ("instagram", "viral")
    assert accounts.split_target("gmail") == ("gmail", None)
    assert accounts.platform_key("YouTube") == "youtube"
    assert accounts.platform_key("notepad") is None


@pytest.fixture
def workflow(monkeypatch):
    from app.companion import Companion
    from app.workflow_extended import ExtendedWorkflow

    profiles = [accounts.Account("Muheb", profile="Profile 1"), accounts.Account("NexTube", profile="Profile 5")]
    monkeypatch.setattr(accounts, "browser_profiles", lambda browser=None: profiles)
    opened = []
    monkeypatch.setattr(accounts, "open_in_profile", lambda url, acc: opened.append((url, acc.profile)))
    cfg = get_config()
    monkeypatch.setattr(cfg, "accounts", {})
    wf = ExtendedWorkflow(Companion(), None)
    wf.opened = opened
    return wf


def test_open_asks_which_account(workflow):
    asked = []
    workflow.ask = lambda q, o=None: (asked.append((q, o)), "next tube")[1]
    _, reply = workflow.run_text("open youtube")
    assert asked and asked[0][1] == ["Muheb", "NexTube"]
    assert workflow.opened == [("https://www.youtube.com/?authuser=0", "Profile 5")]
    assert "NexTube" in reply


def test_open_with_named_account_skips_question(workflow):
    workflow.ask = lambda q, o=None: pytest.fail("should not ask")
    workflow.run_text("open gmail on muheb account")
    assert workflow.opened == [("https://mail.google.com/mail/u/0/", "Profile 1")]


def test_open_cancelled(workflow):
    workflow.ask = lambda q, o=None: None
    _, reply = workflow.run_text("open instagram")
    assert workflow.opened == [] and "didn't open" in reply


def test_configured_accounts_and_google_index(workflow, monkeypatch):
    monkeypatch.setattr(get_config(), "accounts", {"gmail": [
        {"label": "Work", "profile": "Profile 1", "google_index": 1}, {"label": "Home", "profile": "Default"}]})
    workflow.ask = lambda q, o=None: "work"
    workflow.run_text("open gmail")
    assert workflow.opened == [("https://mail.google.com/mail/u/1/", "Profile 1")]


# ---- notifications --------------------------------------------------------------------

def test_parse_toast_and_names():
    payload = (b'<toast><visual><binding template="ToastGeneric"><text>Mama Ji</text><text>Kab aa rahe ho?</text>'
               b'<text placement="attribution">via web.whatsapp.com</text></binding></visual></toast>')
    assert nm.parse_toast(payload) == ("Mama Ji", "Kab aa rahe ho?", "via web.whatsapp.com")
    assert nm.app_name("Chrome", "via mail.google.com") == "Gmail"
    assert nm.app_name("5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App") == "WhatsApp"


@pytest.mark.parametrize("title, parsed", [
    ("(3) WhatsApp", ("WhatsApp", 3, "WhatsApp")),
    ("Inbox (12) - me@gmail.com - Gmail", ("Gmail", 12, "Inbox - me@gmail.com - Gmail")),
    ("(2) TikTok - Make Your Day", ("TikTok", 2, "TikTok - Make Your Day")),
    ("YouTube", ("YouTube", 0, "YouTube")),
    ("Notepad", None),
])
def test_parse_title(title, parsed):
    assert nm.parse_title(title) == parsed


def test_title_source_alerts_only_on_increase():
    src = nm.TitleSource()
    titles = ["Inbox (4) - me@gmail.com - Gmail", "(1) WhatsApp"]
    src._titles = lambda: titles
    assert src.poll() == []                      # first scan = baseline
    titles[:] = ["Inbox (6) - me@gmail.com - Gmail", "(1) WhatsApp"]
    alerts = src.poll()
    assert [(a.app, a.body) for a in alerts] == [("Gmail", "2 new (6 unread) for me@gmail.com")]
    titles[:] = ["Inbox (3) - me@gmail.com - Gmail", "(1) WhatsApp"]
    assert src.poll() == []                      # read some mail: no alert


def test_windows_toast_source(tmp_path):
    db = tmp_path / "wpn.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE NotificationHandler (RecordId INTEGER PRIMARY KEY, PrimaryId TEXT);
        CREATE TABLE Notification ([Order] INTEGER, Id INTEGER, HandlerId INTEGER, Type TEXT, Payload BLOB);
        INSERT INTO NotificationHandler VALUES (1, '5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App'), (2, 'Python.exe');
        INSERT INTO Notification VALUES (1, 1, 1, 'toast', '<toast><visual><binding><text>Old</text></binding></visual></toast>');
    """)
    con.commit()
    src = nm.WindowsToastSource(db)
    assert src.poll() == []                     # existing ones are not announced
    con.execute("INSERT INTO Notification VALUES (2, 2, 1, 'toast', ?)",
                (b"<toast><visual><binding><text>Ali</text><text>Salam!</text></binding></visual></toast>",))
    con.execute("INSERT INTO Notification VALUES (3, 3, 2, 'toast', ?)",
                (b"<toast><visual><binding><text>Our own</text></binding></visual></toast>",))
    con.commit()
    con.close()
    alerts = src.poll()
    assert [(a.app, a.title, a.body) for a in alerts] == [("WhatsApp", "Ali", "Salam!")]


# ---- translation helpers ---------------------------------------------------------------

def test_looks_roman_urdu():
    assert translate.looks_roman_urdu("Main raste mein hoon")
    assert translate.looks_roman_urdu("ji haan main kal free hoon")
    assert not translate.looks_roman_urdu("I am on the way")


def test_pick_lines_paragraph_under_pointer():
    lines = [("Settings saved.", (21, 25, 192, 49)),
             ("Your subscription payment failed. Update your", (20, 125, 544, 149)),
             ("billing information to avoid interruption.", (21, 160, 456, 184)),
             ("Footer text here", (22, 255, 200, 274))]
    assert translate.pick_lines(lines, 200, 165) == ("Your subscription payment failed. Update your "
                                                     "billing information to avoid interruption.")
    assert translate.pick_lines(lines, 60, 30) == "Settings saved."
    assert translate.pick_lines(lines, 850, 900) == ""


# ---- WhatsApp -------------------------------------------------------------------------

@pytest.mark.parametrize("said, contact, message", [
    ("send a whatsapp message to Mama ji saying I am on the way", "Mama ji", "I am on the way"),
    ("mama ji ko whatsapp karo ke main raste mein hoon", "mama ji", "main raste mein hoon"),
    ("Mama ji ko message bhejo ki main kal free hoon", "Mama ji", "main kal free hoon"),
    ("message Ali on whatsapp that I will call later", "Ali", "I will call later"),
    ("open whatsapp, go to Mama Ji chat, and send I am available", "Mama Ji", "I am available"),
    ("send mama ji a whatsapp message", "mama ji", None),
])
def test_whatsapp_parsing(said, contact, message):
    intent = parse(said, use_llm=False)
    assert intent.name == "send_whatsapp"
    assert intent.args == {"contact": contact, "message": message}


def test_resolve_contact(monkeypatch):
    monkeypatch.setattr(get_config(), "contacts", {"mama ji": {"whatsapp": "Mamu Jaan", "phone": "+92 300 1234567"}})
    assert whatsapp.resolve_contact("Mama Ji") == ("Mamu Jaan", "+92 300 1234567")
    assert whatsapp.resolve_contact("mama g") == ("Mamu Jaan", "+92 300 1234567")
    assert whatsapp.resolve_contact("Ahmed") == ("Ahmed", None)


def test_name_matches():
    assert whatsapp.name_matches("Mama Ji", "Mama Ji")
    assert whatsapp.name_matches("Mama Ji", "mama ji, online")
    assert not whatsapp.name_matches("Mama Ji", "Ahmed Khan")


@pytest.fixture
def wa_workflow(monkeypatch):
    from app.companion import Companion
    from app.workflow_extended import ExtendedWorkflow

    sent = []
    monkeypatch.setattr(whatsapp, "send_message", lambda name, msg, phone=None: (sent.append((name, msg, phone)),
                                                                                  f"Message sent to {name}.")[1])
    monkeypatch.setattr(translate, "to_roman_urdu", lambda m: "Main raste mein hoon." if m == "I am on the way" else m)
    monkeypatch.setattr(get_config(), "contacts", {})
    wf = ExtendedWorkflow(Companion(), None)
    wf.sent = sent
    return wf


def test_whatsapp_sends_only_after_yes(wa_workflow):
    asked = []
    wa_workflow.ask = lambda q, o=None: (asked.append(q), "Yes")[1]
    _, reply = wa_workflow.run_text("send a whatsapp message to Mama ji saying I am on the way")
    assert wa_workflow.sent == [("Mama ji", "Main raste mein hoon.", None)]
    assert "Main raste mein hoon." in asked[0]           # message read back in Roman Urdu before sending
    assert reply == "Message sent to Mama ji."


def test_whatsapp_not_sent_on_no(wa_workflow):
    wa_workflow.ask = lambda q, o=None: "No"
    wa_workflow.run_text("mama ji ko whatsapp karo ke main raste mein hoon")
    assert wa_workflow.sent == []


def test_whatsapp_asks_for_missing_message(wa_workflow):
    answers = {"What should I send to mama ji?": "main 5 minute mein aa raha hoon"}
    wa_workflow.ask = lambda q, o=None: answers.get(q, "Yes")
    wa_workflow.run_text("send mama ji a whatsapp message")
    assert wa_workflow.sent == [("mama ji", "main 5 minute mein aa raha hoon", None)]


def test_whatsapp_feature_switch(wa_workflow, monkeypatch):
    monkeypatch.setattr(get_config(), "features", {"whatsapp": False})
    wa_workflow.ask = lambda q, o=None: "Yes"
    _, reply = wa_workflow.run_text("send a whatsapp message to Ali saying hi")
    assert wa_workflow.sent == [] and "turned off" in reply


# ---- web question / answer protocol ---------------------------------------------------------

def test_web_ask_protocol(monkeypatch, wa_workflow):
    import json

    from fastapi.testclient import TestClient

    from app.web import server

    st = server.State.__new__(server.State)   # skip the heavy constructor (scheduler, speech)
    import threading
    from collections import deque
    from itertools import count
    st.lock, st.workflow, st.companion = threading.Lock(), wa_workflow, wa_workflow.companion
    st.events, st._ids, st.services, st.voice, st._listener = deque(), count(1), None, None, None

    class NoScheduler:
        def shutdown(self):
            pass
    st.scheduler = NoScheduler()
    monkeypatch.setattr(server, "state", st)
    monkeypatch.setattr(server.State, "start_background", lambda self: None)
    with TestClient(server.app) as client:
        h = {"X-Companion-Token": server.TOKEN}
        assert client.post("/api/message", json={"text": "hi"}).status_code == 403
        text = "send a whatsapp message to Ali saying hi"
        first = json.loads(client.post("/api/message", headers=h, json={"text": text}).text.splitlines()[0])
        assert first["type"] == "ask" and first["options"] == ["Yes", "No"]
        assert wa_workflow.sent == []                         # nothing happens until answered
        second = json.loads(client.post("/api/message", headers=h, json={
            "text": text, "answers": {first["question"]: "haan bhej do"}}).text.splitlines()[0])
        assert second == {"type": "reply", "intent": "send_whatsapp", "text": "Message sent to Ali."}
        assert wa_workflow.sent == [("Ali", "hi", None)]
