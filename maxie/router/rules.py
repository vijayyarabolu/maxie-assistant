"""Local instant rules: regexes for the very common commands. No model, no network, ~0 ms.

`match()` is strict (the whole command must fit a pattern) and is what runs first.
`extract()` is lenient: once a classifier has said "this is a Mac action", it pulls the
action and its arguments out of looser phrasing ("could you put on some jazz").
Maths and dates are answered in code, never by a model.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import dataclass, field

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "twenty five": 25,
    "thirty": 30, "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90,
}
APP_ALIASES = {
    "mail": "Mail", "email": "Mail", "my email": "Mail", "calendar": "Calendar", "my calendar": "Calendar",
    "music": "Music", "apple music": "Music", "notes": "Notes", "messages": "Messages", "imessage": "Messages",
    "safari": "Safari", "chrome": "Google Chrome", "google chrome": "Google Chrome", "spotify": "Spotify",
    "slack": "Slack", "terminal": "Terminal", "finder": "Finder", "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code", "code": "Visual Studio Code", "zoom": "zoom.us", "settings": "System Settings",
    "system settings": "System Settings", "reminders": "Reminders", "photos": "Photos", "maps": "Maps",
    "facetime": "FaceTime", "xcode": "Xcode", "preview": "Preview", "cursor": "Cursor", "notion": "Notion",
}
SITES = {
    "gmail": "https://mail.google.com", "youtube": "https://www.youtube.com", "linkedin": "https://www.linkedin.com",
    "github": "https://github.com", "google drive": "https://drive.google.com", "drive": "https://drive.google.com",
    "google calendar": "https://calendar.google.com", "indeed": "https://www.indeed.com",
}


@dataclass
class RuleHit:
    route: str
    action: str
    args: dict = field(default_factory=dict)


POLITE = re.compile(r"^(?:hey |ok |okay )?(?:(?:maxie|maxi|max)[,]? )?(?:(?:can|could|would) you |please |pls )?(.*?)(?: please| for me| now| thanks)*[.!?]*$", re.I)


def normalize(text: str, keep_case: bool = False) -> str:
    t = " ".join(text.strip().split())
    t = POLITE.match(t).group(1) if POLITE.match(t) else t
    return t.strip() if keep_case else t.strip().lower()


def _num(s: str) -> float | None:
    s = s.strip().lower()
    if s in ("half an", "half a"):
        return 0.5
    if s in NUMBER_WORDS:
        return float(NUMBER_WORDS[s])
    try:
        return float(s)
    except ValueError:
        return None


# ---- maths (safe: arithmetic only) -------------------------------------------------------------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.Mod: operator.mod}


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("too big")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("not arithmetic")


def calc(expr: str) -> float | None:
    e = expr.lower().replace("x", "*").replace("×", "*").replace("÷", "/").replace("^", "**")
    for word, sym in (("plus", "+"), ("minus", "-"), ("times", "*"), ("multiplied by", "*"),
                      ("divided by", "/"), ("over", "/")):
        e = e.replace(word, sym)
    e = e.replace(",", "")
    if not re.fullmatch(r"[\d\s.+\-*/()%]+", e) or not re.search(r"\d", e) or not re.search(r"[+\-*/%]", e):
        return None
    try:
        return float(_eval(ast.parse(e, mode="eval").body))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
        return None


def fmt_number(x: float) -> str:
    return f"{x:,.0f}" if x == int(x) else f"{x:,.4f}".rstrip("0").rstrip(".")


# ---- strict rules --------------------------------------------------------------------------------

UNIT = r"(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)"
NUM = r"(?P<n>\d+(?:\.\d+)?|half an|half a|[a-z]+(?: five)?)"
TIMER = [
    re.compile(rf"^(?:set|start|make)(?: me)?(?: a| an)? {NUM}[ -]{UNIT}(?: long)? timer(?: for (?P<label>.+))?$"),
    re.compile(rf"^(?:set |start )?(?:a )?timer (?:for )?{NUM} {UNIT}(?: for (?P<label>.+))?$"),
    re.compile(rf"^remind me in {NUM} {UNIT} to (?P<label>.+)$"),
]
MUSIC = [
    (re.compile(r"^(?:pause|stop)(?: the)? (?:music|song|playback)$|^pause$"), "pause"),
    (re.compile(r"^(?:next|skip)(?: this)?(?: song| track)?$|^(?:play )?(?:the )?next (?:song|track)$"), "next"),
    (re.compile(r"^(?:previous|last|go back a)(?: song| track)?$|^play (?:the )?previous (?:song|track)$"), "previous"),
    (re.compile(r"^(?:resume|unpause)(?: the)?(?: music)?$|^play music$"), "play"),
]
VOLUME = [
    (re.compile(r"^(?:volume up|turn (?:it|the volume|the music) up|louder|turn up the (?:volume|music))$"), "up"),
    (re.compile(r"^(?:volume down|turn (?:it|the volume|the music) down|quieter|softer|turn down the (?:volume|music))$"), "down"),
    (re.compile(r"^(?:mute|mute (?:the )?(?:volume|sound|mac|audio))$"), "mute"),
]
VOLUME_SET = re.compile(r"^(?:set (?:the )?)?volume (?:to |at )?(?P<n>\d{1,3})(?: ?%| percent)?$")
DND = re.compile(r"^(?:turn |switch )?(?P<a>on|off)? ?(?:do not disturb|dnd|focus mode)(?: (?P<b>on|off))?$")
OPEN = re.compile(r"^(?:open|launch|start|bring up|pull up) (?:up )?(?:the )?(?P<name>.+?)(?: app)?$")
PLAY = re.compile(r"^(?:play|put on) (?P<q>.+)$")
STOP = re.compile(r"^(?:stop|cancel|cancel that|shut up|be quiet|never ?mind|nevermind|forget it|stop talking|abort)$")
REMEMBER = re.compile(r"^(?:remember|note|keep in mind) (?:that )?(?P<fact>.+)$")
FORGET = re.compile(r"^forget (?:about |that )?(?P<what>.+)$")
SHOW_MEMORY = re.compile(r"^what do you (?:know|remember) about me$")
# Watch me once (maxie.flows): start / stop watching, and replay a flow by its name.
WATCH_ON = re.compile(r"^(?:watch me(?: once| do (?:this|it|something))?|watch (?:and|then) learn|learn this|record (?:this|a flow))$")
WATCH_OFF = re.compile(r"^(?:(?:i'?m |i am )?done watching|stop watching(?: me)?|(?:ok |okay )?(?:that'?s it|done),? (?:stop watching|you can stop))$")
RUN_FLOW = re.compile(r"^(?:run|do|replay|redo|play back) (?:the |my )?(?P<name>.+?) flow(?: again)?$")
RECALL = re.compile(r"^(?:what (?:did|do) (?:i|we) (?:decide|say|think|tell you) (?:about|on|regarding) "
                    r"|what do you (?:know|remember) about |do you remember (?:anything about |what i said about )?"
                    r"|what did i tell you about )(?P<q>.+?)[?. ]*$")
BRIEF = re.compile(r"^(?:brief me|(?:give me |read me )?(?:my |the )?(?:morning )?brief(?:ing)?|what'?s my day look like)$")
WRAP = re.compile(r"^(?:wrap(?: me)? up|(?:give me )?(?:my |the )?(?:evening |daily |night )?wrap(?: ?up)?)$")
MUTE_PROACTIVE = re.compile(r"^(?:mute|pause|silence) (?:notifications|alerts|proactive|briefs)$")
TIME = re.compile(r"^what(?:'s| is)? (?:the )?time(?: is it)?(?: now)?$|^what time is it$")
DATE = re.compile(r"^what(?:'s| is)? (?:the |today'?s )?date(?: today)?$|^what day is (?:it|today)$")
MATH = re.compile(r"^(?:what(?:'s|s| is)|calculate|compute|how much is) (?P<e>.+?)\??$")
PERCENT = re.compile(r"^(?:what(?:'s|s| is) )?(?P<p>\d+(?:\.\d+)?) ?(?:%|percent) of (?P<n>[\d,]+(?:\.\d+)?)\??$")


# Phone that acts: reminders, message drafts and directions run without a model. From the
# iPhone they come back as phone actions the Shortcut runs; on the Mac they use the Mac's own apps.
WHEN = r"(?P<when>(?:at|on|by|tomorrow|tonight|this|next|every|before|after|in the) .+?)"
REMIND = re.compile(rf"^remind me to (?P<text>.+?)(?: {WHEN})?[.!?]*$")
NOT_A_NAME = {"me", "us", "him", "her", "them", "it", "you", "everyone", "from", "to", "the", "a", "my"}
MESSAGE = re.compile(r"^(?i:text|message|imessage|tell) (?P<to>[A-Z][\w'-]*(?: (?!I\b|I')[A-Z][\w'-]*)?)(?: that)?[,:]? (?P<body>.+?)[.!]*$")
NAVIGATE = re.compile(r"^(?:directions|navigate|get me directions|give me directions|take me|drive me|how do i get) "
                      r"(?:to )?(?P<dest>.+?)[.!?]*$")


def _phone_ready(cased: str) -> RuleHit | None:
    """Reminder / message draft / directions from his words; `cased` keeps names and text as said."""
    low = cased.lower()
    if m := REMIND.match(low):
        text = cased[m.start("text"):m.end("text")]
        when = cased[m.start("when"):m.end("when")] if m["when"] else ""
        return RuleHit("mac_action", "add_reminder", {"text": text, "when": when})
    if (m := MESSAGE.match(cased)) and m["to"].split()[0].lower() not in NOT_A_NAME:
        return RuleHit("mac_action", "draft_message", {"to": m["to"], "text": m["body"]})
    if m := NAVIGATE.match(low):
        dest = cased[m.start("dest"):m.end("dest")]
        from urllib.parse import quote
        return RuleHit("mac_action", "open_url", {"url": f"https://maps.apple.com/?daddr={quote(dest)}",
                                                  "label": f"Directions to {dest}."})
    return None


def _seconds_to_minutes(n: float, unit: str) -> float:
    return n / 60 if unit.startswith("s") else n * 60 if unit.startswith("h") else n


def _timer(t: str) -> RuleHit | None:
    for pat in TIMER:
        m = pat.match(t)
        if m and (n := _num(m["n"])) is not None:
            return RuleHit("mac_action", "timer",
                           {"minutes": _seconds_to_minutes(n, m["unit"]), "label": (m["label"] or "").strip()})
    return None


FOLDERS = {"downloads": "~/Downloads", "download": "~/Downloads", "documents": "~/Documents", "document": "~/Documents",
           "desktop": "~/Desktop"}


def _open(name: str) -> RuleHit | None:
    name = name.strip()
    if m := re.fullmatch(r"(?:my |the )?(downloads?|documents?|desktop)(?: folder)?", name):
        return RuleHit("mac_action", "open_file", {"path": FOLDERS[m.group(1)]})
    if name in SITES:
        return RuleHit("mac_action", "open_url", {"url": SITES[name]})
    if re.fullmatch(r"[\w.-]+\.(?:com|org|net|io|dev|ai|edu)(?:/\S*)?", name):
        return RuleHit("mac_action", "open_url", {"url": "https://" + name})
    if name in APP_ALIASES:
        return RuleHit("mac_action", "open_app", {"name": APP_ALIASES[name]})
    if len(name.split()) <= 3 and not re.search(r"\b(my|the|a|an|email|file|document|folder|from|about|that)\b", name):
        return RuleHit("mac_action", "open_app", {"name": name.title()})
    return None


# "find the address Riley texted me", "what's the number Morgan sent me": answered from local Messages, no model.
FIND_IN_MESSAGES = re.compile(
    r"^(?:find|get|show|look up|what(?:'s| is| was)|where(?:'s| is))?\s*(?:me\s+)?(?:the\s+)?"
    r"(?P<kind>address|phone number|number|phone|link|url|email address|email|code)\s+"
    r"(?P<who>[A-Za-z][A-Za-z'-]*(?: [A-Z][A-Za-z'-]*)?)\s+(?:texted|messaged|sent|imessaged|gave)\b(?:\s+me)?"
    r"(?:\s+(?:yesterday|today|last \w+|earlier|this \w+))?\s*[?.!]*$", re.I)
FIND_KIND = {"address": "address", "phone number": "phone", "number": "phone", "phone": "phone", "link": "link",
             "url": "link", "email address": "email", "email": "email", "code": "code"}


def _find_in_messages(cased: str) -> RuleHit | None:
    m = FIND_IN_MESSAGES.match(cased.strip())
    if not m:
        return None
    return RuleHit("messages", "find_in_messages", {"kind": FIND_KIND[m["kind"].lower()], "who": m["who"]})


# "summarize this: <text>", "make this shorter: <text>": Apple's on-device model does these for free.
# Only with the text right there after a colon or a new line; "summarize my inbox" still goes to Claude.
TEXT_TASK = re.compile(
    r"^(?:(?:hey|ok|okay)\s+)?(?:(?:maxie|maxi|max),?\s+)?(?:(?:can|could|would) you\s+|please\s+)?"
    r"(?P<verb>summari[sz]e|sum up|tl;?dr|give me the gist of|rewrite|reword|rephrase|polish|clean up"
    r"|make (?:this|it) (?:clearer|shorter|nicer|friendlier|better|more \w+)"
    r"|(?:draft|write) a reply to this(?: message| email| text)?|reply to this(?: message| email| text)?"
    r"|pull out the (?:dates|details|key facts)|extract the (?:dates|details|key facts))"
    r"(?P<how>[^:\n]{0,48}?)\s*[:\n]\s*(?P<text>\S.*)$", re.I | re.S)
TEXT_TASK_MIN_CHARS = 40
TEXT_TASK_KIND = (("summar", "summarize"), ("sum up", "summarize"), ("tl", "summarize"), ("give", "summarize"),
                  ("draft", "reply"), ("write", "reply"), ("reply", "reply"), ("pull", "extract"),
                  ("extract", "extract"))
HOW_FILLER = re.compile(r"^(?:this|it|that|the following)\b\s*|\s*\bfor me\b", re.I)


def _text_task(text: str) -> RuleHit | None:
    m = TEXT_TASK.match(text.strip())
    if not m or len(m["text"].strip()) < TEXT_TASK_MIN_CHARS:
        return None
    verb = m["verb"].lower()
    task = next((k for prefix, k in TEXT_TASK_KIND if verb.startswith(prefix)), "rewrite")
    how = HOW_FILLER.sub("", m["how"].strip()).strip(" ,")
    return RuleHit("text_task", task, {"task": task, "text": m["text"].strip(), "how": how})


def match(text: str) -> RuleHit | None:
    t = normalize(text)
    if STOP.match(t):
        return RuleHit("control", "stop")
    if hit := _text_task(text):
        return hit
    if BRIEF.match(t):
        return RuleHit("control", "brief", {"kind": "morning"})
    if WRAP.match(t):
        return RuleHit("control", "brief", {"kind": "evening"})
    if MUTE_PROACTIVE.match(t):
        return RuleHit("control", "mute_proactive")
    if SHOW_MEMORY.match(t):
        return RuleHit("memory", "show")
    if WATCH_ON.match(t) or WATCH_OFF.match(t):
        return RuleHit("mac_action", "watch_me", {"on": bool(WATCH_ON.match(t))})
    if m := RUN_FLOW.match(t):
        return RuleHit("mac_action", "run_flow", {"name": m["name"]})
    cased = normalize(text, keep_case=True)
    if hit := _phone_ready(cased.rstrip()):
        return hit
    if hit := _find_in_messages(cased):
        return hit
    if m := RECALL.match(cased.lower()):
        return RuleHit("memory", "recall", {"q": cased[m.start("q"):m.end("q")]})
    if m := FORGET.match(cased.lower()):
        return RuleHit("memory", "forget", {"what": cased[m.start("what"):]})
    if m := REMEMBER.match(cased.lower()):
        return RuleHit("memory", "remember", {"fact": cased[m.start("fact"):]})
    if TIME.match(t):
        return RuleHit("quick_question", "time")
    if DATE.match(t):
        return RuleHit("quick_question", "date")
    if m := PERCENT.match(t):
        value = float(m["p"]) / 100 * float(m["n"].replace(",", ""))
        return RuleHit("quick_question", "math", {"result": fmt_number(value), "expr": t})
    if (m := MATH.match(t)) and (value := calc(m["e"])) is not None:
        return RuleHit("quick_question", "math", {"result": fmt_number(value), "expr": m["e"]})
    if (value := calc(t.rstrip("?="))) is not None:
        return RuleHit("quick_question", "math", {"result": fmt_number(value), "expr": t})
    if hit := _timer(t):
        return hit
    for pat, cmd in MUSIC:
        if pat.match(t):
            return RuleHit("mac_action", "music", {"command": cmd})
    for pat, level in VOLUME:
        if pat.match(t):
            return RuleHit("mac_action", "volume", {"level": level})
    if m := VOLUME_SET.match(t):
        return RuleHit("mac_action", "volume", {"level": str(min(100, int(m["n"])))})
    if (m := DND.match(t)) and (m["a"] or m["b"]):
        return RuleHit("mac_action", "do_not_disturb", {"on": (m["a"] or m["b"]) == "on"})
    if m := OPEN.match(t):
        return _open(m["name"])
    if (m := PLAY.match(t)) and not re.search(r"\b(game|video|movie|role)\b", m["q"]):
        q = re.sub(r"^(?:some|me some|a little)\s+", "", m["q"])
        return RuleHit("mac_action", "music", {"command": "play", "query": q})
    return None


# ---- lenient extraction (after a classifier said "mac_action") -----------------------------------

LOOSE = [
    (re.compile(rf"\b{NUM} {UNIT}\b.*\btimer\b"), "timer"),
    (re.compile(rf"\btimer\b.*?\b{NUM} {UNIT}\b"), "timer"),
    (re.compile(r"\b(?:play|put on|listen to)\b (?P<q>.+)$"), "play"),
    (re.compile(r"\b(?:louder|turn (?:it|the volume|the music) up|volume up|crank it)\b"), "up"),
    (re.compile(r"\b(?:quieter|softer|turn (?:it|the volume|the music) down|volume down|too loud)\b"), "down"),
    (re.compile(r"\b(?:pause|stop) (?:the )?(?:music|song)\b"), "pause"),
    (re.compile(r"\b(?:skip|next) (?:this )?(?:song|track)\b"), "next"),
    (re.compile(r"\b(?:open|launch|fire up|bring up|pull up|start) (?:up )?(?:the )?(?P<name>[\w .]+?)(?: app)?$"), "open"),
]


SEVERAL = re.compile(r"\b(?:and|then|after that|also)\b\s+(?:\w+\s+)?(?:open|play|launch|set|start|turn|put|send|"
                     r"text|email|add|find|check|read|draft|make|block|go)\b")


def extract(text: str) -> RuleHit | None:
    """Best effort action + args from looser phrasing. None if unsure (then Claude handles it).
    Never for a command with several steps ("open Spotify and play X"): half-doing it is worse."""
    if (hit := match(text)) and hit.route == "mac_action":
        return hit
    t = normalize(text)
    if SEVERAL.search(t):
        return None
    for pat, kind in LOOSE:
        m = pat.search(t)
        if not m:
            continue
        if kind == "timer":
            gd = {k: v for k, v in m.groupdict().items() if v}
            n, unit = _num(gd.get("n", "")), gd.get("unit", "min")
            if n is not None:
                return RuleHit("mac_action", "timer", {"minutes": _seconds_to_minutes(n, unit), "label": ""})
        elif kind == "play":
            q = re.sub(r"^(?:some|a little|me some)\s+", "", m["q"]).strip()
            return RuleHit("mac_action", "music", {"command": "play", "query": q})
        elif kind in ("up", "down"):
            return RuleHit("mac_action", "volume", {"level": kind})
        elif kind in ("pause", "next"):
            return RuleHit("mac_action", "music", {"command": kind})
        elif kind == "open":
            return _open(m["name"])
    return None
