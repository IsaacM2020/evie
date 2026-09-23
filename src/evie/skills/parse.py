"""Plain-code readers for skill details: "volume 30", "ten minutes", "open vs code".

No model here: these are fast (microseconds), exact, and testable. Only free text a parser
can't read (a song name, a website) goes to Groq.
"""
import re
from urllib.parse import urlparse

_ONES = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}


def words_to_numbers(text: str) -> str:
    """"twenty five minutes" -> "25 minutes", "one hundred" -> "100"."""
    out: list[str] = []
    num: int | None = None
    for tok in text.lower().replace("-", " ").split():
        if tok in _ONES:
            num = (num or 0) + _ONES[tok]
        elif tok in _TENS:
            num = (num or 0) + _TENS[tok]
        elif tok == "hundred" and num is not None:
            num *= 100
        else:
            if num is not None:
                out.append(str(num))
                num = None
            out.append(tok)
    if num is not None:
        out.append(str(num))
    return " ".join(out)


def _clean(text: str) -> str:
    return words_to_numbers(re.sub(r"[^a-z0-9.%' -]", " ", text.lower()))


# -- volume ---------------------------------------------------------------------------------
_UP = re.compile(r"\b(up|louder|raise|increase|higher)\b")
_DOWN = re.compile(r"\b(down|quieter|lower|decrease|softer)\b")


def parse_volume(text: str) -> tuple[str, int] | None:
    t = _clean(text)
    if re.search(r"\bunmute\b", t):
        return ("unmute", 0)
    if re.search(r"\bmute\b", t):
        return ("mute", 0)
    m = re.search(r"\b(\d{1,3})\b", t)
    if m:
        return ("set", min(100, int(m.group(1))))
    if _UP.search(t):
        return ("up", 10)
    if _DOWN.search(t):
        return ("down", 10)
    return None


# -- durations ------------------------------------------------------------------------------
_UNITS = {"h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
          "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
          "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1}
_UNIT_RE = "|".join(sorted(_UNITS, key=len, reverse=True))


def parse_duration(text: str) -> int | None:
    t = _clean(text)
    t = re.sub(r"\bhalf an? hour\b", "30 minutes", t)
    t = re.sub(rf"\ban? ({_UNIT_RE})\b", r"1 \1", t)
    t = re.sub(rf"\b(\d+) and a half ({_UNIT_RE})\b", r"\1.5 \2", t)
    t = re.sub(rf"\b(\d+) ({_UNIT_RE}) and a half\b", r"\1.5 \2", t)
    total = 0.0
    for n, unit in re.findall(rf"\b(\d+(?:\.\d+)?) ?({_UNIT_RE})\b", t):
        total += float(n) * _UNITS[unit]
    return int(total) if total > 0 else None


def say_duration(seconds: int) -> str:
    """600 -> "10 minute" (as in "your 10 minute timer")."""
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600} hour"
    if seconds >= 3600 and seconds % 3600 == 1800:
        return f"{seconds // 3600} and a half hour"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60} minute"
    if seconds >= 60 and seconds % 60 == 30:
        return f"{seconds // 60} and a half minute"
    return f"{seconds} second"


# -- apps -----------------------------------------------------------------------------------
_ALIASES = {"vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "settings": "System Settings"}


def _variants(name: str) -> set[str]:
    n = name.lower().removesuffix(".app")
    v = {n, n.removesuffix(".us")}
    for prefix in ("google ", "microsoft ", "apple "):
        if n.startswith(prefix):
            v.add(n.removeprefix(prefix))
    return v


def match_app(text: str, apps: list[str]) -> str | None:
    """The installed app named in the sentence, if one is said outright. Longest match wins."""
    t = " " + " ".join(re.sub(r"[^a-z0-9. ]", " ", text.lower()).split()) + " "
    best: tuple[int, str] | None = None
    candidates = [(v, a) for a in apps for v in _variants(a)]
    candidates += [(alias, real) for alias, real in _ALIASES.items() if real in apps]
    for variant, app in candidates:
        if f" {variant} " in t and (best is None or len(variant) > best[0]):
            best = (len(variant), app)
    return best[1] if best else None


# -- websites -------------------------------------------------------------------------------
_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?$", re.IGNORECASE)


def normalize_url(raw: str) -> str | None:
    """Only real web addresses: http(s), a dotted host name. Nothing local, nothing scripted."""
    s = (raw or "").strip()
    if _DOMAIN.match(s):
        s = "https://" + s
    u = urlparse(s)
    if u.scheme not in ("http", "https") or not u.hostname or "." not in u.hostname:
        return None
    return s
