"""Turn what the user says or types into an intent.

Fast regex rules handle common phrasings (English and Roman Urdu). When a sentence
looks like a command but no rule matches, the local model classifies it. Anything
else is ordinary conversation ("chat").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.llm import LLMError, get_llm
from app.logger import get_logger

log = get_logger(__name__)


@dataclass
class Intent:
    name: str
    args: dict = field(default_factory=dict)
    text: str = ""

    @property
    def is_chat(self) -> bool:
        return self.name == "chat"


_POLITE = re.compile(
    r"^(hey|hi|ok|okay|so|please|kindly|can you|could you|would you|will you|i want you to|i want to|i'd like to|"
    r"zara|jaldi|plz|pls|companion|assistant|computer)[\s,]+", re.I)
_TRAILING = re.compile(r"[\s,]*(please|for me|now|right now|zara|plz|pls|kar do|kardo|karo)[\s.!?]*$", re.I)


def normalize(text: str) -> str:
    t = text.strip().rstrip(".!")
    prev = None
    while prev != t:
        prev = t
        t = _POLITE.sub("", t).strip()
        t = _TRAILING.sub("", t).strip()
    return t


def _minutes(amount: str, unit: str) -> float:
    n = float(amount)
    return n * 60 if unit.lower().startswith(("h", "ghant")) else n


UNIT = r"(minutes?|mins?|hours?|hrs?|ghante|ghanta|minat|mint)"
SAYING = r"\s*(?:,\s*)?(?:\s+(?:saying|that|to say|and say|and tell (?:him|her|them)|ke|ki|message)\s+|\s*:\s*)"
CODE_WORDS = r"(code|program|function|script|class|app|html page|web ?page|website|algorithm|query)"

# (pattern, intent, arg-builder). Order matters: first match wins.
RULES: list[tuple[re.Pattern, str, callable]] = [(re.compile(p, re.I), name, fn) for p, name, fn in [
    (r"^(help|what can you do|commands|madad)$", "help", lambda m: {}),
    (r"^(?:say|bolo)\s*:\s*(.+)", "say", lambda m: {"text": m[1]}),
    # ---- self-diagnosis ----
    (r"^(?:diagnose|check|test|heal|fix) (?:yourself|your ?self|md|the companion|the system)|^self[- ]?(?:check|test|heal|diagnos\w*)|"
     r"^(?:kya masla hai|apna check karo|khud ko check karo|khud ko theek karo)|^(?:run )?(?:a )?health check|^what'?s wrong with you",
     "diagnose", lambda m: {}),

    # ---- expert / research / projects ----
    (r"^(?:give me |make |create |write |design )?(?:an? )?(?:blueprint|architecture|system design|tech stack|project plan)"
     r"(?: for| of)? (.+)", "expert", lambda m: {"question": m[0]}),
    (r"^(?:design|plan) (?:a |an |the )?(?:database|db|schema|data model|api|backend|ui|ux|landing page|website|web app)\b.*",
     "expert", lambda m: {"question": m[0]}),
    (r"^(?:expert|as an expert|pro mode)[:,]?\s+(.+)", "expert", lambda m: {"question": m[1]}),
    (r"^(?:research|do research on|investigate|deep dive into|research karo)\s+(.+)", "research", lambda m: {"topic": m[1]}),
    (r"^(?:create|make|start|scaffold|set up|setup|new|banao)\s+(?:a |an |new )?(next\.?js|nextjs|react|vue|astro)\s+"
     r"(?:app|project|site|website)?\s*(?:called|named|with the name)?\s*([\w-]+)?$", "create_project",
     lambda m: {"kind": m[1], "name": m[2]}),

    # ---- installing software ----
    (r"^(?:install|download and install|get)\s+(.+?)(?:\s+from (?:the )?(?:microsoft )?store| app)?$|^(.+?) install (?:karo|kar do)$",
     "install_app", lambda m: {"name": m[1] or m[2], "store": "store" in m[0].lower()}),

    # ---- files in Explorer, browser tabs ----
    (r"^(?:show|open|locate|find)\s+(.+?)\s+in (?:the )?(?:file )?explorer$|^(?:open|show) (?:the )?folder (?:of|for|containing) (.+)",
     "show_in_explorer", lambda m: {"query": m[1] or m[2]}),
    (r"^(?:switch|go|change|jump) to (?:the )?(.+?) tab$|^(.+?) (?:wala |ka )?tab (?:kholo|dikhao)$", "switch_tab",
     lambda m: {"name": m[1] or m[2]}),

    # ---- pointer & on-screen guidance ----
    (r"^(?:what(?:'s| is) (?:this|that|under the (?:mouse|cursor|pointer))|what am i pointing at|what is the mouse on|"
     r"(?:yeh|ye) kya hai|mouse ke (?:neeche|niche) kya hai|is (?:par|pe) kya hai)\??$", "describe_pointer", lambda m: {}),
    (r"^(?:show me|where is|where'?s|point to|highlight|find)\s+(?:the )?(.+?)(?: button| link| option| menu| tab| box)?\??$|"
     r"^(.+?) kahan hai\??$", "show_control", lambda m: {"query": m[1] or m[2]}),
    (r"^(?:click|tap|select)(?: on)? (?:the )?(.+?)(?: button| link| option| menu item)?$|^(.+?) (?:par|pe) click(?: karo| kar do)?$",
     "click_control", lambda m: {"query": m[1] or m[2]}),
    (r"^(?:start|turn on|enable) mouse reading|^read (?:what(?:'s| is) )?under the mouse$|^mouse reading (?:on|chalu)",
     "mouse_reading", lambda m: {"on": True}),
    (r"^(?:stop|turn off|disable) mouse reading|^mouse reading (?:off|band)", "mouse_reading", lambda m: {"on": False}),

    # ---- voice print & calls ----
    (r"^(?:enrol+|register|save|learn|remember) my voice|^meri awaaz (?:yaad|save|pehchaan)", "enroll_voice", lambda m: {}),
    (r"^(?:record|start recording) (?:this|the) call|^call record karo", "record_call", lambda m: {"on": True}),
    (r"^stop recording(?: the call)?$|^recording band karo", "record_call", lambda m: {"on": False}),
    (r"^(?:last |latest )?call (?:notes|summary)|^(?:what happened|summari[sz]e) (?:on|in) the (?:last )?call", "call_notes",
     lambda m: {}),
    # ---- WhatsApp (before the generic open/close rules) ----
    (rf"^(?:send |bhejo )?(?:an? )?(?:whatsapp|whats app)(?: message)? to (.+?)(?:{SAYING}(.+))?$", "send_whatsapp",
     lambda m: {"contact": m[1], "message": m[2]}),
    (rf"^(?:message|text|msg) (.+?) on (?:whatsapp|whats app)(?:{SAYING}(.+))?$", "send_whatsapp",
     lambda m: {"contact": m[1], "message": m[2]}),
    (rf"^(?:send |message |text )(.+?) (?:an? )?(?:whatsapp|whats app)(?: message)?(?:{SAYING}(.+))?$", "send_whatsapp",
     lambda m: {"contact": m[1], "message": m[2]}),
    (rf"^(?:open |go to )?(?:whatsapp|whats app)(?:,| and| then)? (?:go to |open )?(.+?)(?:'s)? chat(?:,| and| then)* (?:and )?"
     rf"(?:send|say|tell|write)(?: (?:him|her|them))?(?: a message)?(?:{SAYING}|:?\s+)(.+)$",
     "send_whatsapp", lambda m: {"contact": m[1], "message": m[2]}),
    (rf"^(?:open )?(?:whatsapp|whats app)(?:,| and)? (?:send|message) (.+?)(?: a message)?(?:{SAYING}(.+))?$", "send_whatsapp",
     lambda m: {"contact": m[1], "message": m[2]}),
    (r"^(.+?) ko (?:whatsapp|whats app|message|msg)(?: par| pe)?(?: message| msg)? (?:karo|kar do|bhejo|bhej do|karna|kar)"
     r"(?:\s+(?:ke|ki|keh do ke|keh do ki|:)\s*(.+))?$", "send_whatsapp", lambda m: {"contact": m[1], "message": m[2]}),
    (r"^(.+?) ko (?:whatsapp |whats app )?(?:par |pe )?(?:bolo|likho|bata do|batao|keh do|kaho) (?:ke |ki )?(.+)$", "send_whatsapp",
     lambda m: {"contact": m[1], "message": m[2]}),
    (r"^(?:open |show )?(.+?)(?:'s| ki| ka)? (?:whatsapp )?chat (?:open|kholo|khol do|dikhao)$|^open (?:the )?(?:whatsapp )?chat (?:with|of) (.+)$",
     "whatsapp_chat", lambda m: {"contact": m[1] or m[2]}),

    # ---- translation ----
    (r"^(?:translate|tarjuma)(?: karo| kar do| kardo)?(?: this| it| the selected text| selection| yeh| ye| isko| is ko| iska| screen)?"
     r"(?: (?:into|to|in) roman urdu)?$|^(?:yeh|ye|is|iss|isko|isse) (?:kya likha hai|ka matlab(?: kya hai)?|ka tarjuma(?: karo)?)|"
     r"what does (?:this|that) (?:say|mean)|^iska matlab(?: kya hai)?|^explain this text$",
     "translate_screen", lambda m: {}),
    (r"^(?:translate|tarjuma karo):? (.+?)(?: (?:into|to|in) roman urdu)?$", "translate_text", lambda m: {"text": m[1]}),
    (r"^(?:wait|ruko) (\d+(?:\.\d+)?)(?: ?(?:seconds?|secs?|s|second))?$", "wait", lambda m: {"seconds": float(m[1])}),
    (r"^(repeat( that)?|say (that|it) again|dobara bolo|phir se bolo|what did you say)$", "repeat", lambda m: {}),
    (r"^what did i (just )?(do|ask|say)|^(recent|my) activity", "recent_activity", lambda m: {}),

    (rf"remind me (?:in|after) (\d+(?:\.\d+)?) {UNIT} (?:to |that |about )?(.+)", "remind",
     lambda m: {"minutes": _minutes(m[1], m[2]), "text": m[3]}),
    (rf"remind me (?:to |that |about )?(.+?) (?:in|after) (\d+(?:\.\d+)?) {UNIT}$", "remind",
     lambda m: {"minutes": _minutes(m[2], m[3]), "text": m[1]}),
    (rf"(?:set |add )?(?:a )?reminder (?:for |to |about )?(.+?) (?:in|after) (\d+(?:\.\d+)?) {UNIT}$", "remind",
     lambda m: {"minutes": _minutes(m[2], m[3]), "text": m[1]}),
    (rf"^(\d+(?:\.\d+)?) {UNIT} (?:baad|bad) (?:yaad dilana|yaad dila do|remind karna)?\s*(.+)", "remind",
     lambda m: {"minutes": _minutes(m[1], m[2]), "text": m[3]}),

    (r"(prayer|namaz|namaaz|salah|salat|azan|adhan)", "prayer", lambda m: {}),
    (r"^(stop|end|cancel|finish) (the )?focus|focus (off|stop|band)", "focus_stop", lambda m: {}),
    (r"^(start |begin )?focus( mode| session| time)?(?: for (\d+) ?(?:minutes?|mins?))?(?: on (.+))?$", "focus_start",
     lambda m: {"minutes": int(m[3]) if m[3] else None, "task": m[4] or ""}),

    # ---- settings by voice (no need to open Settings) ----
    (r"^(?:change|switch|set) (?:your |the )?voice to (?:a |the )?([a-z]+)(?: voice)?$|"
     r"^(?:use|speak (?:with|in)) (?:a |the )?([a-z]+)(?:'?s)? voice$|"
     r"^([a-z]+) (?:ki|wali|jaisi) (?:awaaz|aawaz|awaz)(?: (?:lagao|karo|use karo|mein bolo))?$", "set_voice",
     lambda m: {"name": m[1] or m[2] or m[3]}),
    (r"^(?:what|which) voices? (?:do you have|can you use|are there)|^(?:list|show)(?: me)? (?:your |the )?voices$|"
     r"^(?:kaun kaun si|konsi) (?:awaazein|awaaz) hain", "list_voices", lambda m: {}),
    (r"^(?:your (?:new )?name (?:is|will be)|i'?ll call you|change your name to|call yourself|rename yourself(?: to)?|"
     r"tumhara naam(?: ab)?) ([a-z][\w .-]{0,20}?)(?: hai| ho| from now on)?$", "set_assistant_name", lambda m: {"name": m[1]}),
    (r"^(?:call me|my name is|mera naam) ([a-z][\w .-]{0,25}?)(?: hai)?$", "set_user_name", lambda m: {"name": m[1]}),
    (r"^(?:i (?:live|stay) in|i'?m (?:living|staying) in|my city is|set my (?:city|location) to|main) (.+?)(?: now)?"
     r"(?: mein rehta hoon| mein rehti hoon| mein hoon| se hoon)?$", "set_city", lambda m: {"city": m[1]}),
    (r"^(?:turn|switch) (on|off) (?:the )?(.+?)(?: feature)?$|^(enable|disable) (?:the )?(.+?)(?: feature)?$|"
     r"^(.+?) (on|off|chalu)(?: karo| kar do)?$", "toggle_feature",
     lambda m: {"on": (m[1] or m[3] or m[6] or "").lower() in ("on", "enable", "chalu"), "name": m[2] or m[4] or m[5]}),
    (r"^(?:open|show)(?: me)? (?:your |lyra'?s |the |my )?(window|settings|chat|home|memory|commands|routines|accounts|"
     r"voices?)(?: window| page| settings)?$|"
     r"^(?:settings|window) (?:kholo|dikhao)$", "show_window", lambda m: {"tab": (m[1] or "home").lower()}),
    (r"^(?:stop|pause) listening(?: for (\d+) ?(?:minutes?|mins?))?$|^(?:go to sleep|so jao|chup ho jao)$", "pause_listening",
     lambda m: {"minutes": int(m[1]) if m[1] else 0}),
    # ---- updating memory and goals by voice (before add_goal / remember) ----
    (r"^(?:change|update|replace|switch|edit) (?:my )?goal (?:from |of )?(.+ (?:to|with|into) .+)$", "update_goal",
     lambda m: {"pair": m[1]}),
    (r"^(?:my (?:new )?goal is now|my goal is no longer .+?,? (?:it'?s|now it'?s|instead it'?s)|now my goal is) (?:to )?(.+)$|"
     r"^mera goal ab (.+?)(?: hai| he)?$|^ab mera goal (.+?)(?: hai| he)?$", "update_goal",
     lambda m: {"old": None, "new": m[1] or m[2] or m[3]}),
    (r"^(?:remove|delete|drop|cancel|forget) (?:my |the )?goal (?:to |of |about |called )?(.+)$|^(.+?) (?:wala |ka )?goal (?:hata|khatam|delete) (?:do|karo|kar do)$",
     "remove_goal", lambda m: {"query": m[1] or m[2]}),
    (r"^(?:i (?:have |'ve )?(?:finished|completed|achieved|done|reached)|mark (?:my )?goal) (?:my goal (?:to |of )?)?(.+?)(?: as done| as complete)?$|"
     r"^(.+?) (?:ho gaya|poora ho gaya|mukammal ho gaya)$", "done_goal", lambda m: {"query": m[1] or m[2]}),
    (r"^(?:what (?:was|were) my (?:old|previous|last) goals?|what did i change|what have you updated|show (?:my )?memory changes)\??$",
     "memory_changes", lambda m: {}),
    (r"^forget (?:that |the fact that |about |everything about )?(?!my goal)(.+)$|^(.+?) (?:bhool jao|bhula do)$|"
     r"^(?:remove|delete|erase) (?:the |your )?(?:memory|fact|note|what you (?:know|remember)) (?:about |that |of )?(.+)$",
     "forget_memory", lambda m: {"query": m[1] or m[2] or m[3]}),
    (r"^(?:actually|correction|update|that'?s wrong|that is wrong|galat hai)[:,]?\s+(.+)$", "remember",
     lambda m: {"fact": m[1]}),
    (r"^(?:add|set|new|create) (?:a )?goal(?: to| of|:)? (.+)|^my (?:new )?goal is (?:to )?(.+)", "add_goal",
     lambda m: {"title": m[1] or m[2]}),
    (r"^(what are |show |list |tell me )?(my )?goals\??$", "list_goals", lambda m: {}),
    (r"^remember (?:that )?(.+)|^yaad rakho (?:ke |ki )?(.+)", "remember", lambda m: {"fact": m[1] or m[2]}),
    (r"what do you (know|remember) about me|mere baare mein kya", "list_memories", lambda m: {}),

    (r"^(what('s| is) the )?time( is it)?\??$|what time is it|kitne baje|time kya hua", "time", lambda m: {}),
    (r"^(what('s| is) )?(today'?s |the )?date( today)?\??$|what day is (it|today)|aaj (kya|kaunsi) (date|tareekh)",
     "date", lambda m: {}),
    (r"(battery|system status|cpu|how is my (computer|laptop|pc))", "system_status", lambda m: {}),
    (r"^(morning (summary|brief)|brief me|daily (summary|brief)|what'?s my day)", "morning", lambda m: {}),
    (r"^(give me (some )?advice|(some |any )?advice$|what should i do( now| next)?|motivate me|kya karoon|kya karun)",
     "advice", lambda m: {}),

    (r"\b(take|save) (a )?screenshot|\bscreen ?shot\b", "screenshot", lambda m: {}),
    (r"^(read|padho|parho) (?:the |my |this )?(?:whole |full |entire )?(screen|window|page)|screen (padho|parho)",
     "read_screen", lambda m: {"full": "whole" in m[0] or "full" in m[0] or "entire" in m[0]}),
    (r"(describe|explain) (the |my |this )?screen|what (do you see|am i looking at)|what('s| is) on (my |the )?screen|"
     r"screen (par|pe) kya", "describe_screen", lambda m: {}),
    (r"^read (the |my )?clipboard", "read_clipboard", lambda m: {}),

    (r"^(?:summari[sz]e|read) (?:this |the )?(?:page|website|article)?\s*(https?://\S+)", "summarize_page",
     lambda m: {"url": m[1]}),
    (r"^(?:search|look up|find) (?:the )?(?:web|internet|online) (?:for )?(.+)|^(?:search|look up) (.+?) online$|"
     r"^(?:internet|net) (?:par|pe) (?:dekho|search karo) (.+)", "web_answer", lambda m: {"query": m[1] or m[2] or m[3]}),
    (r"^(?:search|find) (?:on )?youtube (?:for )?(.+)|^play (.+?) on youtube$|^youtube (?:par|pe) (.+)", "search",
     lambda m: {"query": m[1] or m[2] or m[3], "engine": "youtube"}),
    (r"^(?:search|google|look up)(?: for| google for)? (.+)", "search", lambda m: {"query": m[1], "engine": "google"}),

    (rf"^(?:write|create|generate|make|build|likho|banao)\b.*\b{CODE_WORDS}\b.*", "generate_code", lambda m: {"request": m[0]}),
    (r"^debug (.+\.(?:py|js|ps1))$", "debug_file", lambda m: {"path": m[1]}),
    (r"^git (status|log)$", "git", lambda m: {"action": m[1].lower()}),

    (r"^(?:take|make|save|write) (?:a )?note(?: that|:)? (.+)|^note(?: down)?:? (.+)", "note",
     lambda m: {"text": m[1] or m[2]}),
    (r"^(?:what'?s|what is|list|show)(?: me)?(?: in| on)? (?:my |the )?(desktop|documents|downloads|pictures|music|videos|projects)(?: folder)?\??$",
     "list_folder", lambda m: {"folder": m[1]}),
    (r"^(?:find|search for|locate) (?:the |my )?file (?:called |named )?(.+)", "find_file", lambda m: {"query": m[1]}),
    (r"^read (?:the |my )?file (?:called |named )?(.+)", "read_file", lambda m: {"path": m[1]}),

    (r"^(?:type|write down|type out)(?: this)?:? (.+)", "type_text", lambda m: {"text": m[1]}),
    (r"^(?:press|hit)(?: the)? (.+?)(?: key)?$", "press_keys", lambda m: {"keys": m[1]}),
    (r"(?:volume|sound|awaaz|awaz) (up|down|badhao|tez|zyada|kam|mute)|^(mute|unmute)$", "volume",
     lambda m: {"direction": {"badhao": "up", "tez": "up", "zyada": "up", "kam": "down", "unmute": "mute"}.get(
         (m[1] or m[2]).lower(), (m[1] or m[2]).lower())}),
    (r"^(?:switch|go|change) to (?:the )?(.+?)(?: window)?$", "switch_window", lambda m: {"name": m[1]}),
    (r"(what|which) windows are open|list (the )?windows|open windows", "list_windows", lambda m: {}),
    (r"^(?:close|quit|band karo|band kar do) (.+)|^(.+?) band(?: karo| kar do)?$", "close_app", lambda m: {"name": m[1] or m[2]}),
    (r"^(?:run|execute)(?: the)?(?: command| cmd)?:? (.+)|^(?:cmd|powershell|command):? (.+)", "run_command",
     lambda m: {"command": m[1] or m[2]}),
    (r"^(?:open|launch|start|chalao|kholo|khol do|go to|visit) (.+)|^(.+?) (?:kholo|khol do|chalao|open karo)$", "open",
     lambda m: {"target": m[1] or m[2]}),
]]

INTENT_NAMES = sorted({name for _, name, _ in RULES} | {"chat"})

ACTION_HINTS = re.compile(
    r"\b(open|launch|close|play|search|remind|type|press|screen|file|folder|volume|goal|note|focus|prayer|"
    r"screenshot|window|battery|kholo|band|chalao|dikhao|likho|yaad|awaaz|padho|parho|whatsapp|message|"
    r"translate|tarjuma|matlab|bhejo)\b", re.I)

CLASSIFY_SYSTEM = (
    "Classify the user's request for a desktop voice assistant. Reply ONLY with JSON "
    '{"intent": "<one of: ' + ", ".join(INTENT_NAMES) + '>", "args": {...}}. '
    "Arg names: open{target}, close_app{name}, search{query,engine}, web_answer{query}, remind{minutes,text}, "
    "type_text{text}, press_keys{keys}, volume{direction}, add_goal{title}, remember{fact}, note{text}, "
    "focus_start{minutes,task}, generate_code{request}, list_folder{folder}, find_file{query}, read_file{path}, "
    "switch_window{name}, run_command{command}, send_whatsapp{contact,message}, whatsapp_chat{contact}, "
    "translate_screen{}, translate_text{text}, expert{question}, research{topic}, install_app{name}, "
    "show_control{query}, click_control{query}, describe_pointer{}, switch_tab{name}, diagnose{}. Use \"chat\" for questions, conversation or anything else."
)


def rule_match(text: str) -> Intent | None:
    t = normalize(text)
    for pattern, name, build in RULES:
        m = pattern.search(t)
        if m:
            try:
                return Intent(name, build(m), text)
            except (TypeError, ValueError, IndexError) as e:
                log.debug("Rule %s failed on %r: %s", name, t, e)
    return None


def llm_classify(text: str) -> Intent:
    try:
        data = get_llm().ask_json(text, system=CLASSIFY_SYSTEM, max_tokens=80)
    except LLMError:
        return Intent("chat", {}, text)
    if isinstance(data, dict) and data.get("intent") in INTENT_NAMES:
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        return Intent(data["intent"], args, text)
    return Intent("chat", {}, text)


def parse(text: str, use_llm: bool = True) -> Intent:
    text = text.strip()
    intent = rule_match(text)
    if intent:
        return intent
    if use_llm and len(text.split()) <= 20 and ACTION_HINTS.search(text) and not text.endswith("?"):
        intent = llm_classify(text)
        log.info("LLM classified %r as %s %s", text, intent.name, intent.args)
        return intent
    return Intent("chat", {}, text)
