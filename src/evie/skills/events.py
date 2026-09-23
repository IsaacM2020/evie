"""Changing Isaac's calendar by voice: "move sax to 5", "delete my dentist".

Jev picks WHICH event from the real upcoming ones (a choice question, so it can't invent one);
Groq only reads the new time; plain code keeps the length and checks the dates. Moves happen
straight away and can be undone. Deletes are announced and wait 5 s for "stop" first.
"""
from datetime import date, datetime, time, timedelta
from typing import Callable

from evie.calendar_store import TZ, CalendarStore, CalEvent
from evie.countdown import Countdown
from evie.jev import JevError
from evie.remember import _utc, spoken_day, spoken_time
from evie.skills.catalog import Done

MOVE_Q = ('Isaac wants to move a calendar event. It is currently on {day}. Today is {today}. Return '
          '{{"date": "YYYY-MM-DD" or null if he did not say a new day, "time": "HH:MM" 24-hour or null}}.')
MAX_OPTIONS = 60


def _label(e: CalEvent, today: date) -> str:
    """ "Sax Class, Thursday 24 Sep 16:45 (tomorrow)": the weekday AND the relative day, so Jev can
    match either way Isaac says it."""
    d = e.start.astimezone(TZ)
    rel = {0: " (today)", 1: " (tomorrow)"}.get((d.date() - today).days, "")
    return f"{e.title}, {d:%A %-d %b %H:%M}{rel}"


class EventSkills:
    def __init__(self, hands, talker, jev, calendar: CalendarStore, undo, countdown: Countdown,
                 now: Callable[[], datetime] = lambda: datetime.now(TZ), conversation=None):
        self._hands, self._talker, self._jev, self._cal = hands, talker, jev, calendar
        self._undo, self._countdown, self._now = undo, countdown, now
        self._conv = conversation  # so "move it to 5" knows what "it" is

    def _upcoming(self) -> list[CalEvent]:
        now = self._now()
        days = [now.date() + timedelta(days=d) for d in range(14)]
        seen, out = set(), []
        for day in days:
            for e in self._cal.on(day):
                if e.id and not e.all_day and e.end > now and e.id not in seen:
                    seen.add(e.id)
                    out.append(e)
        return out[:MAX_OPTIONS]

    async def _pick(self, text: str) -> CalEvent | None | str:
        events = self._upcoming()
        if not events:
            return None
        today = self._now().date()
        options = {e.id: _label(e, today) for e in events} | {"none": "None of these events"}
        q = {"event": {"type": "choice", "instructions": "Which of Isaac's calendar events does he mean?",
                       "criteria": options}}
        try:
            recent = self._conv.lines(4) if self._conv is not None else []
            state = ("Recent conversation: " + " | ".join(recent) + "\n" if recent else "") + f'Isaac said: "{text}"'
            a = (await self._jev.ask(state, q)).answers["event"]
        except (JevError, KeyError, TypeError):
            return "unsure"
        choice = a.get("choice")
        if choice == "none":
            return None
        if choice not in options or float(a.get("confidence", 0)) < 0.5:
            return "unsure"
        return next(e for e in events if e.id == choice)

    async def move(self, text: str) -> Done:
        e = await self._pick(text)
        if e == "unsure":
            return Done("Which event?", ok=False, detail="unsure which event")
        if e is None:
            return Done("I can't find that on your calendar.", ok=False, detail="no such event")
        now = self._now()
        start0 = e.start.astimezone(TZ)
        x = await self._talker.extract(MOVE_Q.format(day=start0.strftime("%A %-d %B"),
                                                     today=now.strftime("%A %-d %B %Y")), text)
        try:
            day = date.fromisoformat(str(x.get("date"))) if x.get("date") else start0.date()
            t = time.fromisoformat(str(x.get("time"))) if x.get("time") else None
        except ValueError:
            day, t = start0.date(), None
        if t is None and day == start0.date():
            return Done("To when?", ok=False, detail="no new time")
        start = datetime.combine(day, t or start0.time(), TZ)
        if start < now or start > now + timedelta(days=366):
            return Done("That time doesn't work, it's in the past.", ok=False, detail="bad time")
        end = start + (e.end - e.start)
        r = await self._hands.do("calendar_move", id=e.id, start=_utc(start), end=_utc(end))
        if not r.ok:
            return Done(f"Couldn't move it: {r.detail}.", ok=False, detail=r.detail)
        old = (r.data.get("old_start") or _utc(e.start), r.data.get("old_end") or _utc(e.end))

        async def undo() -> str:
            u = await self._hands.do("calendar_move", id=e.id, start=old[0], end=old[1])
            return f"{e.title} is back where it was." if u.ok else f"Couldn't move it back: {u.detail}."

        self._undo.remember_undo(undo)
        return Done(f"Moved {e.title} to {spoken_day(day, now.date())} at {spoken_time(start.time())}.", verified=True)

    async def delete(self, text: str) -> Done:
        e = await self._pick(text)
        if e == "unsure":
            return Done("Which event?", ok=False, detail="unsure which event")
        if e is None:
            return Done("I can't find that on your calendar.", ok=False, detail="no such event")
        now = self._now()
        s = e.start.astimezone(TZ)
        when = f"{spoken_day(s.date(), now.date())} at {spoken_time(s.time())}"

        async def really_delete() -> None:
            r = await self._hands.do("calendar_delete", id=e.id)
            if not r.ok:
                return
            was = r.data

            async def undo() -> str:
                u = await self._hands.do("calendar_add", title=was.get("title") or e.title,
                                         start=was.get("start") or _utc(e.start), end=was.get("end") or _utc(e.end),
                                         all_day=was.get("all_day") == "true")
                return f"Put {e.title} back." if u.ok else f"Couldn't put it back: {u.detail}."

            self._undo.remember_undo(undo)

        self._countdown.start(really_delete)
        return Done(f"Deleting {e.title}, {when}. Say stop to cancel.")
