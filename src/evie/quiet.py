"""Text-only mode (Phase 5 "adaptive output"): voice or text, decided by his calendar.

Isaac, 2026-09-24: "when I'm in school I don't want it to give answers out loud ... depending on the
calendar, if I have school or a chem or like Spanish or some class going on ... and a toggle as well".

  auto   text while a class is on (School, Chem Class, Sax Class, Spanish Class ...) or in a call
  voice  / text   his toggle. During a class it lasts until the class ends; otherwise until he
                  switches back.

Text mode means Evie never makes a sound (voice.Mouth checks this before every line) and the open
mic pauses while a class is on (teachers aren't Isaac, and school may not allow recording).
"""
import re
from datetime import datetime
from typing import Callable

from evie.calendar_store import TZ, CalendarStore, CalEvent

CLASS = re.compile(r"\b(school|class|classes|tuition|lesson|lessons|lecture|exam|exams|test|quiz|mock|seminar|"
                   r"tutorial|practical)\b", re.I)


class Quiet:
    def __init__(self, calendar: CalendarStore, in_call: Callable[[], bool] = lambda: False,
                 now: Callable[[], datetime] = lambda: datetime.now(TZ)):
        self._cal, self._in_call, self._now = calendar, in_call, now
        self._override: str | None = None  # "voice" | "text"
        self._until: datetime | None = None  # the override ends here (the class's end), or never

    def current_class(self) -> CalEvent | None:
        now = self._now()
        for e in self._cal.on(now.astimezone(TZ).date()):
            if not e.all_day and e.start <= now < e.end and CLASS.search(e.title):
                return e
        return None

    def _expire(self) -> None:
        if self._override and self._until is not None and self._now() >= self._until:
            self._override, self._until = None, None

    def set(self, mode: str) -> None:
        """His toggle: "voice", "text" or "auto"."""
        if mode not in ("voice", "text", "auto"):
            raise ValueError(mode)
        if mode == "auto":
            self._override, self._until = None, None
            return
        cls = self.current_class()
        self._override, self._until = mode, (cls.end if cls else None)

    def mode(self) -> str:
        self._expire()
        if self._override:
            return self._override
        return "text" if (self.current_class() or self._in_call()) else "voice"

    def why(self) -> str:
        self._expire()
        if self._override:
            return "you switched it"
        cls = self.current_class()
        if cls:
            return cls.title
        return "a call" if self._in_call() else ""

    def mic_paused(self) -> str | None:
        """The open mic stays off while a class is on, whatever the toggle says."""
        cls = self.current_class()
        return cls.title if cls else None

    def state(self) -> dict:
        self._expire()
        return {"mode": self.mode(), "why": self.why(), "setting": self._override or "auto",
                "until": self._until.isoformat() if self._until else None, "mic_paused": self.mic_paused()}
