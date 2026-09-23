"""Configuration: loads config.yaml, then applies .env / environment overrides."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional
    load_dotenv = None

ROOT = Path(__file__).resolve().parent.parent  # program files (read-only once installed)
# The installed Lyra.exe lives in Program Files, which normal users can't write to, so its data goes
# to %LOCALAPPDATA%\Lyra. Run from source, data stays next to the code as before.
FROZEN = bool(getattr(sys, "frozen", False))
APP_NAME = "Lyra"
DATA_ROOT = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME if FROZEN else ROOT
# Names used by earlier releases; their saved defaults are upgraded to Lyra's on load.
LEGACY_NAMES = {"md"}
# Settings changed from the browser UI. Kept apart from config.yaml so its comments survive;
# applied last, so they win over config.yaml and .env.
SETTINGS_FILE = DATA_ROOT / "data" / "settings.yaml"
SECTIONS = ("user", "assistant", "llm", "storage", "voice", "scheduler", "focus", "accessibility", "notifications", "hotkeys",
            "whatsapp", "expert", "calls")
# free-form mappings that are replaced as a whole when saved
MAPPINGS = ("routines", "features", "accounts", "contacts")


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class UserConfig:
    name: str = "User"
    timezone: str = "local"
    city: str = ""
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class AssistantConfig:
    name: str = "Lyra"  # what the assistant calls itself; also the default wake word


@dataclass
class LLMConfig:
    ollama_url: str = "http://localhost:11434"
    chat_model: str = "phi3:mini"
    embedding_model: str = "nomic-embed-text"
    temperature: float = 0.7
    max_tokens: int = 400  # cap on reply length; small CPU-only models can otherwise ramble for minutes
    translate_model: str = ""  # model for English -> Roman Urdu; "" = use chat_model
    timeout: int = 300
    keep_alive: str = "30m"  # how long Ollama keeps the chat model loaded; reloading it takes seconds


@dataclass
class StorageConfig:
    memory_db: str = "data/memory.db"
    screenshots_dir: str = "data/screenshots"
    logs_dir: str = "data/logs"
    code_output_dir: str = "data/code_output"


@dataclass
class VoiceConfig:
    enabled: bool = True
    stt_model_size: str = "base"
    # Model that listens for the wake word all day. It runs on every sentence heard in the room,
    # so it must be light: "tiny" is ~2x faster than "base" and leaves the CPU free for the chat model.
    wake_model_size: str = "tiny"
    tts_engine: str = "pyttsx3"
    language: str = "en"
    input_device: str | int | None = None
    # Wake word: say one of these names to start talking, no button needed
    wake_enabled: bool = True
    wake_phrases: list[str] = field(default_factory=list)  # empty = "hey <assistant name>", "<assistant name>"
    # Voice print: only enrolled voices (max 2) can wake the companion. Off until someone enrols.
    voiceprint_enabled: bool = True
    voiceprint_threshold: float = 0.5
    wake_sensitivity: float = 0.82  # 0.6 = triggers easily (more false alarms) .. 0.95 = must be exact
    # After the wake word, keep talking without repeating it until "bye" / "bas" or silence
    conversation: bool = True
    follow_up_seconds: float = 6.0
    # Words Whisper should expect (spellings for Roman Urdu, names). Your name is added automatically.
    # Speaking voice: an id from app.voice.voices (8 female + 4 male), speed and loudness
    tts_voice: str = "amy"
    tts_rate: float = 1.0      # 0.7 = slower .. 1.4 = faster
    tts_volume: float = 1.0    # 0.2 .. 1.0
    hint: str = ("Assalam-o-Alaikum. Roman Urdu and English: namaz, kholo, band karo, awaaz kam karo, "
                 "yaad dilao, kitne baje hain, theek hai, shukriya, Allah Hafiz.")


@dataclass
class SchedulerConfig:
    checkin_time: str = "09:00"
    wakeup_time: str = "05:30"
    wakeup_enabled: bool = False
    prayer_reminders_enabled: bool = True
    prayer_method: str = "KARACHI"
    madhab: str = "HANAFI"
    prayer_reminder_minutes_before: int = 10


@dataclass
class FocusConfig:
    default_minutes: int = 25
    distractions: list[str] = field(default_factory=lambda: ["youtube", "facebook", "instagram", "netflix"])


@dataclass
class NotificationsConfig:
    enabled: bool = True
    windows_toasts: bool = True      # anything Windows receives (WhatsApp Desktop, Outlook, Teams, browsers...)
    browser_tabs: bool = True        # unread counts in open browser tabs, e.g. "(3) WhatsApp" — works even
                                     # when a site's or the browser's notifications are switched off
    read_content: bool = True        # speak the message text, not just "new message from X"
    poll_seconds: float = 3.0
    ignore_apps: list[str] = field(default_factory=lambda: ["Microsoft.BingNews", "Widgets", "WindowsAlarms"])


@dataclass
class HotkeysConfig:
    enabled: bool = True
    translate: str = "<ctrl>+<alt>+t"   # translate selected text / text under the mouse
    talk: str = "<ctrl>+<alt>+<space>"  # start listening without the wake word
    open_window: str = "<ctrl>+<alt>+n"  # open Lyra's window


@dataclass
class ExpertConfig:
    provider: str = "local"            # "local" (Ollama, free) or "claude" (your own Anthropic credentials)
    claude_model: str = "claude-opus-5"
    effort: str = "high"               # low | medium | high | xhigh | max — lower = cheaper
    weekly_budget_usd: float = 5.0     # Claude spending cap per week (Monday-Sunday)
    local_model: str = ""              # "" = best installed local model
    route_technical: bool = True       # send web-dev / architecture questions to expert mode automatically


@dataclass
class CallsConfig:
    enabled: bool = True
    offer_recording: bool = True       # ask on every call whether to record and take notes
    debrief: bool = True               # talk the call through when it ends


@dataclass
class WhatsAppConfig:
    message_language: str = "roman_urdu"  # "roman_urdu" converts English dictation; "as_spoken" keeps it
    verify_contact: bool = True           # check the open chat's name on screen before sending


@dataclass
class AccessibilityConfig:
    voice_only_mode: bool = False
    auto_screenshot_on_error: bool = True
    context_memory_enabled: bool = True


@dataclass
class Config:
    user: UserConfig = field(default_factory=UserConfig)
    assistant: AssistantConfig = field(default_factory=AssistantConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    focus: FocusConfig = field(default_factory=FocusConfig)
    accessibility: AccessibilityConfig = field(default_factory=AccessibilityConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    hotkeys: HotkeysConfig = field(default_factory=HotkeysConfig)
    whatsapp: WhatsAppConfig = field(default_factory=WhatsAppConfig)
    expert: ExpertConfig = field(default_factory=ExpertConfig)
    calls: CallsConfig = field(default_factory=CallsConfig)
    routines: dict[str, list[str]] = field(default_factory=dict)
    features: dict[str, bool] = field(default_factory=dict)  # feature group -> enabled (missing = on)
    # platform -> [{label, browser, profile, google_index, email}]
    accounts: dict[str, list[dict]] = field(default_factory=dict)
    # spoken name -> {whatsapp: name as saved in WhatsApp, phone: "+92..."}
    contacts: dict[str, dict] = field(default_factory=dict)
    log_level: str = "INFO"

    def feature_on(self, name: str) -> bool:
        return bool(self.features.get(name, True))

    @property
    def wake_phrases(self) -> list[str]:
        """Configured wake phrases, or ones made from the assistant's name."""
        phrases = [p.strip() for p in (self.voice.wake_phrases or []) if str(p).strip()]
        if phrases:
            return phrases
        name = (self.assistant.name or APP_NAME).strip()
        return [f"hey {name}".lower(), name.lower()]

    def path(self, relative: str) -> Path:
        """Resolve a storage path relative to the project root and ensure its parent exists."""
        p = Path(relative)
        if not p.is_absolute():
            p = DATA_ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def tz(self):
        """The user's timezone as a tzinfo (the system timezone when set to 'local')."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        if self.user.timezone and self.user.timezone.lower() != "local":
            return ZoneInfo(self.user.timezone)
        return datetime.now().astimezone().tzinfo

    def dir(self, relative: str) -> Path:
        p = self.path(relative)
        p.mkdir(parents=True, exist_ok=True)
        return p


def _apply(section, values: dict) -> None:
    for key, value in (values or {}).items():
        if not hasattr(section, key):
            continue
        # a blank/null override must not wipe out a required value (e.g. max_tokens: null
        # saved by an older version of the Settings page) — keep the default instead
        if value is None and getattr(section, key) is not None:
            continue
        setattr(section, key, value)


def _apply_env(cfg: Config) -> None:
    env = os.environ.get
    cfg.user.name = env("USER_NAME", cfg.user.name)
    cfg.user.timezone = env("USER_TIMEZONE", cfg.user.timezone)
    cfg.llm.ollama_url = env("OLLAMA_URL", cfg.llm.ollama_url)
    cfg.llm.chat_model = env("OLLAMA_CHAT_MODEL", cfg.llm.chat_model)
    cfg.llm.embedding_model = env("OLLAMA_EMBED_MODEL", cfg.llm.embedding_model)
    cfg.storage.logs_dir = env("LOG_DIR", cfg.storage.logs_dir)
    cfg.voice.stt_model_size = env("STT_MODEL_SIZE", cfg.voice.stt_model_size)
    cfg.voice.tts_engine = env("TTS_ENGINE", cfg.voice.tts_engine)
    cfg.voice.enabled = _bool(env("ENABLE_VOICE"), cfg.voice.enabled)
    cfg.accessibility.voice_only_mode = _bool(env("PURE_VOICE_MODE"), cfg.accessibility.voice_only_mode)
    cfg.log_level = env("LOG_LEVEL", cfg.log_level)


def _upgrade_legacy(data: dict) -> dict:
    """Settings saved by the earlier "MD" release kept its old name as the assistant name and wake
    phrases; drop those so Lyra's own name (and wake word) apply. A custom name is kept."""
    assistant = data.get("assistant") or {}
    if str(assistant.get("name", "")).strip().lower() in LEGACY_NAMES:
        assistant.pop("name")
    voice = data.get("voice") or {}
    phrases = voice.get("wake_phrases")
    if isinstance(phrases, list) and phrases and all(
            str(p).lower().replace("hey ", "").replace("hello ", "").strip() in LEGACY_NAMES for p in phrases):
        voice.pop("wake_phrases")
    return data


def _apply_file(cfg: Config, path: Path) -> None:
    if not path.exists():
        return
    data = _upgrade_legacy(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    for name in SECTIONS:
        _apply(getattr(cfg, name), data.get(name, {}))
    for key in MAPPINGS:
        if key in data:
            value = data.get(key) or {}
            setattr(cfg, key, {**getattr(cfg, key), **value} if key == "features" else value)


def load_config(path: str | Path | None = None, use_settings: bool = True) -> Config:
    cfg = Config()
    if path:
        _apply_file(cfg, Path(path))
    else:  # an installed copy can be customised with a config.yaml in the data folder
        _apply_file(cfg, ROOT / "config.yaml")
        if DATA_ROOT != ROOT:
            _apply_file(cfg, DATA_ROOT / "config.yaml")
    if load_dotenv:
        load_dotenv(DATA_ROOT / ".env")
    _apply_env(cfg)
    if use_settings:
        _apply_file(cfg, SETTINGS_FILE)
    return cfg


def read_settings() -> dict:
    if SETTINGS_FILE.exists():
        return yaml.safe_load(SETTINGS_FILE.read_text(encoding="utf-8")) or {}
    return {}


def save_settings(changes: dict, remove: dict[str, list[str]] | None = None) -> Config:
    """Merge `changes` ({section: {key: value}} or a MAPPINGS key) into data/settings.yaml,
    drop the overrides listed in `remove` ({section: [keys]} — back to the config.yaml/default
    value), and reload the live configuration in place."""
    current = read_settings()
    for key, value in changes.items():
        if key in SECTIONS and isinstance(value, dict):
            current.setdefault(key, {}).update(value)
        elif key in MAPPINGS:
            current[key] = value
    for section, keys in (remove or {}).items():
        for key in keys:
            (current.get(section) or {}).pop(key, None)
    # clean out nulls left by older versions, and empty sections
    for section in SECTIONS:
        if isinstance(current.get(section), dict):
            current[section] = {k: v for k, v in current[section].items() if v is not None}
            if not current[section]:
                current.pop(section)
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    # write to a temporary file first so a crash mid-write can't leave a half-written settings file
    tmp = SETTINGS_FILE.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(current, allow_unicode=True, sort_keys=False), encoding="utf-8")
    os.replace(tmp, SETTINGS_FILE)
    return reload_config()


def reload_config() -> Config:
    """Re-read all config files, updating the existing Config object so every holder sees the change."""
    fresh = load_config()
    cfg = get_config()
    cfg.__dict__.update(fresh.__dict__)
    return cfg


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config
