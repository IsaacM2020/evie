"""What's on Isaac's screen right now, from one `world` read by the app (~100 ms): which app is in
front, which windows are open front to back, every Safari tab, and any selected text.

`resolve()` turns the words of a goal into WHERE Evie should work, in plain code:
  "this page / this news thing"  -> the front Safari tab, brought to the front if it's behind
  "add milk to this"             -> the app in front (no web words)
  "switch to my gmail"           -> the tab whose title or site matches
  "message mom on whatsapp"      -> that app
  "play the newest X video"      -> a new tab in the front Safari window (or a new window)
Two tabs that could both be meant come back as choices, for Jev to pick from.
"""
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

BROWSERS = ("Safari",)
_DEICTIC = re.compile(r"\b(this|that|these|here|current|on (my|the) screen|i'?m (looking at|reading|watching))\b", re.I)
_WEBBY = re.compile(r"\b(pages?|articles?|news|story|stories|videos?|tabs?|sites?|websites?|links?|posts?|headlines?|"
                    r"youtube|search|google|watch|browser|web|online|channel)\b", re.I)
_TAB_WORDS = re.compile(r"\b(switch to|go to|go back to|open)\b.*\b(tab)?\b", re.I)
_STOP = {"the", "my", "a", "to", "tab", "switch", "go", "back", "open", "on", "in", "of", "and", "page", "please",
         "evie", "hey", "news", "video", "site", "website", "this", "that", "can", "you", "me"}
# Apps Isaac uses a lot, so a goal can name one that isn't running yet.
KNOWN_APPS = ("Safari", "WhatsApp", "Messages", "Notes", "Finder", "System Settings", "Spotify", "Notion", "Mail",
              "Calendar", "Calculator", "Reminders", "Preview", "Terminal", "Photos", "Music", "Maps")


@dataclass(frozen=True)
class Tab:
    window: int
    order: int  # Safari's own window order: 1 = its front window
    index: int
    current: bool
    title: str
    url: str

    @property
    def host(self) -> str:
        return urlparse(self.url).netloc.removeprefix("www.")


@dataclass
class Target:
    kind: str  # "tab" | "new_tab" | "app" | "choose"
    app: str = ""
    window: int | None = None
    index: int | None = None
    bring_front: bool = False
    running: bool = True
    choices: list[Tab] = field(default_factory=list)
    why: str = ""


@dataclass
class World:
    front_app: str
    apps: list[str]
    windows: list[dict]
    tabs: list[Tab]
    selected: str = ""

    @classmethod
    def from_data(cls, d: dict) -> "World":
        tabs = [Tab(int(t.get("window", 0)), int(t.get("order", 1)), int(t.get("index", 1)), bool(t.get("current")),
                    str(t.get("title", "")), str(t.get("url", ""))) for t in d.get("tabs") or [] if isinstance(t, dict)]
        return cls(str(d.get("front_app", "")), [str(a) for a in d.get("apps") or []],
                   [w for w in d.get("windows") or [] if isinstance(w, dict)], tabs, str(d.get("selected", "")))

    # -- facts --------------------------------------------------------------------------------
    def front_tab(self) -> Tab | None:
        """The current tab of Safari's front window."""
        cur = sorted((t for t in self.tabs if t.current), key=lambda t: t.order)
        return cur[0] if cur else None

    def summary(self) -> str:
        parts = [f"In front: {self.front_app}"]
        top = next((w for w in self.windows if w.get("app") == self.front_app), None)
        if top and top.get("title"):
            parts[0] += f" (window '{top['title'][:60]}')"
        for order in sorted({t.order for t in self.tabs}):
            ts = [t for t in self.tabs if t.order == order]
            names = ", ".join(f"{t.title[:50]}{' (current)' if t.current else ''}" for t in ts[:12])
            parts.append(f"Safari window {order}: {names}")
        others = [a for a in self.apps if a != self.front_app and a not in BROWSERS]
        if others:
            parts.append("Also open: " + ", ".join(others[:12]))
        if self.selected:
            parts.append(f"Selected text: {self.selected[:300]}")
        return "\n".join(parts)

    # -- where to work --------------------------------------------------------------------------
    def resolve(self, goal: str) -> Target:
        g = goal.lower()
        browser_front = self.front_app in BROWSERS
        # 1. A tab he names ("my gmail", "the networkchuck tab").
        named = self._named_tabs(g)
        if len(named) == 1:
            t = named[0]
            return Target("tab", "Safari", t.window, t.index, bring_front=not browser_front or not t.current,
                          why=f"the tab '{t.title}'")
        if len(named) > 1:
            return Target("choose", "Safari", choices=named, why="more than one tab could be meant")
        # 2. An app he names.
        app = self._named_app(g)
        if app and app not in BROWSERS:
            return Target("app", app, running=app in self.apps, bring_front=app != self.front_app, why=f"he named {app}")
        # 3. "this ..." means what's in front, or the front web page when he's talking about web things.
        if _DEICTIC.search(g):
            if browser_front or (_WEBBY.search(g) and self.front_tab()):
                t = self.front_tab()
                if t:
                    return Target("tab", "Safari", t.window, t.index, bring_front=not browser_front,
                                  why="the page he's looking at")
            return Target("app", self.front_app, bring_front=False, why="the app in front")
        # 4. Web work somewhere new: a new tab in Safari's front window (or a new window).
        if _WEBBY.search(g) or app in BROWSERS:
            t = self.front_tab()
            return Target("new_tab", "Safari", t.window if t else None, bring_front=True,
                          running="Safari" in self.apps, why="new web task")
        return Target("app", self.front_app, why="nothing named: the app in front")

    def _named_tabs(self, g: str) -> list[Tab]:
        words = {w for w in re.findall(r"[a-z0-9]+", g) if w not in _STOP and len(w) > 2}
        if not words or not self.tabs:
            return []
        hits = []
        for t in self.tabs:
            hay = set(re.findall(r"[a-z0-9]+", (t.title + " " + t.host).lower()))
            if words & hay:
                hits.append(t)
        # Several matches only count as "which one?" when he's clearly asking for a tab.
        if len(hits) > 1 and not ("tab" in g or _TAB_WORDS.search(g)):
            return []
        return hits

    def _named_app(self, g: str) -> str | None:
        for a in sorted(set(self.apps) | set(KNOWN_APPS), key=len, reverse=True):
            if re.search(rf"\b{re.escape(a.lower())}\b", g):
                return a
        return None
