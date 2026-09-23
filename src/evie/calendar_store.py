"""Isaac's calendar, as pushed by the menu bar app from macOS Calendar (EventKit).

The core never logs in to Google. The app already has Calendar access, so it sends the next
8 days here every 5 minutes and whenever the calendar changes. This file just keeps that
snapshot and answers "what's on <day>?".
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Singapore")
STALE_AFTER = timedelta(minutes=30)


@dataclass(frozen=True)
class CalEvent:
    title: str
    start: datetime
    end: datetime
    all_day: bool
    calendar: str


class CalendarStore:
    def __init__(self) -> None:
        self._events: list[CalEvent] = []
        self._updated: datetime | None = None

    def update(self, events: list[CalEvent], at: datetime) -> None:
        self._events = list(events)
        self._updated = at

    @property
    def updated_at(self) -> datetime | None:
        return self._updated

    def stale(self, now: datetime) -> bool:
        return self._updated is None or now - self._updated > STALE_AFTER

    def on(self, day: date) -> list[CalEvent]:
        day_start = datetime.combine(day, time(), TZ)
        day_end = day_start + timedelta(days=1)
        hits = [e for e in self._events if e.start < day_end and e.end > day_start]
        return sorted(hits, key=lambda e: (not e.all_day, e.start))

    def summary(self, day: date, today: date | None = None) -> str:
        today = today or datetime.now(TZ).date()
        if day == today:
            label = "today"
        elif day == today + timedelta(days=1):
            label = "tomorrow"
        else:
            label = day.strftime("%A")
        events = self.on(day)
        if not events:
            return f"Nothing on {label}."
        parts = [f"{e.title} (all day)" if e.all_day
                 else f"{e.start.astimezone(TZ):%-H:%M} {e.title}" for e in events]
        return f"{label[0].upper()}{label[1:]}: {', '.join(parts)}"
