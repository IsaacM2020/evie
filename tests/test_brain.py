import json
from datetime import datetime

import pytest

from evie.brain import Brain, strip_wake
from evie.calendar_store import TZ, CalendarStore, CalEvent
from evie.events import EventBus
from evie.jev import JevError, JevResult
from evie.jobs import Busy, Job
from evie.switchboard import Outcome
from evie.switchboard.context import Context
from evie.switchboard.decision import Decision
from evie.switchboard.policy import Action, Verdict
from evie.voice import ACKS


def outcome(ctx, action, reason, route="quick_action"):
    d = Decision(0.9, route, 1.0, {route: 1.0}, 0.9, 0.0, 300.0, 0.00002)
    return Outcome(ctx, d, Verdict(Action(action), reason))


class FakeSwitchboard:
    def __init__(self, action="act", reason="answer", route="answer", noise=False):
        self.action, self.reason, self.route, self.noise = action, reason, route, noise
        self.contexts: list[Context] = []

    async def handle(self, ctx):
        self.contexts.append(ctx)
        if self.noise:
            return Outcome(ctx, None, Verdict(Action.IGNORE, "noise"))
        return outcome(ctx, self.action, self.reason, self.route)


class FakeTalker:
    def __init__(self):
        self.calls = []

    async def reply(self, utterance, facts):
        self.calls.append(("reply", utterance, facts))
        return "It's 4pm."

    async def clarify(self, utterance, reason):
        self.calls.append(("clarify", utterance, reason))
        return "Which song?"


class FakeMouth:
    def __init__(self):
        self.said, self.clips = [], []

    def say(self, text, kind="reply", ttl_s=None, clip=None):
        self.said.append(text)

    def play_clip(self, name):
        self.clips.append(name)


class FakeRunner:
    def __init__(self, running=None, busy=False):
        self.job = Job(goal=running) if running else None
        self.busy = busy
        self.started, self.stopped, self.instructions = [], 0, []

    @property
    def current(self):
        return self.job

    async def start(self, goal):
        if self.busy:
            raise Busy(self.job.goal)
        self.started.append(goal)
        self.job = Job(goal=goal)
        return self.job

    async def stop(self):
        self.stopped += 1
        self.job = None

    async def add_instruction(self, text):
        self.instructions.append(text)
        return True

    def status_line(self):
        return f"Working on: {self.job.goal} (2 min, last: Ran: pytest)" if self.job else "Nothing running right now."


class FakeNarrator:
    def __init__(self):
        self.started = []

    def start(self, job):
        self.started.append(job.goal)


class FakeJev:
    def __init__(self, op="status", error=None):
        self.op, self.error, self.calls = op, error, 0

    async def ask(self, state, questions):
        self.calls += 1
        if self.error:
            raise self.error
        return JevResult({"job_op": {"type": "choice", "choice": self.op, "probabilities": {self.op: 1.0},
                                     "confidence": 1.0}}, 200.0, 0.00002)


def brain(sb=None, runner=None, jev=None, calendar=None, log=None):
    parts = dict(sb=sb or FakeSwitchboard(), talker=FakeTalker(), mouth=FakeMouth(),
                 runner=runner or FakeRunner(), narrator=FakeNarrator(), bus=EventBus(), jev=jev or FakeJev())
    b = Brain(parts["sb"], parts["talker"], parts["mouth"], parts["runner"], parts["narrator"],
              calendar or CalendarStore(), parts["bus"], parts["jev"], turns_log=log)
    return b, parts


async def test_ignore_says_nothing_but_publishes():
    b, p = brain(FakeSwitchboard("ignore", "not for Evie", "not_for_evie"))
    q = p["bus"].subscribe()
    out = await b.hear("mom ive got the dentist on wednesday")
    assert out["action"] == "ignore" and out["said"] is None
    assert p["mouth"].said == [] and p["mouth"].clips == []
    kinds = [q.get_nowait()["kind"] for _ in range(q.qsize())]
    assert "heard" in kinds and "verdict" in kinds


async def test_noise_never_reaches_talker_or_mouth():
    b, p = brain(FakeSwitchboard(noise=True))
    out = await b.hear("thank you.")
    assert out["reason"] == "noise" and p["talker"].calls == [] and p["mouth"].said == []


async def test_unsure_plays_was_that_for_me():
    b, p = brain(FakeSwitchboard("clarify", "unsure it was for me", "quick_action"))
    out = await b.hear("pause")
    assert p["mouth"].clips == ["for_me"] and out["said"] == ACKS["for_me"]


async def test_missing_detail_asks_a_groq_question():
    b, p = brain(FakeSwitchboard("clarify", "missing detail", "quick_action"))
    await b.hear("evie play that song")
    assert p["talker"].calls[0][0] == "clarify" and p["mouth"].said == ["Which song?"]


async def test_answer_uses_time_calendar_and_job_facts():
    cal = CalendarStore()
    now = datetime.now(TZ)
    cal.update([CalEvent("Math", now.replace(hour=9, minute=0), now.replace(hour=10, minute=0), False, "Math")], at=now)
    b, p = brain(FakeSwitchboard("act", "answer", "answer"), runner=FakeRunner(running="fix the chase bug"),
                 calendar=cal)
    await b.hear("what's on today")
    kind, _, facts = p["talker"].calls[0]
    assert kind == "reply" and "9:00 Math" in facts["calendar_today"]
    assert facts["job"].startswith("Working on: fix the chase bug") and facts["now"]
    assert p["mouth"].said == ["It's 4pm."]


async def test_answer_without_calendar_sync_says_so_in_facts():
    b, p = brain(FakeSwitchboard("act", "answer", "answer"))
    await b.hear("what's on tomorrow")
    facts = p["talker"].calls[0][2]
    assert "not connected" in facts["calendar_today"]


async def test_deep_job_says_on_it_then_starts_job():
    b, p = brain(FakeSwitchboard("act", "deep_job", "deep_job"))
    out = await b.hear("Evie, fix the chase bug in my cricket model")
    assert p["mouth"].clips == ["on_it"]
    assert p["runner"].started == ["fix the chase bug in my cricket model"]
    assert p["narrator"].started == ["fix the chase bug in my cricket model"]
    assert out["said"] == ACKS["on_it"]


async def test_deep_job_while_busy_refuses():
    runner = FakeRunner(running="fix the chase bug", busy=True)
    b, p = brain(FakeSwitchboard("act", "deep_job", "deep_job"), runner=runner)
    await b.hear("evie research MIT")
    assert p["mouth"].said == ["Still on fix the chase bug. Say stop first."] and p["mouth"].clips == []


async def test_job_control_with_no_job():
    b, p = brain(FakeSwitchboard("act", "job_control", "job_control"))
    await b.hear("evie stop")
    assert p["mouth"].said == ["Nothing running right now."] and p["jev"].calls == 0


async def test_job_control_stop():
    b, p = brain(FakeSwitchboard("act", "job_control", "job_control"), runner=FakeRunner(running="x"),
                 jev=FakeJev("stop"))
    await b.hear("evie stop that")
    assert p["runner"].stopped == 1 and p["mouth"].said == ["Stopped."]


async def test_job_control_status():
    b, p = brain(FakeSwitchboard("act", "job_control", "job_control"), runner=FakeRunner(running="x"),
                 jev=FakeJev("status"))
    await b.hear("how's it going")
    assert p["mouth"].said == ["Working on: x (2 min, last: Ran: pytest)"]


async def test_job_control_add_instruction():
    b, p = brain(FakeSwitchboard("act", "job_control", "job_control"), runner=FakeRunner(running="x"),
                 jev=FakeJev("add_instruction"))
    await b.hear("evie also add a test for it")
    assert p["runner"].instructions == ["also add a test for it"]
    assert p["mouth"].said == ["Got it, passing that on."]


async def test_job_control_jev_down_falls_back_to_status_never_stops():
    b, p = brain(FakeSwitchboard("act", "job_control", "job_control"), runner=FakeRunner(running="x"),
                 jev=FakeJev(error=JevError("down")))
    await b.hear("evie stop")
    assert p["runner"].stopped == 0 and p["mouth"].said[0].startswith("Working on: x")


async def test_quick_action_and_remember_are_not_yet():
    for route in ("quick_action", "remember"):
        b, p = brain(FakeSwitchboard("act", route, route))
        await b.hear("evie play some lofi")
        assert p["mouth"].clips == ["not_yet"]


async def test_recent_keeps_last_three_turns_and_passes_jobs():
    sb = FakeSwitchboard("clarify", "missing detail", "quick_action")
    b, p = brain(sb, runner=FakeRunner(running="fix the chase bug"))
    for i in range(5):
        await b.hear(f"line {i}")
    last = sb.contexts[-1]
    assert len(last.recent) == 3 and 'Isaac: "line 3"' in last.recent[-1]
    assert 'Evie: "Which song?"' in last.recent[-1]
    assert last.active_jobs == ("fix the chase bug",)


async def test_turn_timing_is_logged(tmp_path):
    log = tmp_path / "turns.jsonl"
    b, _ = brain(FakeSwitchboard("act", "deep_job", "deep_job"), log=log)
    await b.hear("evie fix it")
    row = json.loads(log.read_text().splitlines()[-1])
    assert row["route"] == "deep_job" and row["action"] == "act"
    assert row["ms_to_verdict"] >= 0 and row["ms_to_speech_queued"] >= row["ms_to_verdict"]


def test_strip_wake():
    assert strip_wake("Evie, fix the chase bug") == "fix the chase bug"
    assert strip_wake("hey eve fix it") == "fix it"
    assert strip_wake("fix the chase bug") == "fix the chase bug"
    assert strip_wake("Evie") == "Evie"


@pytest.mark.live
@pytest.mark.parametrize("said,op", [
    ("evie stop that", "stop"), ("evie cancel the job", "stop"),
    ("evie how's it going", "status"), ("evie what are you doing right now", "status"),
    ("evie also add a test for it", "add_instruction"), ("evie use the new dataset instead", "add_instruction"),
])
async def test_live_job_op(said, op):
    from evie.brain import JOB_OP_Q
    from evie.config import load_settings
    from evie.jev import JevClient
    jev = JevClient(load_settings())
    res = await jev.ask(f'Evie is working on: fix the chase bug in my cricket model\nIsaac just said: "{said}"',
                        JOB_OP_Q)
    await jev.aclose()
    assert res.answers["job_op"]["choice"] == op


class PolicyPickedSwitchboard(FakeSwitchboard):
    """Jev's top route was not_for_evie, but the policy (addressed) picked deep_job."""

    async def handle(self, ctx):
        self.contexts.append(ctx)
        d = Decision(0.3, "not_for_evie", 0.5, {"not_for_evie": 0.5, "deep_job": 0.45}, 0.9, 0.0, 300.0, 0.0)
        return Outcome(ctx, d, Verdict(Action.ACT, "deep_job"))


async def test_act_uses_the_route_the_policy_picked():
    b, p = brain(PolicyPickedSwitchboard())
    out = await b.hear("please edit the cricket files")
    assert p["runner"].started == ["please edit the cricket files"] and out["route"] == "deep_job"


async def test_voice_and_typed_input_count_as_addressed():
    sb = FakeSwitchboard("act", "answer", "answer")
    b, _ = brain(sb)
    await b.hear("hows the igem website looking")
    await b.hear("overheard thing", addressed=False)
    assert [c.addressed for c in sb.contexts] == [True, False]


async def test_unsure_what_you_meant_asks_a_real_question():
    b, p = brain(FakeSwitchboard("clarify", "unsure what you meant", "answer"))
    await b.hear("the igem thing")
    assert p["talker"].calls[0][0] == "clarify" and p["mouth"].clips == []
