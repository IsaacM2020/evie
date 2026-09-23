""""Remember X" becomes something real. Jev already picked where it goes (task / event / fact);
Groq pulls out the details as JSON; plain code checks them (a real date, in the future, within a
year) before anything is written:
  task  -> Todoist (API v1), with Todoist reading the due date ("tomorrow at 5")
  event -> macOS Calendar through the Evie app (EventKit), with a clash check
  fact  -> Evie's own facts file, used in her answers from then on
No time for an event means she asks "What time?"; Isaac's answer is merged in and it runs again.
Everything can be undone ("undo that").
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

import httpx

from evie.calendar_store import TZ, CalendarStore
from evie.facts import FactStore

log = logging.getLogger("evie.remember")

TODOIST_URL = "https://api.todoist.com/api/v1/tasks"

EVENT_Q = ('Isaac is describing something happening at a set time. Today is {today}. Return {{"title": '
           'string (short, like "Dentist" or "Chem test"), "date": "YYYY-MM-DD" or null, "time": "HH:MM" '
           '24-hour or null if no time was said, "duration_min": integer or null, "all_day": true only for '
           'whole-day things like birthdays or holidays}}.')
TASK_Q = ('Isaac wants a to-do. Return {"content": string (the task, short, starting with a verb, like '
          '"Email Mr Tan"), "due": string or null (when it is due exactly as he said it, like "tomorrow '
          'at 5pm" or "friday")}.')
FACT_Q = 'Isaac wants a fact remembered. Return {"fact": string}: one short sentence about Isaac, like "Isaac\'s locker code is 4129."'


@dataclass
class Remembered:
    said: str | None
    ok: bool = True
    ask: str | None = None  # a question to ask first ("What time?")


class Todoist:
    def __init__(self, key: str, http: httpx.AsyncClient | None = None):
        self._key = key
        self._http = http or httpx.AsyncClient(timeout=5.0)

    async def add(self, content: str, due: str | None) -> str | None:
        if not self._key:
            return None
        body = {"content": content, **({"due_string": due, "due_lang": "en"} if due else {})}
        try:
            r = await self._http.post(TODOIST_URL, json=body, headers={"Authorization": f"Bearer {self._key}"})
            r.raise_for_status()
            return str(r.json()["id"])
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("todoist add failed: %r", e)
            return None

    async def delete(self, tid: str) -> bool:
        try:
            r = await self._http.delete(f"{TODOIST_URL}/{tid}", headers={"Authorization": f"Bearer {self._key}"})
            return r.status_code in (200, 204)
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._http.aclose()


def spoken_day(d: date, today: date) -> str:
    n = (d - today).days
    if n == 0:
        return "today"
    if n == 1:
        return "tomorrow"
    if 2 <= n <= 6:
        return d.strftime("%A")
    return d.strftime("%A %-d %b")


def spoken_time(t: time) -> str:
    h = t.hour % 12 or 12
    ampm = "am" if t.hour < 12 else "pm"
    return f"{h}{ampm}" if t.minute == 0 else f"{h}:{t.minute:02d}{ampm}"


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Remember:
    def __init__(self, talker, hands, todoist: Todoist, facts: FactStore, calendar: CalendarStore, undo,
                 now: Callable[[], datetime] = lambda: datetime.now(TZ)):
        self._talker, self._hands, self._todoist = talker, hands, todoist
        self.facts, self._cal, self._undo, self._now = facts, calendar, undo, now

    async def run(self, where: str | None, text: str) -> Remembered:
        if where == "event":
            return await self._event(text)
        if where == "fact":
            return await self._fact(text)
        return await self._task(text)

    async def _event(self, text: str) -> Remembered:
        now = self._now()
        x = await self._talker.extract(EVENT_Q.format(today=now.strftime("%A %-d %B %Y")), text)
        title = str(x.get("title") or "").strip()
        try:
            day = date.fromisoformat(str(x.get("date")))
        except ValueError:
            day = None
        if not title or day is None or day < now.date() or day > now.date() + timedelta(days=366):
            return Remembered("I couldn't work out when that is.", ok=False)
        all_day = bool(x.get("all_day"))
        if all_day:
            start = datetime.combine(day, time(0), TZ)
            end, when = start + timedelta(days=1), spoken_day(day, now.date())
        else:
            try:
                t = time.fromisoformat(str(x.get("time")))
            except ValueError:
                return Remembered(None, ask="What time?")
            start = datetime.combine(day, t, TZ)
            if start < now - timedelta(hours=1):
                return Remembered("That time's already gone.", ok=False)
            mins = x.get("duration_min") if isinstance(x.get("duration_min"), int) and x["duration_min"] > 0 else 60
            end = start + timedelta(minutes=mins)
            when = f"{spoken_day(day, now.date())} at {spoken_time(t)}"
        r = await self._hands.do("calendar_add", title=title, start=_utc(start), end=_utc(end), all_day=all_day)
        if not r.ok:
            return Remembered(f"Couldn't add it: {r.detail}.", ok=False)
        eid = r.data.get("id", "")

        async def undo() -> str:
            u = await self._hands.do("calendar_delete", id=eid)
            return f"Removed {title} from your calendar." if u.ok else f"Couldn't remove it: {u.detail}."

        self._undo.remember_undo(undo)
        clash = next((e.title for e in self._cal.on(day) if not all_day and not e.all_day
                      and e.start < end and e.end > start), None)
        return Remembered(f"Added {title}, {when}." + (f" Heads up, it overlaps {clash}." if clash else ""))

    async def _task(self, text: str) -> Remembered:
        x = await self._talker.extract(TASK_Q, text)
        content = str(x.get("content") or "").strip()
        due = str(x["due"]).strip() if x.get("due") else None
        if not content:
            return Remembered("I didn't catch what the task is.", ok=False)
        tid = await self._todoist.add(content, due)
        if not tid:
            return Remembered("Couldn't reach Todoist, try again in a bit.", ok=False)

        async def undo() -> str:
            return f"Took {content} off Todoist." if await self._todoist.delete(tid) else "Todoist wouldn't let me."

        self._undo.remember_undo(undo)
        return Remembered(f"Added to Todoist: {content}" + (f", {due}." if due else "."))

    async def _fact(self, text: str) -> Remembered:
        fact = str((await self._talker.extract(FACT_Q, text)).get("fact") or "").strip()
        if not fact:
            return Remembered("I didn't catch what to remember.", ok=False)
        self.facts.add(fact)

        async def undo() -> str:
            self.facts.remove_last()
            return "Forgot it."

        self._undo.remember_undo(undo)
        return Remembered("Got it, I'll remember that.")
