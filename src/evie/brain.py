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
from evie.context_packs import named_days, rails
from evie.events import EventBus
from evie.jev import JevError
from evie.jobs import Busy
from evie.skills.catalog import RISK, allowed
from evie.switchboard.context import Context
from evie.switchboard.policy import Action
from evie.voice import ACKS, TURN

log = logging.getLogger("evie.brain")

TURNS_LOG = Path.home() / "Library/Logs/Evie/turns.jsonl"
MAX_TURNS = 3
PENDING_S = 15.0  # how long Evie waits for the answer to a question she asked
FOLLOWUP_S = 10.0
MULTI_AT = 0.7  # Jev's multi_request: this sure it's two separate requests
SPLIT_Q = ('Isaac asked for several separate things in one sentence. Return {"parts": [each request as its own '
           'complete sentence, in the order he said them]}. Keep his words; fill in what "it" or "that" means.')
REPLACE_S = 3.0  # a new request this soon after the last replaces it (see _replace_recent_turn)
COMPUTER_SKILLS = {"computer", "message_send"}  # Phase 3b: done on screen (or by message)
SKILL_CONF_MIN = 0.5  # below this Jev isn't sure which fast skill: Claude Code handles it  # after she answers, a follow-up without her name may still be for her

JOB_OP_Q = {
    "job_op": {
        "type": "choice",
        "instructions": "Isaac said this about the background job Evie is running. What does he want?",
        "criteria": {
            "status": "He wants to know how it's going, what it's doing or how long it'll take",
            "stop": "He wants the job stopped, cancelled or killed",
            "add_instruction": "He's adding something for the job to do, or changing what it should do",
            "queue_status": "He asks what's queued or waiting to be done next",
            "cancel_next": "He wants the next queued job dropped or cancelled, not the one running now",
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
    asked: str = ""  # what she asked ("What time?")


def strip_wake(text: str) -> str:
    """ "Evie, fix the chase bug" -> "fix the chase bug" (the job doesn't need her name)."""
    rest = _WAKE.sub("", text, count=1).strip()
    return rest or text.strip()


class Brain:
    THINK_AFTER_S = 1.2  # the big model says "Let me think." if it hasn't answered by then
    HARD_AT = 0.7
    def __init__(self, sb, talker, mouth, runner, narrator, calendar: CalendarStore, bus: EventBus, jev,
                 turns_log: Path | None = TURNS_LOG, clock: Callable[[], float] = time.monotonic,
                 skills=None, remember=None, countdown=None, conversation=None, packs=None, computer=None):
        self._sb, self._talker, self._mouth = sb, talker, mouth
        self._runner, self._narrator, self._cal = runner, narrator, calendar
        self._bus, self._jev, self._log, self._clock = bus, jev, turns_log, clock
        self._skills, self._remember = skills, remember
        self._countdown = countdown  # a pending "say stop to cancel" (event delete, 3b sends)
        self._conv = conversation  # today's turns with Isaac (evie.memory), for follow-ups
        self._packs = packs  # context packs (evie.context_packs): the knowledge each answer needs
        self._computer = computer  # Phase 3b: operating apps on screen (evie.computer.recipes)
        self._computer_task: asyncio.Task | None = None
        self._turns: deque[str] = deque(maxlen=MAX_TURNS)
        self._pending: Pending | None = None
        self._last_reply_at: float | None = None
        self.scene: Callable[[], dict] = dict  # the app's view of the Mac: front_app, in_call
        self._turn_seq = 0
        self._last_act: tuple[int, float] | None = None  # (turn, when) of the last turn she acted on

    async def hear(self, text: str, speaker: str = "isaac", addressed: bool = True, shadow: bool = False) -> dict:
        """addressed: Isaac held the talk key or typed to Evie, so it's certainly for her.
        addressed=False is the open mic: Jev and the policy decide whether it was for her.
        shadow: open mic trial run. Decide and log what she WOULD do, do nothing."""
        if shadow:
            return await self._shadow(text, speaker)
        self._turn_seq += 1
        TURN.set(self._turn_seq)  # everything said from this turn (and tasks it starts) carries it
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
        waiting = self._pending
        named = not addressed and speaker == "isaac" and bool(_WAKE.match(text))
        out = await self._turn(text, speaker, addressed, named=named)
        # A new real request replaces her question; chatter, echo and fragments don't.
        if self._pending is waiting and out.get("action") == "act":
            self._pending = None
        return out

    def _context(self, text: str, speaker: str, addressed: bool, named: bool = False) -> Context:
        job = self._runner.current
        since = None if self._last_reply_at is None else self._clock() - self._last_reply_at
        scene = self.scene()
        return Context(utterance=text, speaker=speaker, recent=tuple(self._turns),
                       in_call=bool(scene.get("in_call", False)), front_app=str(scene.get("front_app", "")),
                       active_jobs=(job.goal,) if job else (), addressed=addressed,
                       followup_s=since if (not addressed and since is not None and since <= FOLLOWUP_S) else None,
                       named=named)

    def _stop(self, text: str) -> dict:
        self._mouth.stop()
        self._pending = None
        if self._computer_task and not self._computer_task.done():
            self._computer_task.cancel()
        self._bus.publish("heard", text=text)
        self._bus.publish("state", state="working" if self._runner.current else "idle")
        self._write_log({"t": time.time(), "text": text, "action": "act", "reason": "stop", "route": None})
        return {"text": text, "action": "act", "reason": "stop", "route": None, "said": None}

    async def stop_all(self) -> dict:
        """The orb's Stop button: she stops talking, drops any say-stop countdown, abandons screen
        work and stops the background job. Everything, no questions."""
        stopped = ["speech"]
        self._mouth.stop()
        self._pending = None
        if self._countdown is not None and self._countdown.cancel():
            stopped.append("countdown")
        if self._computer_task and not self._computer_task.done():
            self._computer_task.cancel()
            stopped.append("screen")
        job = self._runner.current
        if job:
            await self._runner.stop()
            self._bus.publish("job_done", id=job.id, status="stopped", summary="Stopped.", result="")
            stopped.append("job")
        self._bus.publish("state", state="idle")
        self._write_log({"t": time.time(), "text": None, "action": "act", "reason": "stop button", "route": None})
        return {"stopped": stopped}

    async def _answer_pending(self, text: str, speaker: str) -> dict | None:
        """If Evie just asked something, is this the answer? A yes/no or a real answer is used up;
        anything else (an echo, a fragment, Isaac talking to someone) leaves her question waiting,
        until a new real request replaces it or 15 s pass (2026-09-23: a stray fragment 2 s before
        the "yes" used to eat the question)."""
        p = self._pending
        if p is None:
            return None
        if self._clock() - p.at > PENDING_S or _WAKE.match(text):
            self._pending = None
            return None
        if speaker == "other":
            return None  # not Isaac: keep waiting for him
        words = _norm(text)
        if p.kind == "for_me":
            if p.speaker != "isaac" and speaker != "isaac":
                # She asked because the voice wasn't clearly Isaac's; only Isaac's matched voice
                # can say yes (a TV can't answer "yes" for another TV line).
                return None
            if _YES.match(words):
                self._pending = None
                return await self._turn(p.text, p.speaker, addressed=True)
            if _NO.match(words):
                self._pending = None
                self._write_log({"t": time.time(), "text": text, "action": "ignore", "reason": "not for me"})
                return {"text": text, "action": "ignore", "reason": "not for me", "route": None, "said": None}
            return None  # neither: handle it fresh, the question keeps waiting
        if not await self._answers(p, text):
            return None
        self._pending = None
        merged = f'{p.text}. Evie asked "{p.asked}", Isaac answered "{text}".' if p.asked else f"{p.text}. {text}"
        return await self._turn(merged, p.speaker, addressed=True)

    async def _answers(self, p: Pending, text: str) -> bool:
        """Jev: is this Isaac answering her question, or something else? Unsure means yes."""
        q = {"answers": {"type": "noul", "instructions": (
            f'Evie just asked Isaac: "{p.asked or "a question"}" about his request "{p.text}". Is the latest '
            "speech Isaac answering that question (a time, a name, a choice, a detail), rather than "
            "talking to someone else or saying something unrelated?")}}
        try:
            res = await self._jev.ask(f'Latest speech: "{text}"', q)
            return float(res.answers["answers"]["noul"]) >= 0.5
        except (JevError, KeyError, TypeError, ValueError):
            return True

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

    async def _turn(self, text: str, speaker: str, addressed: bool, named: bool = False) -> dict:
        t0 = time.perf_counter()
        if addressed:
            self._bus.publish("heard", text=text)
            self._bus.publish("state", state="thinking")
        ctx = self._context(text, speaker, addressed, named)
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
        if o.verdict.action != Action.IGNORE:
            self._replace_recent_turn()
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
            if self._conv is not None:
                self._conv.add(text, said, did=route)
                if self._conv.needs_summary() and hasattr(self._talker, "sum_up"):
                    asyncio.create_task(self._sum_up())
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

    def _replace_recent_turn(self) -> None:
        """A new request within REPLACE_S of the last one replaces it (he rephrased, or finished
        the sentence): the old turn's replies that haven't been spoken yet are dropped, so two
        answers never queue up back to back."""
        now, turn = self._clock(), TURN.get()
        last = self._last_act
        if last and last[0] != turn and now - last[1] <= REPLACE_S and hasattr(self._mouth, "drop_turn"):
            self._mouth.drop_turn(last[0])
        self._last_act = (turn, now)

    async def _act(self, verdict, route: str | None, text: str, draft: asyncio.Task | None = None,
                   speaker: str = "isaac", decision=None, addressed: bool = True) -> str | None:
        if verdict.action == Action.IGNORE:
            return None
        if verdict.action == Action.CLARIFY:
            kind = "for_me" if verdict.reason == "unsure it was for me" else "detail"
            pending = self._pending = Pending(kind, text, speaker, self._clock())
            if verdict.reason == "missing detail":
                pending.asked = self._say(await self._talker.clarify(text, "a detail is missing"))
            elif verdict.reason == "unsure what you meant":
                pending.asked = self._say(await self._talker.clarify(
                    text, "it's unclear whether he wants an answer, a job done, or something else"))
            else:
                pending.asked = self._clip("for_me")
            return pending.asked
        if decision and decision.multi >= MULTI_AT and not (route == "quick_action" and decision.skill == "computer"):
            said = await self._one_by_one(text, speaker)
            if said is not None:
                return said
        if route == "answer":
            return await self._answer(text, draft, decision)
        if route == "deep_job":
            return await self._start_job(text, long=bool(decision and decision.long_job >= 0.6))
        if route == "job_control":
            return await self._job_control(text)
        if route == "quick_action" and (self._skills or self._computer):
            return await self._quick(text, decision, speaker, addressed)
        if route == "remember" and self._remember:
            return await self._remember_it(text, decision, speaker)
        return self._clip("not_yet")

    async def _one_by_one(self, text: str, speaker: str) -> str | None:
        """'Pause the music and open WhatsApp': split into the separate requests and do each in
        order, each decided on its own. Screen work with several steps stays one plan instead."""
        try:
            parts = (await self._talker.extract(SPLIT_Q, strip_wake(text)) or {}).get("parts") or []
        except Exception:
            log.exception("couldn't split a two-part request")
            return None
        parts = [str(p).strip() for p in parts if str(p).strip()][:4]
        if len(parts) < 2:
            return None
        said = None
        for part in parts:
            out = await self._turn(part, speaker, addressed=True)
            said = out.get("said") or said
        return said

    async def _answer(self, text: str, draft: asyncio.Task | None, decision) -> str:
        """The draft (started before Jev decided) is used when the answer needs nothing extra.
        Otherwise: gather exactly the packs Jev and the rails picked, and for a hard question use
        the big model. Web lookups and slow thinking get a short line first, never silence."""
        names = set(decision.packs if decision else ()) | rails(text)
        far_day = bool(named_days(text, datetime.now(TZ).date())) and "calendar" in names
        extra = names - {"calendar"} or ({"calendar"} if far_day else set())
        hard = bool(decision and decision.hard >= self.HARD_AT)
        if not extra and not hard and draft is not None:
            return self._say(await draft)
        if draft is not None:
            draft.cancel()
        if "web" in extra:
            self._say("Let me look that up.")
        facts = self._facts()
        if extra and self._packs is not None:
            facts |= await self._packs.gather(extra | ({"calendar"} if "calendar" in names else set()), text)
        if not hard:
            return self._say(await self._talker.reply(text, facts))
        task = asyncio.create_task(self._talker.reply(text, facts, hard=True))
        done, _ = await asyncio.wait({task}, timeout=self.THINK_AFTER_S)
        if not done:
            self._say("Let me think.")
        return self._say(await task)

    async def _remember_it(self, text: str, decision, speaker: str) -> str:
        try:
            r = await self._remember.run(decision.remember_to if decision else None, strip_wake(text))
        except Exception:
            log.exception("remember failed")
            return self._say("Couldn't save that, try again.")
        if r.ask:  # e.g. "What time?": his next sentence is merged in and this runs again
            self._pending = Pending("detail", text, speaker, self._clock(), asked=r.ask)
            return self._say(r.ask)
        return self._say(r.said)

    async def _quick(self, text: str, decision, speaker: str = "isaac", addressed: bool = True) -> str:
        """A fast skill if Jev is sure which one; otherwise Claude Code, the general hands."""
        skill = decision.skill if decision else None
        if skill and not allowed(RISK.get(skill, "unknown"), speaker, addressed):
            return self._say("That one needs your voice. Say it again, or use the talk key.")
        if skill in COMPUTER_SKILLS and self._computer is not None and decision.skill_conf >= SKILL_CONF_MIN:
            return self._start_computer(strip_wake(text), skill)
        if skill and skill != "other" and self._skills and decision.skill_conf >= SKILL_CONF_MIN:
            done = await self._skills.run(skill, strip_wake(text))
            if done.said is not None:
                return self._say(done.said)
        return await self._start_job(text)

    def _start_computer(self, goal: str, skill: str | None = None) -> str:
        """On screen work takes a few seconds: say "On it." now, keep listening, report when done."""
        if self._computer_task and not self._computer_task.done():
            self._computer_task.cancel()
        said = self._clip("on_it")
        self._computer_task = asyncio.create_task(self._run_computer(goal, skill))
        return said

    async def _run_computer(self, goal: str, skill: str | None = None) -> None:
        self._bus.publish("state", state="working")
        try:
            out = await self._computer.run(goal, skill=skill)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("computer task crashed")
            self._say("Something broke doing that on screen.")
            return
        finally:
            self._bus.publish("state", state="working" if self._runner.current else "idle")
        self._write_log({"t": time.time(), "text": goal, "action": "act", "reason": "computer", "route": "computer",
                         "said": out.said, "ok": out.ok, "stuck": out.stuck})
        if out.ask:
            self._pending = Pending("detail", goal, "isaac", self._clock(), asked=out.said)
            self._say(out.said)
        elif out.stuck:
            self._say("That's fiddly on screen, I'll get Claude Code to do it.")
            await self._start_job(f"{goal} (Evie tried this in the app's interface and got stuck. Do it another "
                                  "way, like osascript or Shortcuts; a screenshot only if there's truly no other way.)")
        else:
            self._say(out.said)

    async def _start_job(self, text: str, long: bool = False) -> str:
        running = self._runner.current
        if running:  # it waits its turn (Phase 6's night queue builds on this)
            self._runner.enqueue(strip_wake(text))
            return self._say(f"I'm on {running.goal}. I'll do this right after.")
        said = self._clip("on_it_long" if long else "on_it")
        try:
            job = await self._runner.start(strip_wake(text))
        except Busy as e:
            self._runner.enqueue(strip_wake(text))
            return self._say(f"I'm on {e}. I'll do this right after.")
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
            dropped = await self._runner.stop() or []
            self._bus.publish("job_done", id=job.id, status="stopped", summary="Stopped.", result="")
            if dropped:
                n = len(dropped)
                return self._say(f"Stopped. I dropped the {n} queued job{'s' if n > 1 else ''} too.")
            return self._say("Stopped.")
        if op == "queue_status":
            q = self._runner.queued
            return self._say("Nothing queued after this one." if not q else "Next up: " + ", then ".join(q) + ".")
        if op == "cancel_next":
            gone = self._runner.drop_next()
            return self._say(f"Dropped {gone}." if gone else "Nothing queued.")
        if op == "add_instruction":
            await self._runner.add_instruction(strip_wake(text))
            return self._say("Got it, passing that on.")
        return self._say(self._runner.status_line())

    async def _sum_up(self) -> None:
        try:
            self._conv.set_summary(await self._talker.sum_up(self._conv.summary, self._conv.older()))
        except Exception:
            log.exception("couldn't summarise the conversation")

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
        if self._conv is not None:
            if self._conv.summary:
                facts["conversation_earlier"] = self._conv.summary
            if lines := self._conv.lines():
                facts["conversation"] = " | ".join(lines)
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
