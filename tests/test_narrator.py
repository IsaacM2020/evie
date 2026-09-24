import asyncio

from evie.events import EventBus
from evie.jev import JevError, JevResult
from evie.jobs import Job
from evie.narrator import Narrator
from evie.talk import FALLBACK


class FakeJev:
    def __init__(self, p=0.9, error=None):
        self.p, self.error, self.states = p, error, []

    async def ask(self, state, questions):
        self.states.append(state)
        if self.error:
            raise self.error
        return JevResult({"worth_saying": {"type": "noul", "noul": self.p}}, 200.0, 0.00002)


class FakeTalker:
    def __init__(self, narrate_text=None):
        self.narrate_text = narrate_text

    async def narrate(self, goal, event, last=""):
        return self.narrate_text or f"update: {event}"

    async def summarize(self, goal, result):
        return f"summary: {result}"


class FakeMouth:
    def __init__(self):
        self.said = []

    def say(self, text, kind="reply", ttl_s=None, clip=None):
        self.said.append((kind, text))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(p=0.9, error=None, narrate_text=None):
    jev, mouth, clock, bus = FakeJev(p, error), FakeMouth(), Clock(), EventBus()
    n = Narrator(jev, FakeTalker(narrate_text), mouth, bus, clock=clock)
    return n, jev, mouth, clock, bus


async def test_worth_saying_event_is_narrated_after_the_gap():
    n, jev, mouth, clock, _ = make(p=0.9)
    job = Job(goal="fix the chase bug")
    clock.t = 12
    await n.on_event(job, "Found the bug: off by one")
    assert mouth.said == [("narration", "update: Found the bug: off by one")]
    assert "fix the chase bug" in jev.states[0] and "Found the bug" in jev.states[0]


async def test_below_threshold_is_not_said():
    n, _, mouth, clock, _ = make(p=0.4)
    clock.t = 12
    await n.on_event(Job(goal="x"), "Read model.py")
    assert mouth.said == []


async def test_within_ten_seconds_of_last_line_is_skipped_without_asking_jev():
    n, jev, mouth, clock, _ = make(p=0.9)
    job = Job(goal="x")
    n.start(job)  # "On it" was just said at t=0
    clock.t = 5
    await n.on_event(job, "Found it")
    assert mouth.said == [] and jev.states == []
    clock.t = 11
    await n.on_event(job, "Found it")
    clock.t = 15  # 4s after the last narration
    await n.on_event(job, "Tests pass")
    assert len(mouth.said) == 1


async def test_capped_at_six_per_job():
    n, _, mouth, clock, _ = make(p=0.9)
    job = Job(goal="x")
    for i in range(10):
        clock.t = 11 * (i + 1)
        await n.on_event(job, f"step {i}")
    assert len(mouth.said) == 6


async def test_jev_error_means_silence():
    n, _, mouth, clock, _ = make(error=JevError("down"))
    clock.t = 12
    await n.on_event(Job(goal="x"), "Found it")
    assert mouth.said == []


async def test_groq_fallback_is_not_spoken_as_narration():
    n, _, mouth, clock, _ = make(p=0.9, narrate_text=FALLBACK)
    clock.t = 12
    await n.on_event(Job(goal="x"), "Found it")
    assert mouth.said == []


async def test_every_event_is_published_even_when_silent():
    n, _, _, clock, bus = make(p=0.1)
    q = bus.subscribe()
    job = Job(goal="x")
    clock.t = 12
    await n.on_event(job, "Read model.py")
    ev = q.get_nowait()
    assert ev["kind"] == "job_event" and ev["line"] == "Read model.py" and ev["id"] == job.id


async def test_done_always_speaks_summary_as_reply_and_publishes():
    n, _, mouth, _, bus = make(p=0.0)
    q = bus.subscribe()
    job = Job(goal="fix it", status="done", result="Fixed chase.py")
    await n.on_done(job)
    assert mouth.said == [("reply", "summary: Fixed chase.py")]
    ev = q.get_nowait()
    assert ev["kind"] == "job_done" and ev["status"] == "done" and ev["summary"] == "summary: Fixed chase.py"


async def test_failed_job_summary_says_it_failed():
    n, _, mouth, _, _ = make()
    await n.on_done(Job(goal="fix it", status="failed", result="Claude Code crashed: boom"))
    assert "FAILED" in mouth.said[0][1]


def test_bus_drops_oldest_when_a_slow_subscriber_is_full():
    bus = EventBus(maxsize=3)
    q = bus.subscribe()
    for i in range(5):
        bus.publish("x", n=i)
    assert [q.get_nowait()["n"] for _ in range(3)] == [2, 3, 4]


def test_bus_keeps_recent_history_for_new_subscribers():
    bus = EventBus()
    bus.publish("heard", text="hi")
    assert bus.history()[-1]["text"] == "hi"


async def test_unsubscribe_stops_delivery():
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    bus.publish("x")
    await asyncio.sleep(0)
    assert q.empty()


# -- Phase 3.5 T12: fill the silence ------------------------------------------------------------
async def test_heartbeat_speaks_when_a_job_has_been_quiet():
    jev, mouth, clock, bus = FakeJev(0.1), FakeMouth(), Clock(), EventBus()
    n = Narrator(jev, FakeTalker("Still going, reading your cricket files."), mouth, bus, clock=clock,
                 heartbeat_s=25, tick_s=0.005)
    job = Job(goal="fix the chase bug")
    job.events = ["Read model.py", "Read data.py"]
    n.start(job)
    await asyncio.sleep(0.02)
    assert mouth.said == []  # not quiet long enough
    clock.t = 26
    await asyncio.sleep(0.03)
    assert mouth.said == [("narration", "Still going, reading your cricket files.")]
    clock.t = 40  # only 14 s since that line: no second heartbeat yet
    await asyncio.sleep(0.03)
    assert len(mouth.said) == 1
    job.status = "done"
    await asyncio.sleep(0.02)


async def test_heartbeat_waits_while_isaac_is_busy():
    jev, mouth, clock, bus = FakeJev(0.1), FakeMouth(), Clock(), EventBus()
    busy = [True]
    n = Narrator(jev, FakeTalker("x"), mouth, bus, clock=clock, heartbeat_s=25, tick_s=0.005,
                 can_speak=lambda: not busy[0])
    job = Job(goal="g")
    job.events = ["Read a.py"]
    n.start(job)
    clock.t = 30
    await asyncio.sleep(0.03)
    assert mouth.said == []
    busy[0] = False
    await asyncio.sleep(0.03)
    assert len(mouth.said) == 1
    job.status = "done"


def test_narration_bar_is_lower_now():
    from evie.narrator import NarrationRules
    assert NarrationRules().threshold == 0.45


async def test_routine_steps_never_cost_a_jev_call():
    """2026-09-24: every 'Ran: ls' step was a Jev call scoring ~0.10. Code knows those are routine."""
    n, jev, mouth, clock, bus = make(p=0.9)
    job = Job(goal="check my website")
    q = bus.subscribe()
    for i, line in enumerate(["Read index.html", "Searched for 'vercel'", "Looked for files matching *.md",
                              "Ran: ls /Users/isaac/Elemental", "Ran: find ~ -maxdepth 3 -iname x",
                              "Ran: cat package.json", "Ran: git status", "Opened https://x.com"]):
        clock.t = 20 * (i + 1)
        await n.on_event(job, line)
    assert jev.states == [] and mouth.said == []
    assert q.qsize() == 8  # the panel still shows every step


async def test_real_work_still_goes_to_jev():
    n, jev, _, clock, _ = make(p=0.9)
    job = Job(goal="fix the bug")
    for i, line in enumerate(["Ran: uv run pytest -q", "Edited model.py", "Found it: the date parse is off by one"]):
        clock.t = 20 * (i + 1)
        await n.on_event(job, line)
    assert len(jev.states) == 3


async def test_notices_about_memory_tools_are_never_spoken():
    """A job's first message was the claude-mem outage notice from a hook; she read 8 s of it out."""
    n, jev, mouth, clock, _ = make(p=0.9)
    clock.t = 20
    await n.on_event(Job(goal="x"), "I need to flag something first: the memory system (claude-mem) hit an outage — q")
    assert jev.states == [] and mouth.said == []


async def test_heartbeat_with_a_plan_says_the_real_step_without_any_model():
    jev, mouth, clock, bus = FakeJev(0.1), FakeMouth(), Clock(), EventBus()
    n = Narrator(jev, FakeTalker("should not be used"), mouth, bus, clock=clock, heartbeat_s=30, tick_s=0.005)
    job = Job(goal="fix the deploy")
    job.events = ["Read log.txt"]
    job.todos = [{"content": "a", "status": "completed", "activeForm": "Reading the log"},
                 {"content": "b", "status": "in_progress", "activeForm": "Testing the fix"},
                 {"content": "c", "status": "pending", "activeForm": "Pushing it"}]
    n.start(job)
    clock.t = 31
    await asyncio.sleep(0.03)
    assert mouth.said == [("narration", "Still going. Step 2 of 3, testing the fix.")]
    job.status = "done"


async def test_step_changes_update_the_orb():
    n, jev, mouth, clock, bus = make(p=0.1)
    q = bus.subscribe()
    job = Job(goal="g")
    job.todos = [{"content": "a", "status": "in_progress", "activeForm": "Reading the log"},
                 {"content": "b", "status": "pending", "activeForm": "Fixing it"}]
    clock.t = 20
    await n.on_event(job, "Step 1 of 2: Reading the log")
    kinds = {}
    while not q.empty():
        e = q.get_nowait()
        kinds[e["kind"]] = e
    assert kinds["job_progress"]["done"] == 0 and kinds["job_progress"]["total"] == 2
    assert kinds["job_progress"]["step"] == "Reading the log"


async def test_updates_are_written_knowing_what_she_said_last():
    n, jev, mouth, clock, bus = make(p=0.9)
    seen = []

    async def narrate(goal, event, last=""):
        seen.append(last)
        return f"update {len(seen)}"

    n._talker.narrate = narrate
    job = Job(goal="g")
    clock.t = 20
    await n.on_event(job, "Found the bug")
    clock.t = 40
    await n.on_event(job, "Fixed it")
    assert seen == ["", "update 1"]


async def test_a_finished_job_waits_when_he_is_busy():
    n, _, mouth, _, bus = make(p=0.0)
    q = bus.subscribe()
    held = []
    n.defer = lambda goal, text: held.append((goal, text)) or True
    await n.on_done(Job(goal="fix it", status="done", result="Fixed chase.py"))
    assert mouth.said == [] and held == [("fix it", "summary: Fixed chase.py")]
    assert q.get_nowait()["kind"] == "job_done"  # the orb still knows
