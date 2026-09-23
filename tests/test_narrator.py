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

    async def narrate(self, goal, event):
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
