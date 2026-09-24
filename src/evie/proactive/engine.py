"""WHEN and HOW Evie brings something up.

Code gates first (cheap, can't be argued with):
  - she isn't talking, nobody has talked to or near her for a minute, he isn't in a call
  - one at a time: 90 s after anything she said by herself
  - at most 3 spoken an hour
Then, for anything that isn't time-critical, one Jev question: is this a good moment?
In class (text mode), in quiet hours (22:30-07:00) or over the cap it becomes a chip on the orb instead:
he sees it when he looks, nothing is said.
"""
import logging
import time
from typing import Awaitable, Callable

from evie.jev import JevError
from evie.proactive.queue import FollowUp, FollowUps

log = logging.getLogger("evie.proactive")

IDLE_S = 60.0  # nobody spoke to or near her for this long
GAP_S = 90.0  # between two things she brings up by herself
PER_HOUR = 3
LATER_S = 1800.0
HOLD_S = 600.0  # Jev said "not now": try again in 10 min
QUIET_FROM, QUIET_TO = 22.5, 7.0


class Engine:
    def __init__(self, queue: FollowUps, mouth, bus, jev, act: Callable[[FollowUp], Awaitable[None]] | None,
                 idle_s: Callable[[], float], text_mode: Callable[[], bool], in_call: Callable[[], bool],
                 hour: Callable[[], float] | None = None, clock: Callable[[], float] = time.time,
                 context: Callable[[], str] = lambda: "", on_spoken: Callable[[FollowUp], None] | None = None):
        self.queue, self.mouth, self.bus, self._jev, self._act = queue, mouth, bus, jev, act
        self._idle, self._text, self._call, self._clock, self._context = idle_s, text_mode, in_call, clock, context
        self._hour = hour or (lambda: _local_hour(clock()))
        self._on_spoken = on_spoken  # the Brain waits for his answer to a spoken question
        self._spoken: list[float] = []
        # Nothing is brought up until his calendar has arrived: an empty calendar makes class look
        # like free time (2026-09-24 16:57, right after a restart, during Sax class).
        self.ready: Callable[[], bool] = lambda: True

    def add(self, it: FollowUp) -> bool:
        ok = self.queue.add(it)
        if ok:
            log.info("follow-up queued: %s %s", it.kind, it.source_key)
        return ok

    def free(self) -> bool:
        """Could she speak up right now without talking over anyone?"""
        now = self._clock()
        return (not getattr(self.mouth, "speaking", False) and self._idle() >= IDLE_S and not self._call()
                and (not self._spoken or now - self._spoken[-1] >= GAP_S))

    def _capped(self) -> bool:
        now = self._clock()
        self._spoken = [t for t in self._spoken if now - t < 3600]
        return len(self._spoken) >= PER_HOUR

    def _quiet_hours(self) -> bool:
        h = self._hour()
        return h >= QUIET_FROM or h < QUIET_TO

    async def tick(self) -> FollowUp | None:
        if not self.ready():
            return None
        due = self.queue.due()
        if not due:
            return None
        it = due[0]
        if it.chip_only or self._text() or self._quiet_hours() or self._capped():
            self._chip(it)
            return it
        if not self.free():
            return None
        if it.importance != "high" and await self._worth(it) < 0.5:
            it.due = self._clock() + HOLD_S
            it.postponed += 1
            if it.postponed >= 3:
                it.chip_only = True  # never found a good moment: leave it on the orb
            self.queue.save()
            return None
        self.mouth.say(it.line, kind="reply")
        self._spoken.append(self._clock())
        it.offered = True
        self.queue.save()
        self.bus.publish("followup", **_card(it), spoken=True)
        if self._on_spoken and (it.ask or it.on_yes):
            self._on_spoken(it)
        return it

    def _chip(self, it: FollowUp) -> None:
        it.offered = True
        self.queue.save()
        self.bus.publish("followup", **_card(it), spoken=False)

    async def _worth(self, it: FollowUp) -> float:
        q = {"now": {"type": "noul", "instructions": (
            "Evie is Isaac's voice assistant on his Mac. Nobody has spoken for a minute. Is this a good moment "
            f'to bring this up out loud, and is it worth interrupting him for: "{it.line}"?')}}
        try:
            res = await self._jev.ask(self._context() or "Isaac is at his Mac.", q)
            return float(res.answers["now"]["noul"])
        except (JevError, KeyError, TypeError, ValueError):
            return 0.6  # Jev down: the code gates already said he's free

    async def answer(self, fid: str, action: str, text: str | None = None) -> bool:
        """His answer, spoken or tapped on the orb: yes / no / later (text: "4pm" for a question)."""
        it = self.queue.get(fid)
        if it is None:
            return False
        if action == "later":
            it.due, it.offered = self._clock() + LATER_S, False
            self.queue.save()
            self.bus.publish("followup_done", id=fid)
            return True
        self.queue.remove(fid)
        self.bus.publish("followup_done", id=fid)
        if action == "yes" and self._act is not None:
            if it.ask and text:
                it.on_yes = {"do": "turn", "text": f'{it.request}. Evie asked "{it.line}", Isaac answered "{text}".'}
            if it.on_yes:
                await self._act(it)
        return True


def _card(it: FollowUp) -> dict:
    return {"id": it.id, "about": it.kind, "line": it.line, "ask": it.ask, "answers": it.options,
            "yes": bool(it.on_yes)}


def _local_hour(t: float) -> float:
    from datetime import datetime

    from evie.calendar_store import TZ
    d = datetime.fromtimestamp(t, TZ)
    return d.hour + d.minute / 60
