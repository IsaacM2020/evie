"""Decides which Claude Code steps Evie says out loud while a job runs.

Jev answers one yes/no question per step ("worth saying right now?"); plain rules stop her
from chattering (10s gap, 6 per job max). The end-of-job summary is always spoken.
"""
import logging
import time
from dataclasses import dataclass
from typing import Callable

from evie.events import EventBus
from evie.jev import JevError
from evie.jobs import Job
from evie.talk import FALLBACK

log = logging.getLogger("evie.narrator")

NARRATE_Q = {
    "worth_saying": {
        "type": "noul",
        "instructions": (
            "Isaac is busy doing something else while Evie works on a job for him in the background. "
            "Would he want to hear this progress update said out loud right now? Yes for real "
            "progress or news: a bug found, a fix made, tests passing or failing, a research finding "
            "or answer, a result, a problem or blocker. No for routine steps like reading a file, "
            "searching, listing folders or planning."
        ),
    }
}


@dataclass(frozen=True)
class NarrationRules:
    threshold: float = 0.60
    min_gap_s: float = 10.0
    max_per_job: int = 6


def render_step(goal: str, line: str, since_s: float | None, count: int) -> str:
    since = "none yet" if since_s is None else f"{int(since_s)} seconds ago"
    return (
        f"Evie is working in the background on: {goal}\n"
        f"Updates already said out loud this job: {count} (last one {since})\n"
        f"Latest step: {line}"
    )


class Narrator:
    def __init__(self, jev, talker, mouth, bus: EventBus, rules: NarrationRules = NarrationRules(),
                 clock: Callable[[], float] = time.monotonic):
        self._jev, self._talker, self._mouth, self._bus = jev, talker, mouth, bus
        self._r, self._clock = rules, clock
        self._last: dict[str, float] = {}
        self._count: dict[str, int] = {}

    def start(self, job: Job) -> None:
        """Called right after "On it", which counts as the last thing she said."""
        self._last[job.id] = self._clock()
        self._count[job.id] = 0

    async def worth_saying(self, goal: str, line: str, since_s: float | None, count: int) -> float:
        try:
            res = await self._jev.ask(render_step(goal, line, since_s, count), NARRATE_Q)
            return float(res.answers["worth_saying"]["noul"])
        except (JevError, KeyError, TypeError, ValueError):
            return 0.0

    async def on_event(self, job: Job, line: str) -> None:
        self._bus.publish("job_event", id=job.id, line=line)
        count = self._count.get(job.id, 0)
        if count >= self._r.max_per_job:
            return
        last = self._last.get(job.id)
        since = None if last is None else self._clock() - last
        if since is not None and since < self._r.min_gap_s:
            return
        if await self.worth_saying(job.goal, line, since, count) < self._r.threshold:
            return
        text = await self._talker.narrate(job.goal, line)
        if text == FALLBACK:
            return
        self._mouth.say(text, kind="narration", ttl_s=15)
        self._last[job.id] = self._clock()
        self._count[job.id] = count + 1

    async def on_done(self, job: Job) -> None:
        result = job.result if job.status == "done" else f"FAILED: {job.result}"
        text = await self._talker.summarize(job.goal, result)
        self._mouth.say(text, kind="reply")
        self._bus.publish("job_done", id=job.id, status=job.status, summary=text, result=job.result[:2000])
        self._last.pop(job.id, None)
        self._count.pop(job.id, None)
