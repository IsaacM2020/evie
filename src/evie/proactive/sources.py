"""Where Evie's follow-ups come from (Phase 4/5, all eight Isaac picked on 2026-09-24).

  overheard   he tells someone "dentist Wednesday": later, "What time?" (only what/day/time is kept)
  heads_up    15 min before a class or appointment (not School, he knows about that one)
  tasks       Todoist due today, once after school and once in the evening
  deadlines   due in the next two days (Todoist and his Classroom calendars), each once
  brief       the morning brief on his first activity after 5am
  jobs        a Claude Code job that finishes while he's busy waits until he's free
  stuck       the same error on screen for 10 min while he's there: a quiet chip, never spoken, and
              the error text never leaves the Mac unless he taps yes (so no model sees it first)
  resume      back after 20 min away with things closed: "pick up where you left off?"
"""
import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Callable

from evie.calendar_store import TZ, CalendarStore
from evie.proactive.queue import FollowUp

log = logging.getLogger("evie.proactive")

OVERHEARD_Q = ('Isaac said this to someone else, not to Evie. Did he mention an appointment, deadline, test or '
               'promise of his own? Return {"what": a short noun phrase like "the dentist" or "a physics test" '
               '(or "" if he mentioned none), "day": the day as he said it ("Wednesday", "the 10th", or ""), '
               '"time": the time as he said it (or "")}.')
TASK_MOMENTS = ((15, 45), (19, 30))  # after school, and the evening
RADAR_EVERY_S = 1800.0
RADAR_PER_DAY = 3
HEADS_UP_S = 15 * 60
EVERYDAY = re.compile(r"^\s*school\s*$", re.I)  # no heads-up for the thing he does every day
DEV_APPS = {"Terminal", "iTerm2", "Code", "Visual Studio Code", "Cursor", "Xcode", "Warp", "Ghostty", "Zed"}
ERROR = re.compile(r"(traceback|\berror\b|error:|exception|\bfailed\b|exit code [1-9]|panic:)", re.I)
STUCK_S = 600.0
ACTIVE_S = 120.0
AWAY_S = 20 * 60


class Sources:
    def __init__(self, engine, calendar: CalendarStore, todoist, talker, hands=None,
                 now: Callable[[], datetime] = lambda: datetime.now(TZ), clock: Callable[[], float] = time.time,
                 text_mode: Callable[[], bool] = lambda: False):
        self.engine, self._cal, self._todoist, self._talker, self._hands = engine, calendar, todoist, talker, hands
        self._now, self._clock, self._text = now, clock, text_mode
        self.enabled: Callable[[str], bool] = lambda name: True  # the panel's per-source switches
        self._radar_at = -1e9
        self._asked: set[str] = set()  # task-nudge moments already looked at
        self._brief_day: date | None = None
        self._radar: dict[date, int] = {}
        self._active_at: float | None = None
        self._errors: dict[str, tuple[str, float]] = {}  # app -> (error signature, first seen)
        self._away: tuple[float, dict] | None = None

    # -- on every tick ---------------------------------------------------------------------------
    async def collect(self) -> None:
        for name, fn in (("heads_up", self._heads_up), ("tasks", self._task_nudge), ("deadlines", self._deadlines)):
            if self.enabled(name):
                try:
                    await fn()
                except Exception:  # one broken source never stops the others
                    log.exception("proactive source %s failed", name)

    async def _heads_up(self) -> None:
        now = self._now()
        for e in self._cal.on(now.date()):
            left = (e.start - now).total_seconds()
            if e.all_day or EVERYDAY.match(e.title) or not 0 < left <= HEADS_UP_S:
                continue
            self.engine.add(FollowUp("heads_up", f"{e.title} in {round(left / 60)} minutes.",
                                     f"heads_up:{e.id or e.title}:{e.start.isoformat()}", importance="high",
                                     expires=e.start.timestamp()))

    async def _task_nudge(self) -> None:
        now = self._now()
        for h, m in TASK_MOMENTS:
            moment = now.replace(hour=h, minute=m, second=0, microsecond=0)
            key = f"tasks:{now.date()}:{h:02d}{m:02d}"
            if not moment <= now < moment + timedelta(hours=2) or key in self._asked:
                continue
            self._asked.add(key)  # one look at Todoist per moment
            tasks = await self._todoist.list("today | overdue")
            if not tasks:
                continue
            first, more = tasks[0].content, len(tasks) - 1
            line = f"{first} is due today" + (f", plus {more} more" if more else "") + ". Want help getting it done?"
            self.engine.add(FollowUp("tasks", line, key, on_yes={"do": "job", "goal": f"help me get this done: {first}"},
                                     expires=(moment + timedelta(hours=3)).timestamp()))

    async def _deadlines(self) -> None:
        if self._clock() - self._radar_at < RADAR_EVERY_S:
            return
        self._radar_at = self._clock()
        today = self._now().date()
        soon = {today + timedelta(days=1), today + timedelta(days=2)}
        due: list[tuple[str, str, date]] = []
        for t in await self._todoist.list("overdue | 3 days"):
            d = _day(t.date)
            if d in soon:
                due.append((f"deadline:todoist:{t.id}", t.content, d))
        for d in sorted(soon):
            for e in self._cal.on(d):
                if e.calendar != "Isaac" and not re.search(r"\bclass\b", e.title, re.I):  # Classroom work
                    due.append((f"deadline:cal:{e.id or e.title}:{d}", e.title, d))
        for key, what, d in due:
            if self._radar.get(today, 0) >= RADAR_PER_DAY:
                break
            if self.engine.add(FollowUp("deadline", f"{what} is due {d.strftime('%A')}. Want me to help you start it?", key,
                                        on_yes={"do": "job", "goal": f"help me get started on: {what} (due {d:%A %-d %b})"},
                                        expires=self._clock() + 12 * 3600)):
                self._radar[today] = self._radar.get(today, 0) + 1

    # -- from the Brain and the app --------------------------------------------------------------
    async def overheard(self, text: str) -> None:
        """He told someone about a plan: keep WHAT and WHEN, never the sentence."""
        if not self.enabled("overheard"):
            return
        q = await self._talker.extract(OVERHEARD_Q, text) or {}
        what, day, at = (str(q.get(k) or "").strip() for k in ("what", "day", "time"))
        if not what:
            return
        key = f"overheard:{what}:{day}".lower()
        when = f" on {day}" if day else ""
        if day and not at:
            self.engine.add(FollowUp("overheard", f"Heard you've got {what}{when}. What time?", key, ask=True,
                                     request=f"remember I have {what}{when}"))
        else:
            full = f"{what}{when}" + (f" at {at}" if at else "")
            self.engine.add(FollowUp("overheard", f"Heard you've got {full}. Want it in your calendar?", key,
                                     on_yes={"do": "turn", "text": f"remember I have {full}"}))

    def job_done(self, goal: str, summary: str) -> bool:
        """A job finished. If he's in the middle of something, it waits (True); otherwise the
        narrator just says it (False)."""
        if not self.enabled("jobs") or self.engine.free():
            return False
        goal = goal.strip().rstrip(".")[:60]
        self.engine.add(FollowUp("job", f"The {goal} job is done. Want the summary?", f"job:{goal}:{int(self._clock())}",
                                 importance="high", on_yes={"do": "say", "text": summary}))
        return True

    def activity_now(self) -> None:
        self._active_at = self._clock()

    async def activity(self, kind: str) -> None:
        """The app: "active" (unlocked / first input), "idle" (20 min no input), "back"."""
        now = self._now()
        if kind in ("active", "back"):
            self.activity_now()
        if kind == "active" and self.enabled("brief") and 5 <= now.hour < 12 and self._brief_day != now.date():
            self._brief_day = now.date()
            await self._brief(now)
        elif kind == "idle" and self.enabled("resume") and self._hands is not None:
            self._away = (self._clock(), await self._world())
        elif kind == "back" and self._away:
            (left, before), self._away = self._away, None
            if self._clock() - left >= AWAY_S and before:
                self._resume(before, await self._world(), left)

    async def _brief(self, now: datetime) -> None:
        tasks = await self._todoist.list("today | overdue")
        facts = {"today": self._cal.summary(now.date(), today=now.date()),
                 "due": "; ".join(t.content for t in tasks[:5]) or "nothing due"}
        line = await self._talker.brief(facts)
        if line:
            self.engine.add(FollowUp("brief", line, f"brief:{now.date()}", importance="high",
                                     expires=self._clock() + 3 * 3600))

    def screen_text(self, app: str, text: str) -> None:
        """The app reads the front window of a dev app every minute. The same error for 10 min while
        he's there: a quiet chip. Nothing here is sent anywhere unless he taps yes."""
        if app not in DEV_APPS or self._text() or not self.enabled("stuck"):
            return
        sig = _signature(text)
        if not sig:
            self._errors.pop(app, None)
            return
        seen = self._errors.get(app)
        if seen is None or seen[0] != sig:
            self._errors[app] = (sig, self._clock())
            return
        here = self._active_at is not None and self._clock() - self._active_at <= ACTIVE_S
        if self._clock() - seen[1] >= STUCK_S and here:
            tail = "\n".join(text.strip().splitlines()[-25:])
            self.engine.add(FollowUp("stuck", "Stuck on this? Claude Code can take a look.", f"stuck:{sig}",
                                     chip_only=True, on_yes={"do": "job", "goal": (
                                         f"Isaac has been stuck on this error in {app} for a while. Find the cause and "
                                         f"fix it:\n{tail}")}))

    async def _world(self) -> dict:
        try:
            r = await self._hands.do("world", timeout=6.0)
            return json.loads(r.data.get("world") or "{}") if r.ok else {}
        except Exception:  # noqa: BLE001 - no world, no offer
            return {}

    def _resume(self, before: dict, after: dict, left: float) -> None:
        tabs = [t for t in before.get("tabs", []) if t.get("url")]
        still = {t.get("url") for t in after.get("tabs", [])}
        closed = [t for t in tabs if t["url"] not in still]
        front = before.get("front_app", "")
        if not closed and front == after.get("front_app", ""):
            return  # nothing to pick up: it's all still there
        cur = next((t for t in tabs if t.get("current")), tabs[0] if tabs else None)
        if cur:
            more = len(tabs) - 1
            line = f"Pick up where you left off? {cur.get('title') or 'Your tabs'}" + \
                (f" and {more} more tab{'s' if more != 1 else ''}." if more else ".")
        else:
            line = f"Pick up where you left off in {front}?"
        self.engine.add(FollowUp("resume", line, f"resume:{int(left)}", chip_only=True,
                                 on_yes={"do": "reopen", "urls": [t["url"] for t in tabs], "app": front},
                                 expires=self._clock() + 3600))


def _signature(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ERROR.search(ln)]
    if not lines:
        return ""
    s = re.sub(r"\d+", "#", lines[-1].lower())
    return " ".join(s.split())[:120]


def _day(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None
