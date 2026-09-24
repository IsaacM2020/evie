""""Remember X" becomes something real. Jev already picked where it goes (task / event / fact /
reminder); Groq pulls out the details as JSON; plain code checks them (a real date, in the
future, within a year) before anything is written:
  task     -> Todoist (API v1), with Todoist reading the due date ("tomorrow at 5")
  reminder -> within 12 hours: Evie says it herself when it's time (a labelled timer);
              later than that: a Todoist task with its time
  event    -> Isaac's Google calendar through the Evie app (EventKit), with a clash check
  fact     -> Evie's own facts file, used in her answers from then on
No time for an event means she asks "What time?", no title "What's it called?"; Isaac's answer
is merged in and it runs again. Everything can be undone ("undo that").
"""
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

import httpx

from evie.calendar_store import TZ, CalendarStore
from evie.facts import FactStore
from evie.skills.parse import parse_duration

log = logging.getLogger("evie.remember")

TODOIST_URL = "https://api.todoist.com/api/v1/tasks"

EVENT_Q = ('Isaac is describing something happening at a set time. Today is {today}. Return {{"title": '
           'string (short, like "Dentist" or "Chem test"), "date": "YYYY-MM-DD" or null, "time": "HH:MM" '
           '24-hour or null if no time was said, "duration_min": integer or null, "all_day": true only for '
           'whole-day things like birthdays or holidays}}.')
TASK_Q = ('Isaac wants a to-do. Return {"content": string (the task, short, starting with a verb, like '
          '"Email Mr Tan"), "due": string or null (when it is due exactly as he said it, like "tomorrow '
          'at 5pm" or "friday")}.')
REMINDER_Q = ('Isaac wants a reminder. Today is {today}, it is {now} now. Return {{"what": string (what to '
              'remind him of, short, like "eat a banana" or "Email Mr Tan"), "date": "YYYY-MM-DD" or null if no '
              'day was said, "time": "HH:MM" 24-hour or null if no clock time was said, "due": string or null '
              '(the when exactly as he said it, like "tomorrow at 5pm")}}.')
REMINDER_MAX_S = 12 * 3600  # sooner than this Evie says it herself; later goes to Todoist
_IN_DURATION = re.compile(r"\b(?:in|for|after) ((?:an? |half an? |\d+(?:\.\d+)? ?)(?:and a half )?"
                          r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)(?: and a half)?)\b", re.IGNORECASE)
FACT_Q = 'Isaac wants a fact remembered. Return {"fact": string}: one short sentence about Isaac, like "Isaac\'s locker code is 4129."'


@dataclass
class Remembered:
    said: str | None
    ok: bool = True
    ask: str | None = None  # a question to ask first ("What time?")


@dataclass
class Added:
    id: str | None
    due_dropped: bool = False  # Todoist didn't understand the due date, so it was added without one
    error: str = ""


@dataclass
class Task:
    id: str
    content: str
    due: str | None
    date: str | None = None  # YYYY-MM-DD, for the deadline radar


class Todoist:
    def __init__(self, key: str, http: httpx.AsyncClient | None = None):
        self._key = key
        self._http = http or httpx.AsyncClient(timeout=5.0)

    @property
    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self._key}"}

    async def add(self, content: str, due: str | None) -> Added:
        if not self._key:
            return Added(None, error="Todoist isn't set up")
        body = {"content": content, **({"due_string": due, "due_lang": "en"} if due else {})}
        try:
            r = await self._http.post(TODOIST_URL, json=body, headers=self._auth)
            if r.status_code == 400 and due:
                # Todoist couldn't read the due date ("in 30 seconds"): keep the task, drop the date.
                log.warning("todoist rejected due %r, adding without it", due)
                r = await self._http.post(TODOIST_URL, json={"content": content}, headers=self._auth)
                if r.status_code == 200:
                    return Added(str(r.json()["id"]), due_dropped=True)
            if r.status_code in (401, 403):
                return Added(None, error="Todoist key's wrong")
            r.raise_for_status()
            return Added(str(r.json()["id"]))
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("todoist add failed: %s", type(e).__name__)  # no URL or headers: never the key
            return Added(None, error="Couldn't reach Todoist")

    async def list(self, query: str = "today | overdue") -> list[Task]:
        if not self._key:
            return []
        try:
            r = await self._http.get(f"{TODOIST_URL}/filter", params={"query": query}, headers=self._auth)
            r.raise_for_status()
            rows = r.json().get("results", [])
        except (httpx.HTTPError, ValueError) as e:
            log.warning("todoist list failed: %s", type(e).__name__)
            return []
        return [Task(str(t["id"]), t.get("content", ""), (t.get("due") or {}).get("string"),
                     ((t.get("due") or {}).get("date") or "")[:10] or None) for t in rows if "id" in t]

    async def close(self, tid: str) -> bool:
        try:
            r = await self._http.post(f"{TODOIST_URL}/{tid}/close", headers=self._auth)
            return r.status_code in (200, 204)
        except httpx.HTTPError:
            return False

    async def reopen(self, tid: str) -> bool:
        try:
            r = await self._http.post(f"{TODOIST_URL}/{tid}/reopen", headers=self._auth)
            return r.status_code in (200, 204)
        except httpx.HTTPError:
            return False

    async def delete(self, tid: str) -> bool:
        try:
            r = await self._http.delete(f"{TODOIST_URL}/{tid}", headers=self._auth)
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


def _say_in(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s} seconds"
    if s < 3600:
        m = round(s / 60)
        return "1 minute" if m == 1 else f"{m} minutes"
    h, m = divmod(round(s / 60), 60)
    return (f"{h} hour" + ("s" if h != 1 else "")) + (f" {m} minutes" if m else "")


class Remember:
    def __init__(self, talker, hands, todoist: Todoist, facts: FactStore, calendar: CalendarStore, undo,
                 now: Callable[[], datetime] = lambda: datetime.now(TZ), timers=None):
        self._talker, self._hands, self._todoist = talker, hands, todoist
        self.facts, self._cal, self._undo, self._now = facts, calendar, undo, now
        self._timers = timers
        self.todoist = todoist

    async def run(self, where: str | None, text: str) -> Remembered:
        if where == "event":
            return await self._event(text)
        if where == "fact":
            return await self._fact(text)
        if where == "reminder":
            return await self._reminder(text)
        return await self._task(text)

    async def _reminder(self, text: str) -> Remembered:
        now = self._now()
        x = await self._talker.extract(REMINDER_Q.format(today=now.strftime("%A %-d %B %Y"),
                                                         now=now.strftime("%H:%M")), text)
        what = str(x.get("what") or x.get("content") or "").strip()
        if not what:
            return Remembered("What should I remind you about?", ok=False)
        # "in 30 seconds / in 20 minutes" is read by plain code; clock times come from Groq's JSON.
        m = _IN_DURATION.search(text)
        seconds = float(parse_duration(m.group(0))) if m and parse_duration(m.group(0)) else None
        at: datetime | None = None
        if seconds is None and x.get("time"):
            try:
                t = time.fromisoformat(str(x["time"]))
                day = date.fromisoformat(str(x["date"])) if x.get("date") else now.date()
            except ValueError:
                t, day = None, None
            if t is not None:
                at = datetime.combine(day, t, TZ)
                if at <= now and not x.get("date"):
                    at += timedelta(days=1)  # "at 7" said at 8pm means tomorrow morning
                seconds = (at - now).total_seconds()
        if seconds is not None and 0 < seconds <= REMINDER_MAX_S and self._timers is not None:
            timer = self._timers.start(seconds, label=what)

            async def undo() -> str:
                tid = getattr(timer, "id", None)
                return "Reminder cancelled." if self._timers.cancel(tid) else "That reminder already went off."

            self._undo.remember_undo(undo)
            when = f"at {spoken_time(at.time())}" if at else f"in {_say_in(seconds)}"
            return Remembered(f"Okay, {when} I'll remind you to {what}.")
        due = str(x.get("due")).strip() if x.get("due") else None
        return await self._add_task(what[:1].upper() + what[1:], due)

    async def _event(self, text: str) -> Remembered:
        now = self._now()
        x = await self._talker.extract(EVENT_Q.format(today=now.strftime("%A %-d %B %Y")), text)
        title = str(x.get("title") or "").strip()
        try:
            day = date.fromisoformat(str(x.get("date")))
        except ValueError:
            day = None
        if day is None or day < now.date() or day > now.date() + timedelta(days=366):
            return Remembered("I couldn't work out when that is.", ok=False)
        if not title:
            return Remembered(None, ask="What's it called?")
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
        return await self._add_task(content, due)

    async def _add_task(self, content: str, due: str | None) -> Remembered:
        added = await self._todoist.add(content, due)
        if isinstance(added, str) or added is None:  # older fakes return the id directly
            added = Added(added)
        if not added.id:
            reason = added.error or "Couldn't reach Todoist"
            return Remembered(f"{reason}, try again in a bit.", ok=False)
        tid = added.id

        async def undo() -> str:
            return f"Took {content} off Todoist." if await self._todoist.delete(tid) else "Todoist wouldn't let me."

        self._undo.remember_undo(undo)
        if added.due_dropped:
            return Remembered(f"Added to Todoist: {content}, but I couldn't set the time.")
        return Remembered(f"Added to Todoist: {content}" + (f", {due}." if due else "."))

    async def _fact(self, text: str) -> Remembered:
        fact = str((await self._talker.extract(FACT_Q, text)).get("fact") or "").strip()
        if not fact:
            # Groq stalled or said nothing: keep his own words rather than lose the fact.
            said = re.sub(r"^\s*(please\s+)?(can you\s+)?remember( that)?[\s,:]*", "", text, flags=re.IGNORECASE)
            said = said.strip().rstrip(".!?").strip()
            if not said:
                return Remembered("I didn't catch what to remember.", ok=False)
            fact = f"Isaac said: {said}."
        self.facts.add(fact)

        async def undo() -> str:
            self.facts.remove_last()
            return "Forgot it."

        self._undo.remember_undo(undo)
        return Remembered("Got it, I'll remember that.")
