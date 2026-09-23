"""Execute a parsed intent and return a short, speakable result."""

from __future__ import annotations

import inspect
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from app import advice
from app.accessibility import browser, screen, vision, web_executor
from app.accessibility.context_logger import ContextLogger
from app.automation import accounts, desktop, executor, launcher
from app.code import debugger, file_manager, generator, git_manager
from app.command_parser import Intent
from app.companion import Companion
from app.config import ROOT, get_config
from app.interaction import Ask, ask_confirm, is_yes, no_answer
from app.logger import get_logger
from app.mentor import prayer, wakeup
from app.mentor.focus import get_session
from app.scheduler import CompanionScheduler

log = get_logger(__name__)

Confirm = Callable[[str], bool]

HELP_TEXT = (
    "You can ask me to: open apps or websites, search Google or YouTube, read or describe your screen, "
    "take a screenshot, type text or press keys, change volume, remind you in some minutes, tell prayer times, "
    "start a focus session, add goals, take notes, list or read files, write code, check the battery, "
    "or just chat with me."
)


# Feature groups the user can switch on/off in Settings: key -> (label, intents)
FEATURES: dict[str, tuple[str, set[str]]] = {
    "apps": ("Open/close apps and websites, Google & YouTube search", {"open", "close_app", "search"}),
    "web": ("Answers from the web, page summaries", {"web_answer", "summarize_page"}),
    "screen": ("Read / describe the screen, screenshots, clipboard, what's under the mouse",
               {"read_screen", "describe_screen", "screenshot", "read_clipboard", "describe_pointer"}),
    "keyboard": ("Typing, pressing keys, switching windows, volume",
                 {"type_text", "press_keys", "switch_window", "list_windows", "volume"}),
    "commands": ("Run PowerShell commands (always asks first)", {"run_command"}),
    "files": ("Files, folders and notes", {"list_folder", "find_file", "read_file", "note"}),
    "code": ("Write code, debug scripts, git", {"generate_code", "debug_file", "git"}),
    "mentor": ("Reminders, focus sessions, goals, advice, morning summary",
               {"remind", "focus_start", "focus_stop", "add_goal", "list_goals", "advice", "morning", "update_goal",
                "remove_goal", "done_goal"}),
    "prayer": ("Prayer times and prayer reminders", {"prayer"}),
    "memory": ("Remember things about me and keep that memory up to date",
               {"remember", "list_memories", "forget_memory", "memory_changes"}),
    "system": ("Battery / CPU status", {"system_status"}),
}
INTENT_FEATURE = {intent: key for key, (_, intents) in FEATURES.items() for intent in intents}
# switches without their own intents (checked inside handlers)
EXTRA_FEATURES = {
    "accounts": "Ask which account/profile before opening Gmail, YouTube, Instagram, Chrome…",
    "notifications": "Watch notifications from all apps and browser tabs and read them out",
    "translate": "Translate English on screen (selected / under the mouse) into Roman Urdu",
    "whatsapp": "Send WhatsApp messages by voice (always asks before sending)",
    "wake": "Wake word — start talking by saying my name",
    "calls": "Notice calls, offer to record them and take notes, talk about them afterwards",
    "expert": "Expert mode for web development, design and architecture questions",
    "install": "Install software from the Microsoft Store / winget (always asks first)",
    "pointer": "Move the mouse to buttons, highlight and click them for me",
}
BROWSER_NAMES = {"chrome": "chrome", "google chrome": "chrome", "edge": "edge", "microsoft edge": "edge",
                 "brave": "brave"}


# What may run while Windows is locked: talking and information, nothing that acts on the desktop,
# files, accounts or messages — the lock screen must keep protecting the computer.
LOCKED_OK = {"help", "say", "wait", "set_user_name", "set_city", "pause_listening", "repeat", "recent_activity", "time", "date", "remind", "prayer", "list_goals",
             "add_goal", "advice", "morning", "system_status", "focus_start", "focus_stop", "call_notes",
             "record_call", "expert", "research", "remember", "list_memories", "diagnose", "update_goal",
             "done_goal", "memory_changes"}


def _locked() -> bool:
    from app.runtime import screen_locked

    try:
        return screen_locked()
    except Exception:
        return False


def third_person(fact: str) -> str:
    """'I like cricket' -> 'User likes cricket'; 'my sister is Ayesha' -> "User's sister is Ayesha"."""
    t = fact.strip()
    t = re.sub(r"^(?:i am|i'm)\b", "User is", t, flags=re.I)
    t = re.sub(r"^i have\b", "User has", t, flags=re.I)
    t = re.sub(r"^i was\b", "User was", t, flags=re.I)
    m = re.match(r"^i (\w+)(.*)$", t, flags=re.I)
    if m:
        verb = m.group(1).lower()
        if verb in ("can", "will", "would", "should", "must", "may", "might", "could", "don't", "didn't", "can't", "won't"):
            pass
        elif verb.endswith("y") and verb[-2:-1] not in "aeiou":
            verb = verb[:-1] + "ies"
        elif verb.endswith(("s", "sh", "ch", "x", "o")):
            verb += "es"
        else:
            verb += "s"
        t = f"User {verb}{m.group(2)}"
    t = re.sub(r"^my\b", "User's", t, flags=re.I)
    t = re.sub(r"\bmy\b", "their", t, flags=re.I)
    t = re.sub(r"\bme\b", "them", t, flags=re.I)
    return t if t.lower().startswith("user") else f"User: {t}"


def deny(_: str) -> bool:
    return False


class Workflow:
    def __init__(self, companion: Companion, scheduler: CompanionScheduler | None = None,
                 confirm: Confirm | None = None, context: ContextLogger | None = None, ask: Ask | None = None):
        self.companion = companion
        self.db = companion.db
        self.scheduler = scheduler
        self.ask: Ask = ask or no_answer
        self._confirm = confirm
        self.context = context or ContextLogger()
        self.services = None  # set by Services so voice-only features (mouse reading, enrolment) can reach it
        self.after_settings = None  # set by the app: restart what a settings change affects

    def confirm(self, question: str) -> bool:
        """Yes/no question; uses the dedicated confirm callback if one was given, else `ask`."""
        if self._confirm is not None:
            return self._confirm(question)
        return ask_confirm(self.ask, question)

    def execute(self, intent: Intent) -> str | None:
        """Run the intent. Returns None for chat, so the caller can stream the reply itself."""
        if intent.is_chat:
            return None
        handler = getattr(self, f"do_{intent.name}", None)
        if handler is None:
            return None
        if intent.name not in LOCKED_OK and _locked():
            result = ("The screen is locked. I can answer questions, reminders and prayer times now; "
                      "unlock the laptop for anything that controls the computer.")
            self.context.log(intent.text, intent.name, result)
            return result
        feature = INTENT_FEATURE.get(intent.name)
        if feature and not get_config().feature_on(feature):
            result = f"That feature ({FEATURES[feature][0].split(',')[0].lower()}) is turned off in Settings."
            self.context.log(intent.text, intent.name, result)
            return result
        # LLM classifications sometimes add extra keys; pass only what the handler accepts
        params = inspect.signature(handler).parameters
        args = {k: v for k, v in intent.args.items() if k in params and v is not None}
        try:
            result = handler(**args)
        except TypeError as e:
            log.warning("Bad args for %s: %s (%s)", intent.name, intent.args, e)
            return None  # malformed LLM classification — treat as chat
        except Exception as e:
            log.exception("Intent %s failed", intent.name)
            result = f"Sorry, that didn't work: {e}"
        self.context.log(intent.text, intent.name, result)
        return result

    # ---- conversation & self ---------------------------------------------
    def do_help(self) -> str:
        return HELP_TEXT

    def do_say(self, text: str) -> str:
        return text

    def do_wait(self, seconds: float) -> str:
        import time

        time.sleep(max(0.0, min(float(seconds), 30.0)))
        return ""

    def do_repeat(self) -> str:
        return self.context.last_reply or "I haven't said anything yet."

    def do_recent_activity(self) -> str:
        return self.context.describe_recent()

    def do_time(self) -> str:
        return f"It's {datetime.now().strftime('%I:%M %p').lstrip('0')}."

    def do_date(self) -> str:
        return f"Today is {datetime.now().strftime('%A, %d %B %Y')}."

    def do_remember(self, fact: str) -> str:
        fact = fact.strip().rstrip(".")
        sentence = fact if fact.lower().startswith("user") else third_person(re.sub(r"^(?:that )", "", fact))
        action, _ = self.companion.memory.remember(sentence, importance=4, said=fact)
        return {"added": "Got it, I'll remember that.", "updated": "Got it — I've updated what I remembered.",
                "kept": "I already know that."}[action]

    def do_list_memories(self) -> str:
        mems = self.companion.memory.all()
        if not mems:
            return "I don't know much about you yet. Tell me about yourself!"
        return "Here's what I remember: " + " ".join(m.content.rstrip(".") + "." for m in mems[:10])

    # ---- mentor ----------------------------------------------------------
    def do_remind(self, minutes: float, text: str) -> str:
        if self.scheduler is None:
            return "Reminders need the scheduler, which isn't running."
        return self.scheduler.add_reminder(text, float(minutes))

    def do_prayer(self) -> str:
        return prayer.describe_today()

    def do_focus_start(self, minutes: int | None = None, task: str = "") -> str:
        return get_session().start(int(minutes) if minutes else None, task or "")

    def do_focus_stop(self) -> str:
        return get_session().stop()

    def do_add_goal(self, title: str) -> str:
        action, _ = self.companion.memory.set_goal(title, said=title)
        if action == "kept":
            return f"That's already one of your goals: {title}."
        if action == "updated":
            return f"Updated your goal to: {title}."
        return f"Added your goal: {title}."

    def _split_goal_change(self, pair: str) -> tuple[str, str]:
        """'travelling to Dubai to travelling to London' has several possible splits; pick the one whose
        first half best matches an existing goal (or, with no goals, the most balanced one)."""
        import re as _re

        cuts = [m for m in _re.finditer(r"\s+(?:to|with|into)\s+", pair)]
        options = [(pair[:m.start()].strip(), pair[m.end():].strip()) for m in cuts]
        options = [(o, n) for o, n in options if o and n]
        if not options:
            return pair, pair
        goals = self.db.goals()

        def score(option):
            old, new = option
            match = self.companion.memory.find_goals(old, limit=1) if goals else []
            balance = -abs(len(old.split()) - len(new.split())) * 0.01
            return (match[0][0] if match else 0.0) + balance
        return max(options, key=score)

    def do_update_goal(self, new: str | None = None, old: str | None = None, pair: str | None = None) -> str:
        store = self.companion.memory
        if pair:
            old, new = self._split_goal_change(pair)
        if old is None and len(self.db.goals()) > 1:
            matches = store.find_goals(new)
            # ask only when two goals are equally likely to be the one being changed
            if len(matches) > 1 and matches[0][0] - matches[1][0] < 0.08:
                titles = [t for _, _, t in matches]
                choice = self.ask(f"Which goal should become “{new}”? {', '.join(titles)}.", titles)
                if not choice:
                    return "Okay, I didn't change anything."
                old = choice
        action, _, previous = store.change_goal(old, new.strip().rstrip("."))
        store.remember(f"User's goal is to {new.strip().rstrip('.')}", "goal", 4, said=f"instead of {previous or ''}")
        if action == "updated":
            return f"Updated your goal: “{previous}” is now “{new}”."
        return f"Added your goal: {new}."

    def do_remove_goal(self, query: str) -> str:
        return self._finish_goal(query, done=False)

    def do_done_goal(self, query: str) -> str:
        return self._finish_goal(query, done=True)

    def _finish_goal(self, query: str, done: bool) -> str:
        matches = self.companion.memory.find_goals(query)
        if not matches or matches[0][0] < 0.35:
            return f"I couldn't find a goal about {query}."
        _, goal_id, title = matches[0]
        if not done and not self.confirm(f"Remove your goal “{title}”?"):
            return "Okay, I kept it."
        self.companion.memory.remove_goal(goal_id, done=done)
        return f"Mubarak ho! “{title}” is done." if done else f"Removed the goal “{title}”."

    def do_forget_memory(self, query: str) -> str:
        matches = self.companion.memory.find_memories(query)
        if not matches or matches[0][0] < 0.45:
            return f"I don't remember anything about {query}."
        _, memory = matches[0]
        if not self.confirm(f"Forget this: “{memory.content}”?"):
            return "Okay, I'll keep remembering it."
        self.companion.memory.forget(memory.id)
        return "Done — I've forgotten that."

    def do_memory_changes(self) -> str:
        rows = self.db.history(limit=5)
        if not rows:
            return "Nothing has changed in my memory yet."
        parts = []
        for r in rows:
            when = r["changed_at"][:10]
            if r["action"] == "updated":
                parts.append(f"on {when} “{r['old_content']}” became “{r['new_content']}”")
            elif r["action"] == "forgotten":
                parts.append(f"on {when} I forgot “{r['old_content']}”")
            elif r["action"] == "removed":
                parts.append(f"on {when} you removed the goal “{r['old_content']}”")
            elif r["action"] == "done":
                parts.append(f"on {when} you finished “{r['old_content']}”")
        return "Recent changes: " + "; ".join(parts) + "."

    def do_list_goals(self) -> str:
        goals = self.db.goals()
        if not goals:
            return "You have no active goals. Say 'add goal' followed by your goal."
        return "Your goals are: " + "; ".join(g.title for g in goals) + "."

    def do_morning(self) -> str:
        return wakeup.morning_message(self.db)

    def do_advice(self) -> str:
        return advice.get_advice(self.db)

    # ---- screen & web ----------------------------------------------------
    def do_screenshot(self) -> str:
        path = screen.save_screenshot()
        return f"Screenshot saved as {path.name}."

    def do_read_screen(self, full: bool = False) -> str:
        text = screen.read_screen(window_only=not full)
        return text if text else "I couldn't find any text on the screen."

    def do_describe_screen(self) -> str:
        return vision.describe_screen()

    def do_read_clipboard(self) -> str:
        return desktop.read_clipboard()

    def do_open(self, target: str) -> str:
        site, spoken_account = accounts.split_target(target)
        platform = accounts.platform_key(site)
        if platform or accounts.words(site) in BROWSER_NAMES:
            opened = self._open_with_account(site, platform, spoken_account)
            if opened is not None:
                return opened
        if launcher.find_app(target, fuzzy=False):
            return launcher.open_app(target, fuzzy=False)
        from app.automation.system import WEB_FALLBACK

        web = WEB_FALLBACK.get(target.lower().removesuffix(" app").removesuffix(" desktop").strip())
        if web:
            browser.open_url(web)
            return f"{target} isn't installed on this laptop, so I opened the web version."
        if browser.resolve(target) or target.lower() in browser.SITES:
            return browser.open_site(target)
        result = launcher.open_app(target)
        if result.startswith("I couldn't find"):
            return browser.open_site(target)  # e.g. "open cricket scores" -> search
        return result

    def _open_with_account(self, site: str, platform: str | None, spoken: str | None) -> str | None:
        """Ask which account/profile to use when there is more than one. None = not handled here."""
        if not get_config().feature_on("accounts"):
            return None
        browser_name = BROWSER_NAMES.get(accounts.words(site))
        options = accounts.browser_profiles(browser_name) if browser_name else accounts.accounts_for(platform)
        if not options:
            return None
        account = accounts.find_account(spoken, options) if spoken else None
        if account is None and len(options) == 1:
            account = options[0]
        if account is None:
            labels = [a.label for a in options]
            prefix = f"I couldn't find an account called {spoken}. " if spoken else ""
            answer = self.ask(f"{prefix}Which account should I open {site} with? " + ", ".join(labels) + ".", labels)
            if not answer:
                return f"Okay, I didn't open {site}."
            account = accounts.find_account(answer, options)
            if account is None:
                return f"I couldn't find an account called {answer}."
        url = accounts.url_for(platform, account) if platform else None
        accounts.open_in_profile(url, account)
        return f"Opening {site} with the {account.label} account."

    # ---- settings by voice -------------------------------------------------------------
    def _apply_settings(self, body: dict):
        from app import settings_service

        result = settings_service.apply(body)
        if self.after_settings:
            self.after_settings(result)
        return result

    def do_set_assistant_name(self, name: str) -> str:
        name = name.strip().strip(".").title()
        if not self.confirm(f"Change my name to {name}? Then you'll wake me by saying “Hey {name}”."):
            return "Okay, I'll keep my name."
        self._apply_settings({"assistant": {"name": name}, "voice": {"wake_phrases": ""}})
        return f"From now on my name is {name}. Say “Hey {name}” when you need me."

    def do_set_user_name(self, name: str) -> str:
        name = name.strip().strip(".").title()
        self._apply_settings({"user": {"name": name}})
        self.companion.memory.remember(f"User's name is {name}", "person", 5, said=f"my name is now {name}")
        return f"Nice to meet you, {name}. I'll call you {name}."

    def do_set_city(self, city: str) -> str:
        from app.places import lookup

        place = lookup(city)
        if place is None:
            self._apply_settings({"user": {"city": city.strip().title()}})
            return (f"I saved {city.title()} as your city, but I don't know its location, so prayer times need "
                    "latitude and longitude in Settings.")
        name, lat, lon = place
        self._apply_settings({"user": {"city": name, "latitude": lat, "longitude": lon}})
        self.companion.memory.remember(f"User lives in {name}", "fact", 4, said=f"I live in {name} now")
        return f"Got it — you're in {name}. Prayer times and reminders now use {name}."

    def do_toggle_feature(self, name: str, on: bool) -> str:
        from app import settings_service

        items = settings_service.features()
        synonyms = {"wake word": "wake", "notification": "notifications", "calls": "calls", "call recording": "calls",
                    "translation": "translate", "whatsapp": "whatsapp", "memory": "memory", "prayer": "prayer",
                    "namaz": "prayer", "mouse control": "pointer", "installing": "install", "expert mode": "expert",
                    "accounts": "accounts", "screen reading": "screen", "keyboard": "keyboard", "commands": "commands"}
        from difflib import SequenceMatcher

        spoken = name.lower().strip()
        key = next((v for k, v in synonyms.items() if re.search(rf"\b{re.escape(k)}s?\b", spoken)), None)
        if key is None:
            # whole-name match only (a loose word match once turned "lights" into "highlight")
            names = {f["key"]: f["key"] for f in items} | {f["label"].split(",")[0].split("(")[0].strip().lower(): f["key"]
                                                             for f in items}
            best = max(names, key=lambda n: SequenceMatcher(None, spoken, n).ratio())
            key = names[best] if SequenceMatcher(None, spoken, best).ratio() >= 0.85 else None
        if key not in {f["key"] for f in items}:
            return f"I don't have a feature called {name}."
        if key == "wake" and not on:
            return "If I turn off the wake word I can't hear you any more — say “stop listening” instead, or use Settings."
        self._apply_settings({"features": {key: on}})
        label = next(f["label"] for f in items if f["key"] == key).split("(")[0].split(",")[0].strip()
        return f"{label}: {'on' if on else 'off'}."

    def do_show_window(self, tab: str = "chat") -> str:
        opener = (self.services.extra_hotkeys.get("open_window") if self.services else None)
        if opener is None:
            return "My window isn't available in this mode."
        tab = {"window": "chat", "chat": "chat", "settings": "settings", "memory": "memory", "commands": "commands",
               "accounts": "accounts"}.get(tab, "chat")
        opener(tab) if tab != "chat" else opener()
        return "Here you go." if tab == "chat" else f"Opening {tab}."

    def do_pause_listening(self, minutes: int = 0) -> str:
        wake = self.services.wake if self.services else None
        if wake is None:
            return "I'm not listening for my name right now anyway."
        wake.sleep()
        if minutes:
            import threading as _t

            _t.Timer(minutes * 60, wake.wake_up).start()
            return f"Okay, I'll stop listening for {minutes} minutes."
        return "Okay, I'll stop listening. Press Ctrl+Alt+Space or use the tray icon when you need me again."

    # ---- WhatsApp & translation --------------------------------------------
    def do_send_whatsapp(self, contact: str, message: str | None = None) -> str:
        from app.accessibility import translate
        from app.automation import whatsapp

        if not get_config().feature_on("whatsapp"):
            return "Sending WhatsApp messages is turned off in Settings."
        contact = re.sub(r"^(?:to |my |the )", "", contact.strip(), flags=re.I).strip(" ,.")
        name, phone = whatsapp.resolve_contact(contact)
        if not message:
            message = self.ask(f"What should I send to {name}?", None)
            if not message:
                return "Okay, I didn't send anything."
        message = message.strip().strip('"').strip()
        if get_config().whatsapp.message_language == "roman_urdu":
            try:
                message = translate.to_roman_urdu(message)
            except Exception as e:  # keep the original words if the model isn't available
                log.warning("Couldn't convert message to Roman Urdu: %s", e)
        answer = self.ask(f'Send this WhatsApp message to {name}: "{message}"?', ["Yes", "No"])
        if not answer or (answer != "Yes" and is_yes(answer) is not True):
            return "Okay, I didn't send it."
        try:
            return whatsapp.send_message(name, message, phone)
        except whatsapp.MultipleMatches as e:
            choice = self.ask(f"I found more than one chat for {name}: {', '.join(e.names)}. Which one?", e.names)
            if not choice:
                return "Okay, I didn't send it."
            try:
                return whatsapp.send_message(choice, message, phone)
            except whatsapp.WhatsAppError as e2:
                return str(e2)
        except whatsapp.WhatsAppError as e:
            return str(e)

    def do_whatsapp_chat(self, contact: str) -> str:
        from app.automation import whatsapp

        if not get_config().feature_on("whatsapp"):
            return "WhatsApp control is turned off in Settings."
        name, phone = whatsapp.resolve_contact(contact)
        try:
            whatsapp.open_chat(name, phone)
        except whatsapp.MultipleMatches as e:
            choice = self.ask(f"I found more than one chat for {name}: {', '.join(e.names)}. Which one?", e.names)
            if not choice:
                return "Okay."
            try:
                whatsapp.open_chat(choice, phone)
            except whatsapp.WhatsAppError as e2:
                return str(e2)
            name = choice
        except whatsapp.WhatsAppError as e:
            return str(e)
        return f"The chat with {name} is open."

    def do_translate_screen(self) -> str:
        from app.accessibility import translate

        if not get_config().feature_on("translate"):
            return "Translation is turned off in Settings."
        english, result = translate.translate_on_screen()
        return f"English: {english}\n{result}" if english else result

    def do_translate_text(self, text: str) -> str:
        from app.accessibility import translate

        if not get_config().feature_on("translate"):
            return "Translation is turned off in Settings."
        return translate.translate(text)

    # ---- self-diagnosis, expert, system -------------------------------------
    def do_diagnose(self) -> str:
        from app import doctor

        issues, errors = doctor.check(), doctor.recent_errors()
        report = doctor.summary(issues, errors)
        fixed = []
        for issue in issues:
            if issue.fix_command and self.confirm(f"{issue.title}. Shall I {issue.fix_label.lower()}?"):
                fixed.append(doctor.fix(issue))
        if errors and self.confirm("Shall I work out the cause of the errors in my log?"):
            fixed.append(doctor.explain_errors(errors))
        return " ".join([report] + fixed)

    def do_expert(self, question: str) -> str:
        from app.expert import get_expert

        if not get_config().feature_on("expert"):
            return "Expert mode is turned off in Settings."
        expert = get_expert()
        answer = expert.ask(question)
        return f"{answer}\n\n(answered by {expert.last_backend})"

    def do_research(self, topic: str) -> str:
        from app.expert import get_expert

        if not get_config().feature_on("expert"):
            return "Expert mode is turned off in Settings."
        expert = get_expert()
        return f"{expert.research(topic)}\n\n(answered by {expert.last_backend})"

    def do_create_project(self, kind: str, name: str | None = None) -> str:
        from app.code.file_manager import FOLDERS
        from app.expert import SCAFFOLDS, scaffold_kind
        from app.mentor.notifications import notify

        key = scaffold_kind(kind) or "nextjs"
        label, template = SCAFFOLDS[key]
        name = re.sub(r"[^a-z0-9-]", "-", (name or self.ask(f"What should the {label} project be called?", None) or "").lower()).strip("-")
        if not name:
            return "Okay, I didn't create a project."
        folder = FOLDERS["projects"]
        folder.mkdir(parents=True, exist_ok=True)
        if (folder / name).exists():
            return f"{folder / name} already exists."
        command = template.format(name=name)
        if not self.confirm(f"Create a {label} project called {name} in {folder}? This runs: {command}"):
            return "Okay, I didn't create it."

        def work():
            import subprocess

            proc = subprocess.run(command, cwd=folder, shell=True, capture_output=True, text=True, timeout=1800,
                                  encoding="utf-8", errors="replace")
            if proc.returncode == 0:
                subprocess.Popen(["cmd", "/c", "code", str(folder / name)], creationflags=subprocess.CREATE_NO_WINDOW)
                notify("Project ready", f"{name} is created and opened in VS Code. Run 'npm run dev' inside it to start.")
            else:
                notify("Project failed", (proc.stderr or proc.stdout)[-300:])

        import threading

        threading.Thread(target=work, daemon=True).start()
        return f"Creating {name} now — I'll tell you when it's ready. It usually takes a minute or two."

    def do_install_app(self, name: str, store: bool = False) -> str:
        from app.automation import system
        from app.mentor.notifications import notify

        if not get_config().feature_on("install"):
            return "Installing software is turned off in Settings."
        packages = system.winget_search(name)
        if store:
            packages = [p for p in packages if p.source == "msstore"] or packages
        if not packages:
            return f"I couldn't find {name} in the Microsoft Store or winget."
        labels = [p.label() for p in packages]
        choice = labels[0] if len(labels) == 1 else self.ask(f"I found: {', '.join(labels)}. Which one should I install?", labels)
        if not choice:
            return "Okay, I didn't install anything."
        pkg = packages[labels.index(choice)]
        if not self.confirm(f"Install {pkg.name} {pkg.version} from {pkg.label().split('(')[-1].rstrip(')')}? "
                            "This accepts the package's licence terms."):
            return "Okay, I didn't install it."
        system.winget_install(pkg, on_done=lambda msg: notify("Install finished", msg))
        return f"Installing {pkg.name} in the background. I'll tell you when it's done."

    def do_show_in_explorer(self, query: str) -> str:
        from app.automation import system

        found = file_manager.find_files(query, limit=1) if not Path(query).exists() else [Path(query)]
        if not found:
            return f"I couldn't find a file called {query}."
        return system.show_in_explorer(found[0])

    def do_switch_tab(self, name: str) -> str:
        from app.automation import system

        return system.switch_browser_tab(name) or f"I couldn't find a browser tab for {name}."

    # ---- pointer -------------------------------------------------------------------------
    def do_describe_pointer(self) -> str:
        from app.accessibility import pointer

        return pointer.describe_pointer()

    def do_show_control(self, query: str) -> str:
        from app.accessibility import pointer

        if not get_config().feature_on("pointer"):
            return "Mouse control is turned off in Settings."
        return pointer.show(query)

    def do_click_control(self, query: str) -> str:
        from app.accessibility import pointer

        if not get_config().feature_on("pointer"):
            return "Mouse control is turned off in Settings."
        return pointer.click(query, confirm=self.confirm)

    def do_mouse_reading(self, on: bool = True) -> str:
        if self.services is None:
            return "Mouse reading needs the voice mode or the browser mode running."
        return self.services.mouse_reading(on)

    # ---- voice print & calls ----------------------------------------------------------------------
    def do_enroll_voice(self) -> str:
        if self.services is None or self.services.voice is None:
            return "Voice enrolment works in voice mode, or on the Settings page in the browser."
        return self.services.enroll_voice(self.ask)

    def do_record_call(self, on: bool = True) -> str:
        if self.services is None or self.services.calls is None or self.services.calls.call is None:
            return "There's no call in progress."
        watcher = self.services.calls
        if on:
            if watcher.call.recorder:
                return "This call is already being recorded."
            watcher.call.decided = False
            watcher.decide(True)
            return "Recording this call."
        if watcher.call.recorder:
            watcher.call.recorder.stop()
            return "Stopped recording."
        return "This call isn't being recorded."

    def do_call_notes(self) -> str:
        folder = get_config().dir("data/calls")
        notes = sorted(folder.glob("*/notes.md"))
        if not notes:
            return "I don't have notes from any call yet."
        return notes[-1].read_text(encoding="utf-8")

    def do_close_app(self, name: str) -> str:
        import psutil

        matches = launcher.close_app(name)
        if not matches:
            return f"{name} doesn't seem to be running."
        if not self.confirm(f"Close {', '.join(matches)}? Unsaved work may be lost."):
            return "Okay, I left it open."
        count = 0
        for p in psutil.process_iter(["name"]):
            if p.info["name"] in matches:
                try:
                    p.terminate()
                    count += 1
                except psutil.Error:
                    pass
        return f"Closed {name}." if count else f"I couldn't close {name}."

    def do_search(self, query: str, engine: str = "google") -> str:
        return browser.search(query, engine)

    def do_web_answer(self, query: str) -> str:
        return web_executor.answer_from_web(query)

    def do_summarize_page(self, url: str) -> str:
        return web_executor.summarize_page(url)

    # ---- desktop control -------------------------------------------------
    def do_type_text(self, text: str) -> str:
        return desktop.type_text(text)

    def do_press_keys(self, keys: str) -> str:
        return desktop.press_keys(keys)

    def do_volume(self, direction: str) -> str:
        return desktop.volume(direction)

    def do_switch_window(self, name: str) -> str:
        result = desktop.switch_window(name)
        if result.startswith("I couldn't find"):
            from app.automation import system

            return system.switch_browser_tab(name) or result
        return result

    def do_list_windows(self) -> str:
        titles = desktop.list_windows()
        return f"{len(titles)} windows are open: " + "; ".join(titles[:10]) + "."

    def do_system_status(self) -> str:
        return desktop.system_status()

    def do_run_command(self, command: str) -> str:
        return executor.run(command, confirm=self.confirm)

    # ---- files & code ----------------------------------------------------
    def do_note(self, text: str) -> str:
        return file_manager.create_note(text)

    def do_list_folder(self, folder: str) -> str:
        return file_manager.list_folder(folder)

    def do_find_file(self, query: str) -> str:
        found = file_manager.find_files(query)
        if not found:
            return f"I couldn't find any file matching {query}."
        return f"I found {len(found)}: " + "; ".join(f"{p.name} in {p.parent.name}" for p in found[:5]) + "."

    def do_read_file(self, path: str) -> str:
        return file_manager.read_file(path)

    def do_generate_code(self, request: str) -> str:
        result = generator.generate(request)
        lines = result["code"].count("\n")
        return (f"I wrote {lines} lines of {result['language']} and saved it as {Path(result['path']).name} "
                f"in data, code output. {result['explanation']}").strip()

    def do_debug_file(self, path: str) -> str:
        p = Path(path)
        if not p.is_absolute():
            candidates = [ROOT / path, Path.cwd() / path] + file_manager.find_files(p.name, limit=1)
            p = next((c for c in candidates if Path(c).is_file()), p)
        apply = self.confirm("If it fails, should I save a corrected copy next to it?")
        return debugger.debug_file(p, apply_fix=apply)

    def do_git(self, action: str = "status") -> str:
        return git_manager.log(Path.cwd()) if action == "log" else git_manager.status(Path.cwd())
