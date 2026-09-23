"""Context packs: exactly the knowledge a question needs, picked per sentence (Isaac's design).

Jev answers "does this need the calendar / tasks / his projects / the screen / the web?" in the
same call that picks the route (asked in parallel, so no extra wait). Plain-code rails add the
obvious ones Jev might miss ("am I free at 5" always gets the calendar). Only the chosen packs
are gathered, so answers stay fast and the model isn't buried in text it doesn't need.

  always   : time, today's conversation, what Evie can do, remembered facts (built by the Brain)
  calendar : right now + today/tomorrow/week, plus any named far day ("the 14th of October")
  tasks    : Todoist, due today and overdue
  projects : a short brief about Isaac and his week + the best-matching bits of his IsaacOS notes
  screen   : the app and window in front (Phase 3b adds the page itself)
  web      : a search-grounded answer (Groq gpt-oss-120b with browser search)
"""
import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from evie.calendar_store import TZ, lookup

log = logging.getLogger("evie.packs")

PACKS = ("calendar", "tasks", "projects", "screen", "web")
ISAACOS = Path.home() / "IsaacOS"

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                           "august", "september", "october", "november", "december"])}
_MONTHS |= {m[:3]: n for m, n in list(_MONTHS.items())} | {"sept": 9}
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))

_RAILS = {
    "calendar": re.compile(r"\b(free|busy|today|tonight|tomorrow|this week|next week|weekend|calendar|schedule|"
                           r"what'?s on(?! my (?:to ?do|list))|when is|when's|class|lesson|school|due|" + "|".join(_WEEKDAYS) + r")\b"),
    "tasks": re.compile(r"\b(to ?do|todo|tasks?|todoist|due|homework|my list)\b"),
    "screen": re.compile(r"\b(this (page|tab|article|site|video|email|doc|window)|on (my|the) screen|summari[sz]e this|"
                         r"what am i looking at|read this)\b"),
    "web": re.compile(r"\b(news|weather|forecast|score|who won|latest|price of|stock|headlines|results?|"
                      r"right now in|current(ly)?|look (it )?up|search (for|the web))\b"),
}


def rails(text: str) -> set[str]:
    t = text.lower()
    return {name for name, rx in _RAILS.items() if rx.search(t)}


def named_days(text: str, today: date) -> list[date]:
    """Days Isaac names that the week summary may not cover: "the 14th of October", "Oct 14",
    "the 30th", "friday", "next week monday"."""
    t = text.lower()
    out: list[date] = []

    def add(d: date | None) -> None:
        if d and d not in out:
            out.append(d)

    def ymd(m: int, d: int) -> date | None:
        for y in (today.year, today.year + 1):
            try:
                cand = date(y, m, d)
            except ValueError:
                return None
            if cand >= today - timedelta(days=1):
                return cand
        return None

    for d, m in re.findall(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?(?: of)? ({_MON})\b", t):
        add(ymd(_MONTHS[m], int(d)))
    for m, d in re.findall(rf"\b({_MON})(?: the)? (\d{{1,2}})(?:st|nd|rd|th)?\b", t):
        add(ymd(_MONTHS[m], int(d)))
    for m, d in re.findall(rf"\bin ({_MON})\b.*?\bthe (\d{{1,2}})(?:st|nd|rd|th)\b", t):
        add(ymd(_MONTHS[m], int(d)))
    if not out:
        for d in re.findall(r"\bthe (\d{1,2})(?:st|nd|rd|th)\b", t):
            n = int(d)
            try:
                cand = today.replace(day=n)
            except ValueError:
                continue
            if cand < today:
                nm = today.replace(day=1) + timedelta(days=32)
                try:
                    cand = nm.replace(day=n)
                except ValueError:
                    continue
            add(cand)
    for nxt, wd in re.findall(r"\b(next week )?(" + "|".join(_WEEKDAYS) + r")\b", t):
        ahead = (_WEEKDAYS.index(wd) - today.weekday()) % 7 or 7
        if nxt:
            monday_next = today + timedelta(days=7 - today.weekday())
            add(monday_next + timedelta(days=_WEEKDAYS.index(wd)))
        else:
            add(today + timedelta(days=ahead))
    return out


class ProjectIndex:
    """Isaac's IsaacOS notes as searchable paragraphs, plus a short brief. Rebuilt hourly;
    plain keyword matching, no embeddings, all local."""

    REFRESH_S = 3600
    STOP = set("what how whats the and for with that this have from about going does into your mine "
               "evie isaac tell give show please".split())

    def __init__(self, root: Path = ISAACOS, clock: Callable[[], float] = time.monotonic):
        self._root, self._clock = root, clock
        self._built_at: float | None = None
        self._paras: list[tuple[str, str]] = []
        self.brief = ""

    def _build(self) -> None:
        if self._built_at is not None and self._clock() - self._built_at < self.REFRESH_S:
            return
        self._built_at = self._clock()
        parts = []
        claude = self._root / "CLAUDE.md"
        if claude.exists():
            parts.append(claude.read_text(errors="ignore")[:2500])
        goals = self._root / "context/goals-status.json"
        if goals.exists():
            parts.append("Goals: " + " ".join(goals.read_text(errors="ignore").split())[:800])
        log_md = self._root / "projects/session-log.md"
        if log_md.exists():
            recent = [l for l in log_md.read_text(errors="ignore").splitlines() if re.match(r"\d{4}-\d\d-\d\d \|", l)]
            parts.append("This week: " + " / ".join(l[:160] for l in recent[-25:]))
        self.brief = "\n".join(parts)[:6000]
        paras = []
        for p in sorted((self._root / "projects").rglob("*.md")) if (self._root / "projects").exists() else []:
            if p.name == "session-log.md" or p.stat().st_size > 400_000:
                continue
            rel = str(p.relative_to(self._root / "projects"))
            rel += f", updated {datetime.fromtimestamp(p.stat().st_mtime):%-d %b}"
            for para in re.split(r"\n\s*\n", p.read_text(errors="ignore")):
                para = " ".join(para.split())
                if 40 <= len(para):
                    paras.append((rel, para[:700]))
        self._paras = paras

    def search(self, question: str, k: int = 3) -> str:
        self._build()
        words = {w for w in re.findall(r"[a-z0-9]{4,}", question.lower()) if w not in self.STOP}
        if not words:
            return ""
        scored = []
        for rel, para in self._paras:
            low = para.lower()
            hits = sum(1 for w in words if w in low) + sum(0.5 for w in words if w in rel.lower())
            if hits:
                scored.append((hits, rel, para))
        scored.sort(key=lambda x: -x[0])
        return "\n".join(f"[{rel}] {para}" for _, rel, para in scored[:k])


class Packs:
    def __init__(self, calendar, hands, todoist, projects: ProjectIndex | None = None, web=None,
                 screen: Callable[[], dict] = dict, today: Callable[[], date] = lambda: datetime.now(TZ).date()):
        self._cal, self._hands, self._todoist = calendar, hands, todoist
        self._projects, self._web, self._screen, self._today = projects, web, screen, today

    async def gather(self, names: set[str], text: str) -> dict:
        jobs = {n: getattr(self, f"_pack_{n}")(text) for n in names if n in PACKS}
        facts: dict = {}
        results = await asyncio.gather(*jobs.values(), return_exceptions=True)
        for name, res in zip(jobs, results):
            if isinstance(res, BaseException):
                log.warning("context pack %s failed: %r", name, res)
                facts |= {"tasks": {"todoist": "couldn't check Todoist just now"}}.get(name, {})
                continue
            facts |= res
        return facts

    async def _pack_calendar(self, text: str) -> dict:
        today = self._today()
        out = {}
        for d in named_days(text, today):
            if (d - today).days > 7 or d < today:
                key = "calendar_" + re.sub(r"\W+", "_", d.strftime("%A %-d %b").lower())
                out[key] = await lookup(self._cal, self._hands, d, today=today)
        return out

    async def _pack_tasks(self, text: str) -> dict:
        tasks = await self._todoist.list("today | overdue")
        if not tasks:
            return {"todoist": "nothing due today or overdue"}
        return {"todoist": "; ".join(t.content + (f" (due {t.due})" if t.due else "") for t in tasks[:25])}

    async def _pack_projects(self, text: str) -> dict:
        if self._projects is None:
            return {}
        idx = self._projects
        snippets = await asyncio.get_running_loop().run_in_executor(None, idx.search, text)
        out = {"isaac_brief": idx.brief}
        if snippets:
            out["isaac_files"] = snippets
        return out

    async def _pack_screen(self, text: str) -> dict:
        s = self._screen() or {}
        bits = [f"Front app: {s['front_app']}" if s.get("front_app") else "",
                f"Window: {s['window']}" if s.get("window") else "",
                f"Page: {s['url']}" if s.get("url") else "",
                f"Selected text: {s['selected'][:1500]}" if s.get("selected") else "",
                f"Page text: {s['page_text'][:4000]}" if s.get("page_text") else ""]
        line = ". ".join(b for b in bits if b)
        return {"screen": line} if line else {}

    async def _pack_web(self, text: str) -> dict:
        if self._web is None:
            return {}
        return {"web": await self._web.search(text)}


class WebSearch:
    """Groq gpt-oss-120b with its built-in browser search: ~4 s, grounded, cited in its answer."""

    MODEL = "openai/gpt-oss-120b"

    def __init__(self, groq):
        self._groq = groq

    async def search(self, question: str) -> str:
        from evie.talk import TalkError
        try:
            return await self._groq.search(question)
        except TalkError as e:
            log.warning("web search failed: %s", str(e)[:120])
            return "couldn't search the web just now"

