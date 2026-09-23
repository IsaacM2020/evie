import asyncio
import json
from datetime import datetime, timedelta

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
        self.queue = []

    @property
    def queued(self):
        return list(self.queue)

    def enqueue(self, goal):
        self.queue.append(goal)
        return len(self.queue)

    def drop_next(self):
        return self.queue.pop(0) if self.queue else None

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
        dropped, self.queue = self.queue, []
        return dropped

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
    assert kind == "reply" and "9:00-10:00 Math" in facts["calendar_today"]
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


async def test_deep_job_while_busy_is_queued_not_refused():
    runner = FakeRunner(running="fix the chase bug", busy=True)
    b, p = brain(FakeSwitchboard("act", "deep_job", "deep_job"), runner=runner)
    await b.hear("evie research MIT")
    assert runner.queue == ["research MIT"]
    assert p["mouth"].said == ["I'm on fix the chase bug. I'll do this right after."] and p["mouth"].clips == []


async def test_long_job_says_it_might_take_a_minute():
    from evie.switchboard.decision import Decision

    class LongSB(FakeSwitchboard):
        async def handle(self, ctx):
            self.contexts.append(ctx)
            d = Decision(0.9, "deep_job", 1.0, {"deep_job": 1.0}, 0.9, 0.0, 300.0, 0.0, long_job=0.9)
            return Outcome(ctx, d, Verdict(Action.ACT, "deep_job"))

    b, p = brain(LongSB())
    out = await b.hear("evie research the best IB physics IA topics")
    assert p["mouth"].clips == ["on_it_long"] and out["said"] == ACKS["on_it_long"]


async def test_job_control_queue_status_and_cancel_next():
    runner = FakeRunner(running="x")
    runner.queue = ["research MIT", "clean my desktop"]
    b, p = brain(FakeSwitchboard("act", "job_control", "job_control"), runner=runner, jev=FakeJev("queue_status"))
    await b.hear("evie what's queued")
    assert p["mouth"].said[-1] == "Next up: research MIT, then clean my desktop."
    b._jev = FakeJev("cancel_next")
    await b.hear("evie cancel the next one")
    assert runner.queue == ["clean my desktop"] and p["mouth"].said[-1] == "Dropped research MIT."


async def test_stop_says_when_it_dropped_queued_jobs():
    runner = FakeRunner(running="x")
    runner.queue = ["research MIT"]
    b, p = brain(FakeSwitchboard("act", "job_control", "job_control"), runner=runner, jev=FakeJev("stop"))
    await b.hear("evie stop that")
    assert p["mouth"].said == ["Stopped. I dropped the 1 queued job too."]


async def test_job_control_with_no_job():
    b, p = brain(FakeSwitchboard("act", "job_control", "job_control"))
    await b.hear("evie hows the job going")
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
    sb = FakeSwitchboard("act", "answer", "answer")
    b, p = brain(sb, runner=FakeRunner(running="fix the chase bug"))
    for i in range(5):
        await b.hear(f"line {i}")
    last = sb.contexts[-1]
    assert len(last.recent) == 3 and 'Isaac: "line 3"' in last.recent[-1]
    assert 'Evie: "It\'s 4pm."' in last.recent[-1]
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


class SlowSwitchboard(FakeSwitchboard):
    def __init__(self, *a, log=None, **kw):
        super().__init__(*a, **kw)
        self.log = log

    async def handle(self, ctx):
        self.log.append("jev start")
        await asyncio.sleep(0.05)
        self.log.append("jev done")
        return await super().handle(ctx)


class LoggingTalker(FakeTalker):
    def __init__(self, log):
        super().__init__()
        self.log = log

    async def reply(self, utterance, facts):
        self.log.append("groq start")
        return await super().reply(utterance, facts)


async def test_answer_is_drafted_while_jev_decides():
    log = []
    b, p = brain(SlowSwitchboard("act", "answer", "answer", log=log))
    b._talker = LoggingTalker(log)
    await b.hear("what's on tomorrow")
    assert log.index("groq start") < log.index("jev done")
    assert p["mouth"].said == ["It's 4pm."]


async def test_drafted_answer_is_dropped_when_jev_picks_a_job():
    log = []
    b, p = brain(SlowSwitchboard("act", "deep_job", "deep_job", log=log))
    b._talker = LoggingTalker(log)
    await b.hear("fix the chase bug")
    assert p["mouth"].said == [] and p["mouth"].clips == ["on_it"]


async def test_no_draft_for_speech_not_addressed_to_evie():
    log = []
    b, _ = brain(SlowSwitchboard("ignore", "not for Evie", "not_for_evie", log=log))
    b._talker = LoggingTalker(log)
    await b.hear("mom I've got the dentist", addressed=False)
    assert "groq start" not in log


async def test_answer_facts_include_the_rest_of_the_week():
    from datetime import timedelta
    cal = CalendarStore()
    now = datetime.now(TZ)
    day4 = (now + timedelta(days=4)).replace(hour=11, minute=0)
    cal.update([CalEvent("Physics", day4, day4 + timedelta(hours=1), False, "Physics")], at=now)
    b, p = brain(FakeSwitchboard("act", "answer", "answer"), calendar=cal)
    await b.hear("what's on friday")
    week = p["talker"].calls[0][2]["calendar_week"]
    assert f"{day4:%A}: 11:00-12:00 Physics" in week


class Clock:
    def __init__(self):
        self.t = 500.0

    def __call__(self):
        return self.t


class SeqSwitchboard(FakeSwitchboard):
    """Answers each call from a script of (action, reason, route)."""

    def __init__(self, *script):
        super().__init__()
        self.script = list(script)

    async def handle(self, ctx):
        self.contexts.append(ctx)
        action, reason, route = self.script.pop(0) if self.script else ("act", "answer", "answer")
        return outcome(ctx, action, reason, route)


def brain_c(sb, clock=None, **kw):
    b, p = brain(sb, **kw)
    b._clock = clock or Clock()
    return b, p


class StopMouth(FakeMouth):
    def __init__(self):
        super().__init__()
        self.stops = 0

    def stop(self):
        self.stops += 1


async def test_yes_after_was_that_for_me_runs_the_original():
    sb = SeqSwitchboard(("clarify", "unsure it was for me", "quick_action"), ("act", "answer", "answer"))
    b, p = brain_c(sb)
    await b.hear("whats the time", "isaac", addressed=False)
    out = await b.hear("yeah", "unknown", addressed=False)
    assert sb.contexts[1].utterance == "whats the time" and sb.contexts[1].addressed is True
    assert out["said"] == "It's 4pm."


async def test_no_after_was_that_for_me_stays_quiet():
    sb = SeqSwitchboard(("clarify", "unsure it was for me", "quick_action"))
    b, p = brain_c(sb)
    await b.hear("pause it", "isaac", addressed=False)
    out = await b.hear("no", "isaac", addressed=False)
    assert out["said"] is None and len(sb.contexts) == 1 and p["mouth"].said == []


async def test_answer_to_a_missing_detail_question_is_merged():
    sb = SeqSwitchboard(("clarify", "missing detail", "quick_action"), ("act", "answer", "answer"))
    b, p = brain_c(sb)
    await b.hear("evie play that song")
    await b.hear("espresso", "unknown", addressed=False)
    assert sb.contexts[1].utterance == 'evie play that song. Evie asked "Which song?", Isaac answered "espresso".'
    assert sb.contexts[1].addressed


async def test_pending_question_expires():
    clock = Clock()
    sb = SeqSwitchboard(("clarify", "missing detail", "quick_action"), ("ignore", "not for Evie", "not_for_evie"))
    b, p = brain_c(sb, clock)
    await b.hear("evie play that song")
    clock.t += 16
    await b.hear("espresso", "isaac", addressed=False)
    assert sb.contexts[1].utterance == "espresso" and not sb.contexts[1].addressed


async def test_someone_else_cant_answer_her_question():
    sb = SeqSwitchboard(("clarify", "unsure it was for me", "quick_action"), ("ignore", "not Isaac's voice", "x"))
    b, p = brain_c(sb)
    await b.hear("pause it", "isaac", addressed=False)
    await b.hear("yes", "other", addressed=False)
    assert sb.contexts[1].utterance == "yes"


async def test_starting_with_her_name_is_a_new_request_not_an_answer():
    sb = SeqSwitchboard(("clarify", "missing detail", "quick_action"), ("act", "answer", "answer"))
    b, p = brain_c(sb)
    await b.hear("evie play that song")
    await b.hear("evie what time is it")
    assert sb.contexts[1].utterance == "evie what time is it"


async def test_stop_is_instant_and_skips_jev():
    sb = SeqSwitchboard()
    b, p = brain_c(sb)
    p["mouth"] = b._mouth = StopMouth()
    out = await b.hear("Evie, stop.", "isaac", addressed=False)
    assert b._mouth.stops == 1 and sb.contexts == [] and out["reason"] == "stop"


async def test_stop_while_a_job_runs_and_she_is_quiet_goes_to_job_control():
    sb = SeqSwitchboard(("act", "job_control", "job_control"))
    b, p = brain_c(sb, runner=FakeRunner(running="x"), jev=FakeJev("stop"))
    await b.hear("evie stop")
    assert p["runner"].stopped == 1


async def test_stop_while_she_talks_during_a_job_just_quiets_her():
    sb = SeqSwitchboard()
    b, p = brain_c(sb, runner=FakeRunner(running="x"))
    b._mouth = StopMouth()
    b._mouth.speaking = True
    await b.hear("evie stop")
    assert b._mouth.stops == 1 and p["runner"].stopped == 0 and sb.contexts == []


async def test_stop_from_someone_else_is_ignored():
    sb = SeqSwitchboard(("ignore", "not Isaac's voice", "not_for_evie"))
    b, p = brain_c(sb)
    b._mouth = StopMouth()
    await b.hear("stop", "other", addressed=False)
    assert b._mouth.stops == 0


async def test_followup_window_goes_into_the_context():
    clock = Clock()
    sb = SeqSwitchboard(("act", "answer", "answer"), ("act", "answer", "answer"), ("act", "answer", "answer"))
    b, p = brain_c(sb, clock)
    await b.hear("evie whats on tomorrow")
    clock.t += 4
    await b.hear("and friday", "isaac", addressed=False)
    clock.t += 30
    await b.hear("and saturday", "isaac", addressed=False)
    assert sb.contexts[1].followup_s == pytest.approx(4) and sb.contexts[2].followup_s is None


async def test_shadow_decides_but_does_nothing():
    sb = SeqSwitchboard(("act", "quick_action", "quick_action"))
    b, p = brain_c(sb)
    q = p["bus"].subscribe()
    out = await b.hear("play some lofi", "isaac", addressed=False, shadow=True)
    assert out["shadow"] is True and out["action"] == "act" and out["said"] is None
    assert p["mouth"].said == [] and p["mouth"].clips == [] and p["talker"].calls == []
    evs = [q.get_nowait() for _ in range(q.qsize())]
    assert [e["kind"] for e in evs] == ["shadow"] and evs[0]["would"] == "act · quick_action"


async def test_shadow_never_leaves_a_pending_question():
    sb = SeqSwitchboard(("clarify", "missing detail", "quick_action"), ("act", "answer", "answer"))
    b, p = brain_c(sb)
    await b.hear("play that song", "isaac", addressed=False, shadow=True)
    await b.hear("espresso", "isaac", addressed=False, shadow=True)
    assert sb.contexts[1].utterance == "espresso"


async def test_overheard_speech_that_isnt_for_her_stays_off_the_panel():
    sb = SeqSwitchboard(("ignore", "not for Evie", "not_for_evie"))
    b, p = brain_c(sb)
    q = p["bus"].subscribe()
    await b.hear("mom can you drive me", "isaac", addressed=False)
    kinds = [q.get_nowait()["kind"] for _ in range(q.qsize())]
    assert "heard" not in kinds and "overheard" in kinds


async def test_what_the_mac_is_doing_goes_into_the_context():
    sb = SeqSwitchboard()
    b, p = brain_c(sb)
    b.scene = lambda: {"front_app": "zoom.us", "in_call": True}
    await b.hear("so the answer is four", "isaac", addressed=False)
    assert sb.contexts[0].in_call is True and sb.contexts[0].front_app == "zoom.us"


async def test_overheard_chatter_is_logged_without_its_words(tmp_path):
    log = tmp_path / "turns.jsonl"
    sb = SeqSwitchboard(("ignore", "not for Evie", "not_for_evie"), ("ignore", "not for Evie", "not_for_evie"))
    b, p = brain_c(sb, log=log)
    await b.hear("mom can you drive me", "isaac", addressed=False)
    await b.hear("mom can you drive me", "isaac", addressed=False, shadow=True)
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert all(r["text"] is None for r in rows) and all(r["action"] == "ignore" for r in rows)


class FakeSkills:
    def __init__(self, said="Volume 30."):
        self.said, self.runs = said, []

    async def run(self, skill, text):
        from evie.skills.catalog import Done
        self.runs.append((skill, text))
        return Done(self.said)


def skill_outcome(ctx, skill, conf=0.9):
    d = Decision(0.9, "quick_action", 1.0, {"quick_action": 1.0}, 0.9, 0.0, 300.0, 0.0, skill=skill, skill_conf=conf)
    return Outcome(ctx, d, Verdict(Action.ACT, "quick_action"))


class SkillSwitchboard(FakeSwitchboard):
    def __init__(self, skill, conf=0.9):
        super().__init__()
        self.skill, self.conf = skill, conf

    async def handle(self, ctx):
        self.contexts.append(ctx)
        return skill_outcome(ctx, self.skill, self.conf)


async def test_quick_action_runs_the_skill_jev_picked():
    b, p = brain(SkillSwitchboard("volume"))
    b._skills = sk = FakeSkills()
    out = await b.hear("evie volume 30")
    assert sk.runs == [("volume", "volume 30")] and out["said"] == "Volume 30." and p["mouth"].said == ["Volume 30."]


async def test_unknown_quick_thing_goes_to_claude_code():
    for skill, conf in (("other", 0.9), ("volume", 0.3), (None, 0.0)):
        b, p = brain(SkillSwitchboard(skill, conf))
        b._skills = FakeSkills()
        await b.hear("evie rename my screenshots by date")
        assert p["runner"].started == ["rename my screenshots by date"], skill


async def test_skill_that_isnt_fast_falls_to_claude_code():
    b, p = brain(SkillSwitchboard("open_app"))
    b._skills = FakeSkills(said=None)
    await b.hear("evie open the thing")
    assert p["runner"].started == ["open the thing"]


class FakeRemember:
    def __init__(self, *results):
        from evie.facts import FactStore
        self.results, self.runs = list(results), []
        self.facts = FactStore(None)

    async def run(self, where, text):
        from evie.remember import Remembered
        self.runs.append((where, text))
        return self.results.pop(0) if self.results else Remembered("Added.")


def remember_outcome(ctx, where="event"):
    d = Decision(0.9, "remember", 1.0, {"remember": 1.0}, 0.9, 0.9, 300.0, 0.0, remember_to=where)
    return Outcome(ctx, d, Verdict(Action.ACT, "remember"))


class RememberSwitchboard(FakeSwitchboard):
    async def handle(self, ctx):
        self.contexts.append(ctx)
        return remember_outcome(ctx)


async def test_remember_goes_where_jev_said():
    from evie.remember import Remembered
    b, p = brain(RememberSwitchboard())
    b._remember = rem = FakeRemember(Remembered("Added Dentist, Wednesday at 4pm."))
    out = await b.hear("evie remember the dentist wednesday at 4")
    assert rem.runs == [("event", "remember the dentist wednesday at 4")]
    assert out["said"] == "Added Dentist, Wednesday at 4pm."


async def test_remember_asks_what_time_then_uses_the_answer():
    from evie.remember import Remembered
    b, p = brain_c(RememberSwitchboard())
    b._remember = rem = FakeRemember(Remembered(None, ask="What time?"), Remembered("Added Dentist, Wednesday at 4pm."))
    first = await b.hear("evie i have the dentist wednesday")
    second = await b.hear("4pm", "unknown", addressed=False)
    assert first["said"] == "What time?"
    assert rem.runs[1] == ("event", 'i have the dentist wednesday. Evie asked "What time?", Isaac answered "4pm".')
    assert second["said"].startswith("Added")


async def test_remembered_facts_reach_her_answers():
    b, p = brain()
    b._remember = rem = FakeRemember()
    rem.facts.add("Isaac's locker code is 4129.")
    await b.hear("evie whats my locker code")
    facts = p["talker"].calls[0][2]
    assert "4129" in facts["things_isaac_told_evie"]


async def test_an_unknown_voice_cant_say_yes_for_an_unknown_voice():
    # TV: "Evie play music" (unknown) -> "Was that for me?" -> TV: "yes" (unknown): nothing happens
    sb = SeqSwitchboard(("clarify", "unsure it was for me", "quick_action"), ("ignore", "not for Evie", "x"))
    b, p = brain_c(sb)
    await b.hear("evie play some music", "unknown", addressed=False)
    await b.hear("yes", "unknown", addressed=False)
    assert sb.contexts[1].utterance == "yes" and not sb.contexts[1].addressed


async def test_isaacs_matched_yes_still_confirms_an_unknown_voice():
    sb = SeqSwitchboard(("clarify", "unsure it was for me", "quick_action"), ("act", "answer", "answer"))
    b, p = brain_c(sb)
    await b.hear("evie whats the time", "unknown", addressed=False)
    await b.hear("yes that was me", "isaac", addressed=False)
    assert sb.contexts[1].utterance == "evie whats the time" and sb.contexts[1].addressed


class BrokenRemember(FakeRemember):
    async def run(self, where, text):
        raise ValueError("boom")


async def test_a_broken_remember_still_answers():
    b, p = brain(RememberSwitchboard())
    b._remember = BrokenRemember()
    out = await b.hear("evie remember the dentist")
    assert out["said"] == "Couldn't save that, try again."


async def test_answer_facts_have_a_right_now_line():
    cal = CalendarStore()
    now = datetime.now(TZ)
    cal.update([CalEvent("Deep work", now - timedelta(minutes=5), now + timedelta(minutes=55), False, "Isaac")], at=now)
    b, p = brain(FakeSwitchboard("act", "answer", "answer"), calendar=cal)
    await b.hear("what am I doing right now")
    facts = p["talker"].calls[0][2]
    assert facts["calendar_now"].startswith("Right now: Deep work until")


async def test_answer_facts_warn_when_the_calendar_is_stale():
    cal = CalendarStore()
    cal.update([], at=datetime.now(TZ) - timedelta(hours=1))
    b, p = brain(FakeSwitchboard("act", "answer", "answer"), calendar=cal)
    await b.hear("what's on today")
    assert "may be out of date" in p["talker"].calls[0][2]["calendar_today"]


async def test_stop_calls_off_a_pending_delete_before_anything_else():
    from evie.countdown import Countdown
    cd = Countdown(seconds=5)
    ran = []

    async def delete():
        ran.append(1)

    sb = SeqSwitchboard()
    b, p = brain_c(sb, runner=FakeRunner(running="x"))  # even with a job running and her quiet
    p["mouth"] = b._mouth = StopMouth()
    b._countdown = cd
    cd.start(delete)
    out = await b.hear("stop")
    assert out["reason"] == "cancelled" and not cd.pending and p["runner"].stopped == 0
    assert p["mouth"].said == ["Okay, cancelled."] and sb.contexts == []


async def test_risky_skill_from_the_open_mic_needs_isaacs_voice():
    from evie.switchboard.decision import Decision
    b, p = brain(FakeSwitchboard("act", "quick_action", "quick_action"))

    class Skills:
        ran = []

        async def run(self, skill, text):
            self.ran.append(skill)

    b._skills = Skills()
    d = Decision(0.9, "quick_action", 1.0, {}, 0.9, 0.0, 0, 0, skill="event_delete", skill_conf=0.9)
    said = await b._quick("delete my sax class", d, speaker="unknown", addressed=False)
    assert Skills.ran == [] and "talk key" in said



# -- Phase 3.5: follow-ups survive noise -------------------------------------------------------
class AnswerJev:
    """Jev judging "is this Isaac answering Evie's question?" from a script of probabilities."""

    def __init__(self, *ps):
        self.ps, self.asked = list(ps), []

    async def ask(self, state, questions):
        self.asked.append(state)
        return JevResult({"answers": {"type": "noul", "noul": self.ps.pop(0)}}, 200.0, 0.0)


async def test_a_fragment_before_the_yes_no_longer_eats_the_question():
    # 21:19:33 on 2026-09-23: a 42-char fragment used up "Was that for me?" 2 s before the yes.
    sb = SeqSwitchboard(("clarify", "unsure it was for me", "quick_action"),
                        ("ignore", "not for Evie", "not_for_evie"), ("act", "answer", "answer"))
    b, p = brain_c(sb)
    await b.hear("set a timer for 30 seconds", "isaac", addressed=False)
    await b.hear("oh and the other thing", "isaac", addressed=False)
    out = await b.hear("yes it's for you", "isaac", addressed=False)
    assert sb.contexts[2].utterance == "set a timer for 30 seconds" and sb.contexts[2].addressed
    assert out["action"] == "act"


async def test_chatter_during_a_what_time_question_is_not_taken_as_the_answer():
    sb = SeqSwitchboard(("clarify", "missing detail", "remember"), ("ignore", "not for Evie", "not_for_evie"),
                        ("act", "remember", "remember"))
    b, p = brain_c(sb, jev=AnswerJev(0.05, 0.95))
    await b.hear("evie remember i have the dentist wednesday")
    await b.hear("mom where are my keys", "isaac", addressed=False)
    await b.hear("4pm", "isaac", addressed=False)
    assert sb.contexts[1].utterance == "mom where are my keys"
    assert sb.contexts[2].utterance.startswith("evie remember i have the dentist wednesday. Evie asked")
    assert 'Isaac answered "4pm"' in sb.contexts[2].utterance


async def test_a_new_real_command_clears_the_question():
    sb = SeqSwitchboard(("clarify", "unsure it was for me", "quick_action"), ("act", "answer", "answer"),
                        ("act", "answer", "answer"))
    b, p = brain_c(sb)
    await b.hear("pause", "isaac", addressed=False)
    await b.hear("what's the weather like", "isaac", addressed=True)  # talk key: a new request
    await b.hear("yes", "isaac", addressed=False)
    assert sb.contexts[2].utterance == "yes"


async def test_her_name_first_in_isaacs_voice_marks_it_as_named():
    sb = SeqSwitchboard(("act", "answer", "answer"))
    b, p = brain_c(sb)
    await b.hear("Evie, what files are on my desktop", "isaac", addressed=False)
    assert sb.contexts[0].named is True
    await b.hear("Evie, what time is it", "unknown", addressed=False)
    assert sb.contexts[1].named is False  # only Isaac's matched voice gets the benefit


async def test_answers_see_todays_conversation(tmp_path):
    from evie.memory import Conversation
    b, p = brain(FakeSwitchboard("act", "answer", "answer"))
    b._conv = Conversation(tmp_path)
    await b.hear("what's on friday")
    await b.hear("and what about saturday")
    facts = p["talker"].calls[-1][2]
    assert 'Isaac: "what\'s on friday" / Evie: "It\'s 4pm."' in facts["conversation"]


async def test_overheard_chatter_never_enters_the_conversation(tmp_path):
    from evie.memory import Conversation
    b, p = brain(FakeSwitchboard("ignore", "not for Evie", "not_for_evie"))
    b._conv = Conversation(tmp_path)
    await b.hear("mom can you drive me", "isaac", addressed=False)
    assert b._conv.lines() == []


# -- Phase 3.5 T11/T12: context packs, the big model, filler lines ----------------------------
class PackSwitchboard(FakeSwitchboard):
    def __init__(self, packs=(), hard=0.0):
        super().__init__("act", "answer", "answer")
        self.packs, self.hard = packs, hard

    async def handle(self, ctx):
        self.contexts.append(ctx)
        d = Decision(0.9, "answer", 1.0, {"answer": 1.0}, 0.9, 0.0, 300.0, 0.0, packs=self.packs, hard=self.hard)
        return Outcome(ctx, d, Verdict(Action.ACT, "answer"))


class FakePacks:
    def __init__(self):
        self.asked = []

    async def gather(self, names, text):
        self.asked.append(set(names))
        await asyncio.sleep(0.01)
        return {n: f"{n} facts" for n in names}


class HardTalker(FakeTalker):
    def __init__(self, delay=0.0):
        super().__init__()
        self.delay = delay

    async def reply(self, utterance, facts, hard=False):
        self.calls.append(("reply", utterance, facts, hard))
        await asyncio.sleep(self.delay)
        return "Here's why." if hard else "It's 4pm."


async def test_plain_question_keeps_the_fast_draft():
    b, p = brain(PackSwitchboard())
    b._packs = FakePacks()
    await b.hear("how are you")
    assert b._packs.asked == [] and len(p["talker"].calls) == 1


async def test_jev_and_rails_pick_the_packs_and_the_answer_is_redone_with_them():
    b, p = brain(PackSwitchboard(packs=("projects",)))
    b._packs = FakePacks()
    b._talker = p["talker"] = HardTalker()
    await b.hear("what's on my to do list and how's my igem work going")
    assert b._packs.asked == [{"projects", "tasks"}]
    facts = p["talker"].calls[-1][2]
    assert facts["projects"] == "projects facts" and facts["tasks"] == "tasks facts"


async def test_web_questions_say_let_me_look_that_up_first():
    b, p = brain(PackSwitchboard(packs=("web",)))
    b._packs = FakePacks()
    b._talker = p["talker"] = HardTalker()
    await b.hear("who won the ipl final")
    assert p["mouth"].said[0] == "Let me look that up." and p["mouth"].said[-1] == "It's 4pm."


async def test_hard_questions_go_to_the_big_model_with_a_let_me_think():
    b, p = brain(PackSwitchboard(hard=0.9))
    b._packs = FakePacks()
    b._talker = p["talker"] = HardTalker(delay=0.05)
    b.THINK_AFTER_S = 0.01
    out = await b.hear("why is the derivative of sin cos")
    assert p["talker"].calls[-1][3] is True and out["said"] == "Here's why."
    assert p["mouth"].said == ["Let me think.", "Here's why."]
