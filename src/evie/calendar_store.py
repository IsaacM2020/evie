"""Isaac's calendar, as pushed by the menu bar app from macOS Calendar (EventKit).

The core never logs in to Google. Isaac's Google account is on the Mac, and the app (which has
Calendar access) sends the next 14 days of his Google calendars here every 5 minutes and
whenever the calendar changes. The old iCloud calendars are ignored (Isaac, 2026-09-23).
This file keeps that snapshot, works out "what's on right now" in code (the model only ever
reads the answer), and asks the app for any day further out.
"""
import json
from dataclasses import dataclass
from functools import lru_cache
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Singapore")
STALE_AFTER = timedelta(minutes=11)  # the app pushes every 5 minutes: two missed means it's gone
HORIZON_DAYS = 14


@dataclass(frozen=True)
class CalEvent:
    title: str
    start: datetime
    end: datetime
    all_day: bool
    calendar: str
    id: str = ""


def _hm(dt: datetime) -> str:
    return f"{dt.astimezone(TZ):%-H:%M}"


def _day_label(day: date, today: date) -> str:
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    if 0 < (day - today).days < 7:
        return day.strftime("%A")
    return day.strftime("%A %-d %b")


@lru_cache(maxsize=4)
def _sg_holidays(year: int):
    import holidays
    return holidays.country_holidays("SG", years=year)


def _public_holidays(day: date) -> list[str]:
    """Singapore public holidays, worked out locally (Isaac wanted them; no calendar to sync)."""
    name = _sg_holidays(day.year).get(day)
    return [n.strip() for n in name.split(";")] if name else []


def _describe(events: list[CalEvent], label: str, day: date | None = None) -> str:
    parts = [f"{h} (public holiday, all day)" for h in (_public_holidays(day) if day else [])]
    parts += [f"{e.title} (all day)" if e.all_day else f"{_hm(e.start)}-{_hm(e.end)} {e.title}" for e in events]
    if not parts:
        return f"Nothing on {label}."
    return f"{label[0].upper()}{label[1:]}: {', '.join(parts)}"


def _sorted_for_day(events: list[CalEvent], day: date) -> list[CalEvent]:
    day_start = datetime.combine(day, time(), TZ)
    day_end = day_start + timedelta(days=1)
    hits = [e for e in events if e.start < day_end and e.end > day_start]
    return sorted(hits, key=lambda e: (not e.all_day, e.start))


class CalendarStore:
    def __init__(self) -> None:
        self._events: list[CalEvent] = []
        self._updated: datetime | None = None
        self.calendars: list[dict] = []  # what the app reported: which calendars are read

    def update(self, events: list[CalEvent], at: datetime, calendars: list[dict] | None = None) -> None:
        self._events = list(events)
        self._updated = at
        if calendars is not None:
            self.calendars = calendars

    @property
    def updated_at(self) -> datetime | None:
        return self._updated

    def stale(self, now: datetime) -> bool:
        return self._updated is None or now - self._updated > STALE_AFTER

    def covers(self, day: date) -> bool:
        if self._updated is None:
            return False
        first = self._updated.astimezone(TZ).date()
        return first <= day < first + timedelta(days=HORIZON_DAYS)

    def on(self, day: date) -> list[CalEvent]:
        return _sorted_for_day(self._events, day)

    def summary(self, day: date, today: date | None = None) -> str:
        today = today or datetime.now(TZ).date()
        return _describe(self.on(day), _day_label(day, today), day)

    def now_line(self, now: datetime) -> str:
        """What's happening right now and what's next, worked out by code (the model got this
        wrong from a list of start times: "you're in the middle of Dipanjan")."""
        if self._updated is None:
            return "Calendar not connected yet."
        timed = sorted((e for e in self._events if not e.all_day), key=lambda e: e.start)
        current = [e for e in timed if e.start <= now < e.end]
        upcoming = next((e for e in timed if e.start > now), None)
        if current:
            line = "Right now: " + ", ".join(f"{e.title} until {_hm(e.end)}" for e in current) + "."
        else:
            line = "Nothing on right now" + ("." if upcoming else ", and nothing else coming up.")
        if upcoming:
            when = _day_label(upcoming.start.astimezone(TZ).date(), now.astimezone(TZ).date())
            line += f" Next: {upcoming.title}" + ("" if when == "today" else f" {when}") + f" at {_hm(upcoming.start)}."
        return line


def _utc(dt: datetime) -> str:
    return dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(rows: list[dict]) -> list[CalEvent]:
    out = []
    for r in rows:
        try:
            out.append(CalEvent(title=r["title"], start=datetime.fromisoformat(r["start"].replace("Z", "+00:00")),
                                end=datetime.fromisoformat(r["end"].replace("Z", "+00:00")),
                                all_day=bool(r.get("all_day")), calendar=r.get("calendar", ""), id=r.get("id", "")))
        except (KeyError, ValueError, AttributeError):
            continue
    return out


async def lookup(store: CalendarStore, hands, day: date, today: date | None = None) -> str:
    """Any day: the pushed fortnight answers straight away; further out, the app looks it up."""
    today = today or datetime.now(TZ).date()
    if store.covers(day):
        return store.summary(day, today=today)
    label = _day_label(day, today)
    start = datetime.combine(day, time(), TZ)
    r = await hands.do("calendar_query", start=_utc(start), end=_utc(start + timedelta(days=1)))
    if not r.ok:
        return f"Couldn't check {label}: the Evie app isn't answering."
    try:
        rows = json.loads(r.data.get("events", "[]"))
    except ValueError:
        rows = []
    return _describe(_sorted_for_day(_parse(rows), day), label, day)
