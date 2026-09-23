"""Reading, validating and saving user settings — shared by the native Settings window and
the (optional) web interface, so both behave the same.

`apply(body)` saves every valid field, reports the invalid ones by name instead of failing the
whole save, treats a blank number as "back to the default", and says which running parts need
a restart (speech, background services, scheduler)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import get_config, save_settings

# Settings the user may edit: section -> {key: type}
EDITABLE = {
    "user": {"name": str, "city": str, "latitude": float, "longitude": float},
    "assistant": {"name": str},
    "expert": {"provider": str, "claude_model": str, "effort": str, "weekly_budget_usd": float, "local_model": str,
               "route_technical": bool},
    "calls": {"enabled": bool, "offer_recording": bool, "debrief": bool},
    "llm": {"chat_model": str, "temperature": float, "max_tokens": int, "translate_model": str},
    "voice": {"stt_model_size": str, "wake_model_size": str, "language": str, "hint": str, "input_device": str,
              "wake_enabled": bool, "wake_phrases": list, "wake_sensitivity": float,
              "voiceprint_enabled": bool, "voiceprint_threshold": float,
              "conversation": bool, "follow_up_seconds": float},
    "scheduler": {"checkin_time": str, "wakeup_time": str, "wakeup_enabled": bool,
                  "prayer_reminders_enabled": bool, "prayer_method": str, "madhab": str,
                  "prayer_reminder_minutes_before": int},
    "focus": {"default_minutes": int, "distractions": list},
    "notifications": {"enabled": bool, "windows_toasts": bool, "browser_tabs": bool, "read_content": bool},
    "hotkeys": {"enabled": bool, "translate": str, "talk": str, "open_window": str},
    "whatsapp": {"message_language": str, "verify_contact": bool},
}
VOICE_KEYS = {"stt_model_size", "wake_model_size", "language", "hint", "input_device", "wake_phrases"}

# Allowed ranges / choices, checked before anything is saved
RANGES = {
    "user.latitude": (-90, 90), "user.longitude": (-180, 180), "llm.temperature": (0, 1.5),
    "llm.max_tokens": (50, 8000), "voice.wake_sensitivity": (0.5, 0.99), "voice.voiceprint_threshold": (0.2, 0.95),
    "scheduler.prayer_reminder_minutes_before": (0, 120), "focus.default_minutes": (1, 600),
    "expert.weekly_budget_usd": (0, 10_000),
}
CHOICES = {
    "expert.provider": {"local", "claude"}, "expert.effort": {"low", "medium", "high", "xhigh", "max"},
    "voice.stt_model_size": {"tiny", "base", "small", "medium"},
    "voice.wake_model_size": {"tiny", "base", "small", "medium"}, "voice.language": {"", "en", "ur", "hi"},
    "scheduler.madhab": {"HANAFI", "SHAFI"}, "whatsapp.message_language": {"roman_urdu", "as_spoken"},
    "scheduler.prayer_method": {"KARACHI", "MUSLIM_WORLD_LEAGUE", "UMM_AL_QURA", "EGYPTIAN", "NORTH_AMERICA", "DUBAI",
                                "KUWAIT", "QATAR", "SINGAPORE", "MOON_SIGHTING_COMMITTEE", "UOIF"},
}
TIMES = {"scheduler.checkin_time", "scheduler.wakeup_time"}
# blank = "go back to the default" rather than an empty value
BLANK_MEANS_DEFAULT = {"assistant.name", "user.name", "llm.chat_model", "expert.claude_model", "hotkeys.translate",
                       "hotkeys.talk", "hotkeys.open_window"} | set(TIMES)
_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
KEEP_DEFAULT = object()


def coerce(kind, value, field: str = ""):
    """Turn a form value into the setting's type. Accepts what people actually type: '24,86',
    '24.86° N', '400.0'. Returns KEEP_DEFAULT for a blank field that should fall back to its
    default; raises ValueError with a message meant for the user."""
    text = value.strip() if isinstance(value, str) else value
    if kind is bool:
        return text if isinstance(text, bool) else str(text).lower() in {"1", "true", "yes", "on"}
    if kind is list:
        items = text if isinstance(text, list) else str(text or "").split(",")
        return [str(v).strip() for v in items if str(v).strip()]
    if text in ("", None):
        if kind is str and field not in BLANK_MEANS_DEFAULT:
            return ""
        return KEEP_DEFAULT
    if kind is str:
        text = str(text)
        if field in CHOICES and text not in CHOICES[field]:
            raise ValueError(f"must be one of: {', '.join(sorted(c or '(auto)' for c in CHOICES[field]))}")
        if field in TIMES and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", text):
            raise ValueError("must be a time like 09:00")
        return text
    match = _NUMBER.search(str(text))
    if not match:
        raise ValueError("must be a number")
    number = float(match.group(0).replace(",", "."))
    if str(text).upper().rstrip().endswith(("S", "W")) and field in ("user.latitude", "user.longitude"):
        number = -abs(number)  # 33.9 S / 118.2 W
    if field in RANGES:
        lo, hi = RANGES[field]
        if not lo <= number <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
    return int(round(number)) if kind is int else number


def current_values() -> dict:
    cfg = get_config()
    return {section: {key: getattr(getattr(cfg, section), key) for key in keys} for section, keys in EDITABLE.items()}


def features() -> list[dict]:
    from app.workflow import EXTRA_FEATURES, FEATURES

    cfg = get_config()
    items = [{"key": k, "label": label, "enabled": cfg.feature_on(k)} for k, (label, _) in FEATURES.items()]
    return items + [{"key": k, "label": label, "enabled": cfg.feature_on(k)} for k, label in EXTRA_FEATURES.items()]


@dataclass
class SaveResult:
    errors: dict[str, str] = field(default_factory=dict)
    speech_changed: bool = False       # Whisper prompt / model / mic changed -> reload speech recognition
    background_changed: bool = False   # wake word, notifications, hotkeys, calls, features -> restart services
    scheduler_changed: bool = False    # prayer location / times -> restart the scheduler


def _snapshot(cfg):
    speech = {k: getattr(cfg.voice, k) for k in VOICE_KEYS} | {"name": cfg.user.name, "assistant": cfg.assistant.name}
    background = (vars(cfg.voice).copy(), vars(cfg.notifications).copy(), vars(cfg.hotkeys).copy(), dict(cfg.features),
                  vars(cfg.calls).copy())
    scheduler = (cfg.user.latitude, cfg.user.longitude, vars(cfg.scheduler).copy())
    return speech, background, scheduler


def apply(body: dict) -> SaveResult:
    """Validate and save a {section: {key: value}} form (plus optional "features")."""
    from app.workflow import EXTRA_FEATURES, FEATURES

    cfg = get_config()
    before = _snapshot(cfg)
    changes: dict = {}
    remove: dict[str, list[str]] = {}
    result = SaveResult()
    for section, keys in EDITABLE.items():
        values = body.get(section)
        if not isinstance(values, dict):
            continue
        for key, kind in keys.items():
            if key not in values:
                continue
            name = f"{section}.{key}"
            try:
                value = coerce(kind, values[key], name)
            except (TypeError, ValueError) as e:
                result.errors[name] = str(e)  # skip just this field; everything else is still saved
                continue
            if value is KEEP_DEFAULT:
                remove.setdefault(section, []).append(key)
            else:
                changes.setdefault(section, {})[key] = value
    if "voice" in changes and changes["voice"].get("input_device") == "":
        changes["voice"].pop("input_device")
        remove.setdefault("voice", []).append("input_device")
    if isinstance(body.get("features"), dict):
        allowed = set(FEATURES) | set(EXTRA_FEATURES)
        changes["features"] = {**cfg.features, **{k: bool(v) for k, v in body["features"].items() if k in allowed}}
    cfg = save_settings(changes, remove)
    from app.llm import get_llm

    llm = get_llm()
    llm.model, llm.temperature, llm.max_tokens = cfg.llm.chat_model, cfg.llm.temperature, cfg.llm.max_tokens
    after = _snapshot(cfg)
    result.speech_changed = before[0] != after[0]
    result.background_changed = before[1] != after[1] or result.speech_changed
    result.scheduler_changed = before[2] != after[2]
    return result


# ---- my commands, accounts, contacts ---------------------------------------------------------

def save_commands(commands: list[dict]) -> dict[str, list[str]]:
    routines: dict[str, list[str]] = {}
    for item in commands:
        phrase = str(item.get("phrase", "")).strip()
        steps = [str(s).strip() for s in item.get("steps", []) if str(s).strip()]
        if phrase and steps:
            routines[phrase] = steps
    save_settings({"routines": routines})
    return routines


def save_accounts(accounts: dict, contacts: list[dict]) -> None:
    from app.automation import accounts as accounts_mod

    clean_accounts: dict[str, list[dict]] = {}
    for platform, items in (accounts or {}).items():
        if platform not in accounts_mod.PLATFORMS:
            continue
        clean = [{"label": str(a.get("label") or a["profile"]).strip(), "browser": str(a.get("browser") or "chrome"),
                  "profile": str(a["profile"]), "google_index": int(a.get("google_index") or 0),
                  "email": str(a.get("email") or "")}
                 for a in items or [] if isinstance(a, dict) and a.get("profile")]
        if clean:
            clean_accounts[platform] = clean
    clean_contacts: dict[str, dict] = {}
    for c in contacts or []:
        spoken = str(c.get("spoken", "")).strip()
        if spoken:
            clean_contacts[spoken] = {"whatsapp": str(c.get("whatsapp") or spoken).strip(),
                                      "phone": str(c.get("phone") or "").strip()}
    save_settings({"accounts": clean_accounts, "contacts": clean_contacts})
