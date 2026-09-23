"""The Brain: one sentence in, the right thing happens.

Switchboard (Jev) gives the verdict. This file maps each verdict + route to an action:
speak an answer, ask a question, start a Claude Code job, control the running job, or stay
quiet. Everything that happens goes onto the event bus so the menu bar panel can show it.
"""
import json
import logging
import re
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from evie.calendar_store import TZ, CalendarStore
from evie.events import EventBus
from evie.jev import JevError
from evie.jobs import Busy
from evie.switchboard.context import Context
from evie.switchboard.policy import Action
from evie.voice import ACKS

log = logging.getLogger("evie.brain")

TURNS_LOG = Path.home() / "Library/Logs/Evie/turns.jsonl"
MAX_TURNS = 3

JOB_OP_Q = {
    "job_op": {
        "type": "choice",
        "instructions": "Isaac said this about the background job Evie is running. What does he want?",
        "criteria": {
            "status": "He wants to know how it's going, what it's doing or how long it'll take",
            "stop": "He wants the job stopped, cancelled or killed",
            "add_instruction": "He's adding something for the job to do, or changing what it should do",
        },
    }
}

_WAKE = re.compile(r"^\s*(hey\s+)?(evie|eve|evey|ivy)\b[\s,.:!]*", re.IGNORECASE)


def strip_wake(text: str) -> str:
    """ "Evie, fix the chase bug" -> "fix the chase bug" (the job doesn't need her name)."""
    rest = _WAKE.sub("", text, count=1).strip()
    return rest or text.strip()


class Brain:
    def __init__(self, sb, talker, mouth, runner, narrator, calendar: CalendarStore, bus: EventBus, jev,
                 turns_log: Path | None = TURNS_LOG):
        self._sb, self._talker, self._mouth = sb, talker, mouth
        self._runner, self._narrator, self._cal = runner, narrator, calendar
        self._bus, self._jev, self._log = bus, jev, turns_log
        self._turns: deque[str] = deque(maxlen=MAX_TURNS)

    async def hear(self, text: str, speaker: str = "isaac") -> dict:
        t0 = time.perf_counter()
        self._bus.publish("heard", text=text)
        self._bus.publish("state", state="thinking")
        job = self._runner.current
        ctx = Context(utterance=text, speaker=speaker, recent=tuple(self._turns),
                      active_jobs=(job.goal,) if job else ())
        o = await self._sb.handle(ctx)
        route = o.decision.route if o.decision else None
        t_verdict = time.perf_counter()
        self._bus.publish("verdict", action=o.verdict.action.value, reason=o.verdict.reason, route=route)
        said = await self._act(o.verdict, route, text)
        t_said = time.perf_counter()
        if said:
            self._turns.append(f'Isaac: "{text}" / Evie: "{said}"')
        self._bus.publish("state", state="working" if self._runner.current else "idle")
        self._write_log({
            "t": time.time(), "text": text, "action": o.verdict.action.value, "reason": o.verdict.reason,
            "route": route, "said": said,
            "ms_to_verdict": round((t_verdict - t0) * 1000),
            "ms_to_speech_queued": round((t_said - t0) * 1000),
            "jev_ms": round(o.decision.latency_ms) if o.decision else None,
        })
        return {"text": text, "action": o.verdict.action.value, "reason": o.verdict.reason,
                "route": route, "said": said}

    async def _act(self, verdict, route: str | None, text: str) -> str | None:
        if verdict.action == Action.IGNORE:
            return None
        if verdict.action == Action.CLARIFY:
            if verdict.reason == "missing detail":
                return self._say(await self._talker.clarify(text, "a detail is missing"))
            return self._clip("for_me")
        if route == "answer":
            return self._say(await self._talker.reply(text, self._facts()))
        if route == "deep_job":
            return await self._start_job(text)
        if route == "job_control":
            return await self._job_control(text)
        return self._clip("not_yet")  # quick_action / remember land in Phase 3

    async def _start_job(self, text: str) -> str:
        running = self._runner.current
        if running:
            return self._say(f"Still on {running.goal}. Say stop first.")
        said = self._clip("on_it")
        try:
            job = await self._runner.start(strip_wake(text))
        except Busy as e:
            return self._say(f"Still on {e}. Say stop first.")
        self._narrator.start(job)
        self._bus.publish("job_started", id=job.id, goal=job.goal)
        return said

    async def _job_control(self, text: str) -> str:
        if not self._runner.current:
            return self._say("Nothing running right now.")
        try:
            res = await self._jev.ask(
                f"Evie is working on: {self._runner.current.goal}\nIsaac just said: \"{text}\"", JOB_OP_Q)
            op = res.answers["job_op"]["choice"]
        except (JevError, KeyError, TypeError):
            op = "status"  # unsure: the safe, read-only answer, never a stop
        if op == "stop":
            job = self._runner.current
            await self._runner.stop()
            self._bus.publish("job_done", id=job.id, status="stopped", summary="Stopped.", result="")
            return self._say("Stopped.")
        if op == "add_instruction":
            await self._runner.add_instruction(strip_wake(text))
            return self._say("Got it, passing that on.")
        return self._say(self._runner.status_line())

    def _facts(self) -> dict:
        now = datetime.now(TZ)
        if self._cal.updated_at is None:
            today = tomorrow = "unknown, calendar not connected yet"
        else:
            today = self._cal.summary(now.date())
            tomorrow = self._cal.summary(now.date() + timedelta(days=1))
        return {"now": now.strftime("%a %-d %b %Y, %H:%M"), "calendar_today": today,
                "calendar_tomorrow": tomorrow, "job": self._runner.status_line()}

    def _say(self, text: str) -> str:
        self._mouth.say(text, kind="reply")
        return text

    def _clip(self, name: str) -> str:
        self._mouth.play_clip(name)
        return ACKS[name]

    def _write_log(self, row: dict) -> None:
        if not self._log:
            return
        try:
            self._log.parent.mkdir(parents=True, exist_ok=True)
            with self._log.open("a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            log.exception("couldn't write turns log")
