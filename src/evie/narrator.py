"""Decides which Claude Code steps Evie says out loud while a job runs.

Jev answers one yes/no question per step ("worth saying right now?"); plain rules stop her
from chattering (10s gap, 6 per job max). The end-of-job summary is always spoken.
"""
import asyncio
import logging
import re
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


# Steps code already knows aren't news: reading, listing, searching. They still show on the panel,
# they just never cost a Jev call (2026-09-24: each "Ran: ls" was a call scoring ~0.10).
_ROUTINE_PREFIX = ("Read ", "Searched for ", "Looked for files", "Opened ", "Used ")
_ROUTINE_CMD = re.compile(
    r"^Ran: (cd |ls|cat |head |tail |find |grep |rg |pwd|wc |echo |which |sed -n |stat |file |tree"
    r"|git (status|log|diff|show|branch|remote))")
# Notices from Isaac's own Claude Code setup (memory tools, hooks) that leak into a job's words.
_NOTICE = re.compile(r"claude-mem|memory (system|observer|tool)|\bhook\b|allowance|outage", re.IGNORECASE)


def routine(line: str) -> bool:
    return line.startswith(_ROUTINE_PREFIX) or bool(_ROUTINE_CMD.match(line)) or bool(_NOTICE.search(line))


@dataclass(frozen=True)
class NarrationRules:
    threshold: float = 0.45  # was 0.60: most steps scored 0.1-0.3 and she went silent for minutes
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
                 clock: Callable[[], float] = time.monotonic, heartbeat_s: float = 30.0, tick_s: float = 2.0,
                 can_speak: Callable[[], bool] = lambda: True):
        self._jev, self._talker, self._mouth, self._bus = jev, talker, mouth, bus
        self._r, self._clock = rules, clock
        self._heartbeat_s, self._tick_s, self._can_speak = heartbeat_s, tick_s, can_speak
        self._last: dict[str, float] = {}
        self._count: dict[str, int] = {}
        self._said: dict[str, str] = {}  # the last update she said per job (updates build on it)

    def start(self, job: Job) -> None:
        """Called right after "On it", which counts as the last thing she said."""
        self._last[job.id] = self._clock()
        self._count[job.id] = 0
        asyncio.get_running_loop().create_task(self._heartbeat(job))

    async def _heartbeat(self, job: Job) -> None:
        """Never minutes of silence: after ~25 s with nothing said, a one-line "still going" update
        from the latest steps. Skipped while Isaac is talking or in a call, or she's already speaking."""
        while job.status == "running":
            await asyncio.sleep(self._tick_s)
            last = self._last.get(job.id)
            if job.status != "running" or last is None or not job.events:
                continue
            if self._clock() - last < self._heartbeat_s or getattr(self._mouth, "speaking", False):
                continue
            if not self._can_speak():
                continue
            self._last[job.id] = self._clock()
            p = job.progress() if hasattr(job, "progress") else None
            if p:  # the real step, from its own plan: no model needed
                text = f"Still going. Step {p[0]} of {p[1]}, {p[2][:1].lower() + p[2][1:]}."
            else:
                text = await self._talker.narrate(job.goal, "still working. Latest steps: " + "; ".join(job.events[-3:]),
                                                  **self._last_kw(job))
            if text != FALLBACK and job.status == "running":
                self._mouth.say(text, kind="narration", ttl_s=15)
                self._said[job.id] = text

    async def worth_saying(self, goal: str, line: str, since_s: float | None, count: int) -> float:
        try:
            res = await self._jev.ask(render_step(goal, line, since_s, count), NARRATE_Q)
            return float(res.answers["worth_saying"]["noul"])
        except (JevError, KeyError, TypeError, ValueError):
            return 0.0

    def _last_kw(self, job: Job) -> dict:
        last = self._said.get(job.id, "")
        return {"last": last} if last else {}

    async def on_event(self, job: Job, line: str) -> None:
        self._bus.publish("job_event", id=job.id, line=line)
        p = job.progress() if hasattr(job, "progress") else None
        if p and line.startswith("Step "):  # the orb's progress ring and step line
            self._bus.publish("job_progress", id=job.id, done=p[0] - 1, total=p[1], step=p[2])
        if routine(line):
            return
        count = self._count.get(job.id, 0)
        if count >= self._r.max_per_job:
            return
        last = self._last.get(job.id)
        since = None if last is None else self._clock() - last
        if since is not None and since < self._r.min_gap_s:
            return
        p = await self.worth_saying(job.goal, line, since, count)
        log.info("narrate? p=%.2f %s", p, line[:80])
        if p < self._r.threshold:
            return
        text = await self._talker.narrate(job.goal, line, last=self._said.get(job.id, ""))
        if text == FALLBACK:
            log.info("narration skipped: Groq fallback")
            return
        self._mouth.say(text, kind="narration", ttl_s=15)
        self._said[job.id] = text
        self._last[job.id] = self._clock()
        self._count[job.id] = count + 1

    async def on_done(self, job: Job) -> None:
        result = job.result if job.status == "done" else f"FAILED: {job.result}"
        text = await self._talker.summarize(job.goal, result)
        self._mouth.say(text, kind="reply")
        self._bus.publish("job_done", id=job.id, status=job.status, summary=text, result=job.result[:2000])
        self._last.pop(job.id, None)
        self._count.pop(job.id, None)
        self._said.pop(job.id, None)
