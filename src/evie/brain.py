"""The Brain: one sentence in, the right thing happens.

Switchboard (Jev) gives the verdict. This file maps each verdict + route to an action:
speak an answer, ask a question, start a Claude Code job, control the running job, or stay
quiet. Everything that happens goes onto the event bus so the menu bar panel can show it.
"""
import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from evie.calendar_store import TZ, CalendarStore
from evie.events import EventBus
from evie.jev import JevError
from evie.jobs import Busy
from evie.skills.catalog import RISK, allowed
from evie.switchboard.context import Context
from evie.switchboard.policy import Action
from evie.voice import ACKS

log = logging.getLogger("evie.brain")

TURNS_LOG = Path.home() / "Library/Logs/Evie/turns.jsonl"
MAX_TURNS = 3
PENDING_S = 15.0  # how long Evie waits for the answer to a question she asked
FOLLOWUP_S = 10.0
SKILL_CONF_MIN = 0.5  # below this Jev isn't sure which fast skill: Claude Code handles it  # after she answers, a follow-up without her name may still be for her

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


_STOP = re.compile(r"^((hey )?(evie|eve|evey|ivy) )?(stop( talking| it)?|shut up|be quiet|quiet|"
                   r"never ?mind|cancel( that)?|thats enough|enough)$")
_YES = re.compile(r"^(yes|yeah|yep|yup|ya|yah|sure|mhm+|mm hmm|uh huh|correct|it was|i was)\b")
_NO = re.compile(r"^(no|nope|nah|not you|it wasnt|i wasnt)\b")


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", "", text.lower().replace(",", " ")).split())


def is_stop(text: str) -> bool:
    """"Evie stop", "shut up", "never mind": handled on the Mac instantly, no Jev round trip."""
    return bool(_STOP.match(_norm(text)))


@dataclass
class Pending:
    """A question Evie just asked. Isaac's next sentence is probably the answer."""
    kind: str  # "for_me" ("Was that for me?") or "detail" ("Which song?")
    text: str  # what he said that made her ask
    speaker: str
    at: float


def strip_wake(text: str) -> str:
    """ "Evie, fix the chase bug" -> "fix the chase bug" (the job doesn't need her name)."""
    rest = _WAKE.sub("", text, count=1).strip()
    return rest or text.strip()


class Brain:
    def __init__(self, sb, talker, mouth, runner, narrator, calendar: CalendarStore, bus: EventBus, jev,
                 turns_log: Path | None = TURNS_LOG, clock: Callable[[], float] = time.monotonic,
                 skills=None, remember=None, countdown=None):
        self._sb, self._talker, self._mouth = sb, talker, mouth
        self._runner, self._narrator, self._cal = runner, narrator, calendar
        self._bus, self._jev, self._log, self._clock = bus, jev, turns_log, clock
        self._skills, self._remember = skills, remember
        self._countdown = countdown  # a pending "say stop to cancel" (event delete, 3b sends)
        self._turns: deque[str] = deque(maxlen=MAX_TURNS)
        self._pending: Pending | None = None
        self._last_reply_at: float | None = None
        self.scene: Callable[[], dict] = dict  # the app's view of the Mac: front_app, in_call

    async def hear(self, text: str, speaker: str = "isaac", addressed: bool = True, shadow: bool = False) -> dict:
        """addressed: Isaac held the talk key or typed to Evie, so it's certainly for her.
        addressed=False is the open mic: Jev and the policy decide whether it was for her.
        shadow: open mic trial run. Decide and log what she WOULD do, do nothing."""
        if shadow:
            return await self._shadow(text, speaker)
        # A delete (or a send) waiting on "say stop to cancel": stop calls it off, nothing else.
        if speaker != "other" and is_stop(text) and self._countdown is not None and self._countdown.cancel():
            self._mouth.stop()
            self._write_log({"t": time.time(), "text": text, "action": "act", "reason": "cancelled", "route": None})
            return {"text": text, "action": "act", "reason": "cancelled", "route": None,
                    "said": self._say("Okay, cancelled.")}
        # "Stop" means "stop talking" unless she's silent and a job is running: then it's
        # about the job, and job control (Jev) decides.
        if speaker != "other" and is_stop(text) and (getattr(self._mouth, "speaking", False)
                                                      or not self._runner.current):
            return self._stop(text)
        answered = await self._answer_pending(text, speaker)
        if answered is not None:
            return answered
        return await self._turn(text, speaker, addressed)

    def _context(self, text: str, speaker: str, addressed: bool) -> Context:
        job = self._runner.current
        since = None if self._last_reply_at is None else self._clock() - self._last_reply_at
        scene = self.scene()
        return Context(utterance=text, speaker=speaker, recent=tuple(self._turns),
                       in_call=bool(scene.get("in_call", False)), front_app=str(scene.get("front_app", "")),
                       active_jobs=(job.goal,) if job else (), addressed=addressed,
                       followup_s=since if (not addressed and since is not None and since <= FOLLOWUP_S) else None)

    def _stop(self, text: str) -> dict:
        self._mouth.stop()
        self._pending = None
        self._bus.publish("heard", text=text)
        self._bus.publish("state", state="working" if self._runner.current else "idle")
        self._write_log({"t": time.time(), "text": text, "action": "act", "reason": "stop", "route": None})
        return {"text": text, "action": "act", "reason": "stop", "route": None, "said": None}

    async def _answer_pending(self, text: str, speaker: str) -> dict | None:
        """If Evie just asked something, treat this sentence as the answer (short answers like
        "yes" or "4pm" are too short for voice ID, so anyone but a known other voice counts)."""
        p, self._pending = self._pending, None
        if p is None or self._clock() - p.at > PENDING_S or _WAKE.match(text):
            return None
        if speaker == "other":
            self._pending = p  # not Isaac: keep waiting for him
            return None
        words = _norm(text)
        if p.kind == "for_me":
            if p.speaker != "isaac" and speaker != "isaac":
                # She asked because the voice wasn't clearly Isaac's; only Isaac's matched voice
                # can say yes (a TV can't answer "yes" for another TV line).
                return None
            if _YES.match(words):
                return await self._turn(p.text, p.speaker, addressed=True)
            if _NO.match(words):
                self._write_log({"t": time.time(), "text": text, "action": "ignore", "reason": "not for me"})
                return {"text": text, "action": "ignore", "reason": "not for me", "route": None, "said": None}
            return None  # neither: he moved on, handle it fresh
        return await self._turn(f"{p.text}. {text}", p.speaker, addressed=True)

    async def _shadow(self, text: str, speaker: str) -> dict:
        ctx = self._context(text, speaker, addressed=False)
        o = await self._sb.handle(ctx)
        route = o.verdict.reason if o.verdict.action == Action.ACT else (o.decision.route if o.decision else None)
        detail = route if o.verdict.action == Action.ACT else o.verdict.reason
        would = f"{o.verdict.action.value} · {detail}"
        self._bus.publish("shadow", text=text, speaker=speaker, would=would)
        keep = o.verdict.action != Action.IGNORE  # overheard chatter isn't kept as text
        self._write_log({"t": time.time(), "text": text if keep else None, "speaker": speaker, "shadow": True,
                         "action": o.verdict.action.value, "reason": o.verdict.reason, "route": route,
                         "jev_ms": round(o.decision.latency_ms) if o.decision else None})
        return {"text": text, "action": o.verdict.action.value, "reason": o.verdict.reason, "route": route,
                "said": None, "shadow": True, "would": would}

    async def _turn(self, text: str, speaker: str, addressed: bool) -> dict:
        t0 = time.perf_counter()
        if addressed:
            self._bus.publish("heard", text=text)
            self._bus.publish("state", state="thinking")
        ctx = self._context(text, speaker, addressed)
        # Speed: when Isaac is talking to Evie, draft the spoken answer while Jev decides.
        # If Jev picks "answer" the words are ready; otherwise the draft is dropped.
        draft = asyncio.create_task(self._talker.reply(text, self._facts())) if addressed else None
        try:
            o = await self._sb.handle(ctx)
        except BaseException:
            if draft:
                draft.cancel()
            raise
        # On ACT the policy's pick wins (it can differ from Jev's top route when addressed).
        route = o.verdict.reason if o.verdict.action == Action.ACT else (o.decision.route if o.decision else None)
        t_verdict = time.perf_counter()
        if not addressed and o.verdict.action == Action.IGNORE:
            # Overheard and not for her (Isaac talking to someone): keep it off the panel.
            self._bus.publish("overheard", text=text, reason=o.verdict.reason)
        else:
            if not addressed:
                self._bus.publish("heard", text=text)
            self._bus.publish("verdict", action=o.verdict.action.value, reason=o.verdict.reason, route=route)
        if draft and not (o.verdict.action == Action.ACT and route == "answer"):
            draft.cancel()
            draft = None
        said = await self._act(o.verdict, route, text, draft, speaker, o.decision, addressed)
        t_said = time.perf_counter()
        if said:
            self._turns.append(f'Isaac: "{text}" / Evie: "{said}"')
        if addressed or o.verdict.action != Action.IGNORE:
            self._bus.publish("state", state="working" if self._runner.current else "idle")
        self._write_log({
            "t": time.time(), "speaker": speaker, "addressed": addressed,
            # overheard chatter that wasn't for Evie isn't kept as text
            "text": text if (addressed or o.verdict.action != Action.IGNORE) else None,
            "action": o.verdict.action.value, "reason": o.verdict.reason,
            "route": route, "said": said,
            "ms_to_verdict": round((t_verdict - t0) * 1000),
            "ms_to_speech_queued": round((t_said - t0) * 1000),
            "jev_ms": round(o.decision.latency_ms) if o.decision else None,
        })
        return {"text": text, "action": o.verdict.action.value, "reason": o.verdict.reason,
                "route": route, "said": said}

    async def _act(self, verdict, route: str | None, text: str, draft: asyncio.Task | None = None,
                   speaker: str = "isaac", decision=None, addressed: bool = True) -> str | None:
        if verdict.action == Action.IGNORE:
            return None
        if verdict.action == Action.CLARIFY:
            kind = "for_me" if verdict.reason == "unsure it was for me" else "detail"
            self._pending = Pending(kind, text, speaker, self._clock())
            if verdict.reason == "missing detail":
                return self._say(await self._talker.clarify(text, "a detail is missing"))
            if verdict.reason == "unsure what you meant":
                return self._say(await self._talker.clarify(
                    text, "it's unclear whether he wants an answer, a job done, or something else"))
            return self._clip("for_me")
        if route == "answer":
            return self._say(await draft if draft else await self._talker.reply(text, self._facts()))
        if route == "deep_job":
            return await self._start_job(text)
        if route == "job_control":
            return await self._job_control(text)
        if route == "quick_action" and self._skills:
            return await self._quick(text, decision, speaker, addressed)
        if route == "remember" and self._remember:
            return await self._remember_it(text, decision, speaker)
        return self._clip("not_yet")

    async def _remember_it(self, text: str, decision, speaker: str) -> str:
        try:
            r = await self._remember.run(decision.remember_to if decision else None, strip_wake(text))
        except Exception:
            log.exception("remember failed")
            return self._say("Couldn't save that, try again.")
        if r.ask:  # e.g. "What time?": his next sentence is merged in and this runs again
            self._pending = Pending("detail", text, speaker, self._clock())
            return self._say(r.ask)
        return self._say(r.said)

    async def _quick(self, text: str, decision, speaker: str = "isaac", addressed: bool = True) -> str:
        """A fast skill if Jev is sure which one; otherwise Claude Code, the general hands."""
        skill = decision.skill if decision else None
        if skill and not allowed(RISK.get(skill, "unknown"), speaker, addressed):
            return self._say("That one needs your voice. Say it again, or use the talk key.")
        if skill and skill != "other" and decision.skill_conf >= SKILL_CONF_MIN:
            done = await self._skills.run(skill, strip_wake(text))
            if done.said is not None:
                return self._say(done.said)
        return await self._start_job(text)

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
            today = tomorrow = week = "unknown, calendar not connected yet"
        else:
            today = self._cal.summary(now.date())
            tomorrow = self._cal.summary(now.date() + timedelta(days=1))
            week = " | ".join(self._cal.summary(now.date() + timedelta(days=d)) for d in range(2, 8))
            if self._cal.stale(now):  # the app stopped pushing: say so rather than sound sure
                today += " (may be out of date, the Evie app hasn't synced lately)"
        facts = {"now": now.strftime("%a %-d %b %Y, %H:%M"), "calendar_now": self._cal.now_line(now),
                 "calendar_today": today, "calendar_tomorrow": tomorrow, "calendar_week": week,
                 "job": self._runner.status_line()}
        if self._remember and self._remember.facts.recent():
            facts["things_isaac_told_evie"] = " | ".join(self._remember.facts.recent())
        return facts

    def _say(self, text: str) -> str:
        self._mouth.say(text, kind="reply")
        self._last_reply_at = self._clock()
        return text

    def _clip(self, name: str) -> str:
        self._mouth.play_clip(name)
        self._last_reply_at = self._clock()
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
